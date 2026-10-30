// Runs the bootstrap scripts against stub commands on PATH (no Azure, GitHub or
// Kubernetes access). Stubs append every call to $STUB_LOG as "<tool> <args>" and keep
// state as files in $STUB_STATE.
import { spawnSync } from "node:child_process";
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
                'PAT_REPOS="mmkdigitalio/hackathon-platform-team mmkdigitalio/hackathon-platform-affine"',
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

// GitHub REST stub for curl: status per "<METHOD> <path>"; $CURL_DENY lists
// "<METHOD> <repo-relative path prefix>@<repo>" entries answered with 403.
export const curlStub = `
method=GET; url=""; prev=""
for a in "$@"; do [[ "$prev" == "-X" ]] && method="$a"; [[ "$a" == https://* ]] && url="$a"; prev="$a"; done
config="$(cat)"
[[ "$config" == *'Authorization: Bearer '* ]] || { printf 401; exit 0; }
token="\${config#*Bearer }"; token="\${token%%\\"*}"
[[ "$token" == invalid* || "$token" == github_pat_invalid* ]] && { printf 401; exit 0; }
echo "$method $url" >>"$STUB_STATE/curl-requests"
path="\${url#https://api.github.com/repos/}"
owner="\${path%%/*}"; rest="\${path#*/}"; name="\${rest%%[/?]*}"; repo="$owner/$name"
sub="\${rest#"$name"}"; sub="\${sub%%\\?*}"
for deny in \${CURL_DENY:-}; do
  [[ "$deny" == "$method:$sub@$repo" || "$deny" == "$method:*@$repo" ]] && { printf 403; exit 0; }
done
case "$method:$sub" in
  GET:*) printf 200 ;;
  POST:/actions/workflows/0/dispatches) printf 404 ;;
  POST:*) printf 422 ;;
esac
`;
