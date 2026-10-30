// Runs the bootstrap scripts against stub commands on PATH (no Azure, GitHub or
// Kubernetes access). Stubs append every call to $STUB_LOG as "<tool> <args>" and keep
// state as files in $STUB_STATE.
import { spawnSync } from "node:child_process";
import { generateKeyPairSync } from "node:crypto";
import { chmodSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

export const scriptsDir = new URL("../../scripts/bootstrap/", import.meta.url).pathname;

export function bash4() {
    for (const candidate of ["/opt/homebrew/bin/bash", "/usr/local/bin/bash", "/bin/bash", "/usr/bin/bash"]) {
        const probe = spawnSync(candidate, ["-c", "(( BASH_VERSINFO[0] > 4 || (BASH_VERSINFO[0] == 4 && BASH_VERSINFO[1] >= 4) ))"]);
        if (probe.status === 0) return candidate;
    }
    return undefined;
}

export const skipWithoutBash = !bash4() && "needs bash >= 4.4";

// Common stub prologue: log the call, return the value of a flag with `arg --flag "$@"`.
const prologue = (tool) => `#!/usr/bin/env bash
s="$STUB_STATE"; echo "${tool} $*" >>"$STUB_LOG"
arg() { local want="$1"; shift; local prev=""; for a in "$@"; do [[ "$prev" == "$want" ]] && { echo "$a"; return; }; prev="$a"; done; }
`;

export function sandbox(stubs, { envFile } = {}) {
    const dir = mkdtempSync(join(tmpdir(), "bootstrap-scripts-"));
    const bin = join(dir, "bin");
    const state = join(dir, "state");
    mkdirSync(bin);
    mkdirSync(state);
    for (const [name, body] of Object.entries(stubs)) {
        writeFileSync(join(bin, name), prologue(name) + body);
        chmodSync(join(bin, name), 0o755);
    }
    const config = join(dir, "staging.env");
    writeFileSync(
        config,
        envFile ??
            [
                "SUBSCRIPTION_ID=sub-1",
                "LOCATION=germanywestcentral",
                "ENVIRONMENT=staging",
                "GITHUB_REPO=mmkdigitalio/hackathon-platform-team",
                "GITHUB_APP_ID=5542094",
                "GITHUB_APP_INSTALLATION_ID=176901698",
                'AGENT_REPOS="mmkdigitalio/hackathon-platform-team mmkdigitalio/hackathon-platform-affine"',
                "STATE_STORAGE_ACCOUNT=stmmkstatetest",
            ].join("\n"),
    );
    const log = join(dir, "calls.log");
    return {
        dir,
        state,
        config,
        touch: (...files) => files.forEach((file) => writeFileSync(join(state, file), "")),
        run(script, args = [], env = {}, input = undefined) {
            writeFileSync(log, "");
            const result = spawnSync(bash4(), [join(scriptsDir, script), ...args], {
                encoding: "utf8",
                input,
                env: {
                    PATH: `${bin}:${process.env.PATH}`,
                    HOME: dir,
                    STUB_STATE: state,
                    STUB_LOG: log,
                    ...env,
                },
            });
            const calls = readFileSync(log, "utf8").trim().split("\n").filter(Boolean);
            return { ...result, calls };
        },
        read: (file) => readFileSync(join(state, file), "utf8"),
    };
}

// Throwaway RSA key pair of a GitHub App, written as PEM files into a new directory.
export function appKeys() {
    const dir = mkdtempSync(join(tmpdir(), "github-app-"));
    const { privateKey, publicKey } = generateKeyPairSync("rsa", {
        modulusLength: 2048,
        publicKeyEncoding: { type: "spki", format: "pem" },
        privateKeyEncoding: { type: "pkcs8", format: "pem" },
    });
    const privateKeyFile = join(dir, "app.pem");
    const publicKeyFile = join(dir, "app.pub.pem");
    writeFileSync(privateKeyFile, privateKey, { mode: 0o600 });
    writeFileSync(publicKeyFile, publicKey);
    return { privateKey, privateKeyFile, publicKeyFile };
}

// GitHub REST stub for curl: status per "<METHOD> <path>"; $CURL_DENY lists
// "<METHOD> <repo-relative path prefix>@<repo>" entries answered with 403. GitHub App
// calls need a JWT of app 5542094 that verifies against $APP_PUBLIC_KEY, is already valid
// and expires within 10 minutes; like GitHub, other repository calls reject a JWT.
// Repositories in $APP_MISSING have no installation and "<permission>@<repo>" entries in
// $APP_READ_ONLY are granted read only. Calls with -w '\n%{http_code}' get the body, a
// newline and the status.
export const curlStub = `
method=GET; url=""; prev=""; with_body=0
for a in "$@"; do
  [[ "$prev" == "-X" ]] && method="$a"; [[ "$a" == https://* ]] && url="$a"
  [[ "$prev" == "-w" && "$a" == '\\n%{http_code}' ]] && with_body=1; prev="$a"
done
reply() { if (( with_body )); then printf '%s\\n%s' "$2" "$1"; else printf '%s' "$1"; fi; exit 0; }
config="$(cat)"
[[ "$config" == *'Authorization: Bearer '* ]] || reply 401 '{}'
token="\${config#*Bearer }"; token="\${token%%\\"*}"
[[ "$token" == invalid* || "$token" == github_pat_invalid* ]] && reply 401 '{}'
echo "$method $url" >>"$STUB_STATE/curl-requests"
jwt=0
if [[ "$token" == eyJ* ]]; then
  b64() { local b; b="$(tr '_-' '/+')"; while (( \${#b} % 4 )); do b="$b="; done; printf '%s' "$b" | openssl base64 -d -A; }
  printf '%s' "\${token##*.}" | b64 >"$STUB_STATE/jwt-signature"
  printf '%s' "\${token%.*}" | openssl dgst -sha256 -verify "\${APP_PUBLIC_KEY:-/nonexistent}" \\
    -signature "$STUB_STATE/jwt-signature" >/dev/null 2>&1 || reply 401 '{}'
  claims="\${token#*.}"; claims="$(printf '%s' "\${claims%%.*}" | b64)"
  now="$(date +%s)"
  jq -e --argjson now "$now" '.iss == "5542094" and .iat <= $now and .exp > $now and .exp - $now <= 600' \\
    <<<"$claims" >/dev/null 2>&1 || reply 401 '{"message":"bad JWT claims"}'
  jwt=1
fi
if [[ "$method:$url" == POST:https://api.github.com/app/installations/176901698/access_tokens ]]; then
  (( jwt )) || reply 401 '{}'
  reply 201 '{"token":"ghs_installation_token"}'
fi
path="\${url#https://api.github.com/repos/}"
owner="\${path%%/*}"; rest="\${path#*/}"; name="\${rest%%[/?]*}"; repo="$owner/$name"
sub="\${rest#"$name"}"; sub="\${sub%%\\?*}"
if [[ "$sub" == /installation ]]; then
  (( jwt )) || reply 401 '{}'
  [[ " \${APP_MISSING:-} " == *" $repo "* ]] && reply 404 '{"message":"Not Found"}'
  permissions=""
  for permission in issues pull_requests contents actions; do
    access=write; [[ " \${APP_READ_ONLY:-} " == *" $permission@$repo "* ]] && access=read
    permissions+="\\"$permission\\":\\"$access\\","
  done
  reply 200 "{\\"id\\":176901698,\\"permissions\\":{\${permissions%,}}}"
fi
(( jwt )) && reply 401 '{"message":"A JSON web token could not be decoded"}'
for deny in \${CURL_DENY:-}; do
  [[ "$deny" == "$method:$sub@$repo" || "$deny" == "$method:*@$repo" ]] && reply 403 '{}'
done
case "$method:$sub" in
  GET:*) reply 200 '{}' ;;
  POST:/actions/workflows/0/dispatches) reply 404 '{}' ;;
  POST:*) reply 422 '{}' ;;
esac
`;
