// preflight.sh and bootstrap.sh against stateful az/gh/curl stubs: rights are checked
// before anything is created, every step only creates what is missing, and secret values
// never reach a command line.
import assert from "node:assert/strict";
import { join } from "node:path";
import { test } from "node:test";
import { appKeys, curlStub, sandbox, skipWithoutBash as skip } from "./helpers/script-stubs.mjs";

const stateKey = "STATE-ACCOUNT-KEY-VALUE";
const pat = "github_pat_agent_token_value";
const app = appKeys();
const withApp = { GITHUB_APP_PRIVATE_KEY_FILE: app.privateKeyFile, APP_PUBLIC_KEY: app.publicKeyFile };

const azStub = `
case "$*" in
  "account show") exit 0 ;;
  "account show --subscription sub-1 --query id -o tsv") echo sub-1 ;;
  "account set --subscription sub-1") ;;
  "rest --method get --url https://management.azure.com/subscriptions/sub-1/providers/Microsoft.Authorization/permissions"*)
    if [[ -n "\${NOT_CONTRIBUTOR:-}" ]]; then echo '*/read'; else printf '*\\n*/read\\n'; fi ;;
  "aks get-versions"*) echo 1.34.0 ;;
  "vm list-skus --location germanywestcentral --size "*) printf '%s' "\${SKUS_JSON:-[]}" ;;
  "vm list-usage --location germanywestcentral -o json") printf '%s' "\${USAGE_JSON:-[]}" ;;
  "postgres flexible-server list-skus --location germanywestcentral --query [0].reason -o tsv") printf '%s\n' "\${PG_REASON:-}" ;;
  "provider show"*) echo Registered ;;
  "group exists"*) [[ -f "$s/rg" ]] && echo true || echo false ;;
  "group create"*) touch "$s/rg" ;;
  "storage account list"*) [[ -f "$s/account" ]] && echo 1 || echo 0 ;;
  "storage account create"*) touch "$s/account" ;;
  "storage account blob-service-properties update"*) ;;
  "storage account keys list"*) echo "${stateKey}" ;;
  "storage container create"*) [[ "$AZURE_STORAGE_KEY" == "${stateKey}" ]] || exit 1; touch "$s/container" ;;
  *) echo "unexpected az call: $*" >&2; exit 99 ;;
esac
`;

const ghStub = `
case "$*" in
  "auth status") exit 0 ;;
  "api repos/mmkdigitalio/hackathon-platform-team --jq .permissions.admin") [[ -n "\${NOT_ADMIN:-}" ]] && echo false || echo true ;;
  "secret list --repo mmkdigitalio/hackathon-platform-team --json name --jq .[].name")
    [[ -n "\${FAIL_SECRET_LIST:-}" ]] && exit 1
    for f in "$s"/secret-*; do [[ -e "$f" ]] && basename "$f" | sed 's/^secret-//'; done; exit 0 ;;
  "secret set "*" --repo mmkdigitalio/hackathon-platform-team")
    value="$(cat; printf x)"; value="\${value%x}"
    [[ -n "$value" && "$value" != *$'\\n' ]] || exit 3
    printf '%s' "$value" >"$s/secret-$3" ;;
  *) echo "unexpected gh call: $*" >&2; exit 99 ;;
esac
`;

const newSandbox = () => sandbox({ az: azStub, gh: ghStub, curl: curlStub });
const created = (calls) => calls.filter((call) => / create|secret set|provider register/.test(call));

// ---- preflight ----

test("preflight passes with Contributor, repository admin and a complete GitHub App installation, creating nothing", { skip }, () => {
    const box = newSandbox();
    const result = box.run("preflight.sh", [box.config], withApp);
    assert.equal(result.status, 0, result.stderr);
    assert.match(result.stderr, /OK +Contributor \(or higher\)/);
    assert.match(result.stderr, /OK +GitHub App actions \(write\) on mmkdigitalio\/hackathon-platform-affine/);
    assert.match(result.stderr, /Preflight passed/);
    assert.deepEqual(created(result.calls), []);
    assert.ok(box.read("curl-requests").split("\n").filter(Boolean).every((call) => call.startsWith("GET ")), "only reads");
    assert.ok(!result.calls.some((call) => call.includes("PRIVATE KEY")), "the key stays off the command line");
});

test("spec scenario: a GitHub App without Actions write stops the preflight and names the right", { skip }, () => {
    const box = newSandbox();
    const result = box.run("preflight.sh", [box.config], {
        ...withApp,
        APP_READ_ONLY: "actions@mmkdigitalio/hackathon-platform-affine",
    });
    assert.equal(result.status, 1);
    assert.match(result.stderr, /- GitHub App permission actions \(write\) on mmkdigitalio\/hackathon-platform-affine \(granted: read\)/);
    assert.doesNotMatch(result.stderr, /MISSING.*hackathon-platform-team/);
});

test("a repository without the app installation is reported by name", { skip }, () => {
    const box = newSandbox();
    const result = box.run("preflight.sh", [box.config], { ...withApp, APP_MISSING: "mmkdigitalio/hackathon-platform-affine" });
    assert.equal(result.status, 1);
    assert.match(result.stderr, /- GitHub App 5542094 installed on mmkdigitalio\/hackathon-platform-affine/);
    assert.match(result.stderr, /OK +GitHub App issues \(write\) on mmkdigitalio\/hackathon-platform-team/);
});

test("a key of another app and an unreadable key file are reported", { skip }, () => {
    const box = newSandbox();
    const other = appKeys();
    const wrong = box.run("preflight.sh", [box.config], { ...withApp, GITHUB_APP_PRIVATE_KEY_FILE: other.privateKeyFile });
    assert.equal(wrong.status, 1);
    assert.match(wrong.stderr, /- GitHub App 5542094 installed on mmkdigitalio\/hackathon-platform-team \(or the key belongs to another app\)/);
    const missing = box.run("preflight.sh", [box.config], { ...withApp, GITHUB_APP_PRIVATE_KEY_FILE: join(box.dir, "missing.pem") });
    assert.equal(missing.status, 1);
    assert.match(missing.stderr, /- GitHub App private key .*missing\.pem \(no readable PEM private key\)/);
});

test("missing Contributor and missing repository admin are both reported", { skip }, () => {
    const box = newSandbox();
    const result = box.run("preflight.sh", [box.config], { ...withApp, NOT_CONTRIBUTOR: "1", NOT_ADMIN: "1" });
    assert.equal(result.status, 1);
    assert.match(result.stderr, /- Contributor on subscription sub-1/);
    assert.match(result.stderr, /- Admin on mmkdigitalio\/hackathon-platform-team/);
});

test("without GITHUB_APP_PRIVATE_KEY_FILE the GitHub App checks are skipped with a notice", { skip }, () => {
    const box = newSandbox();
    const result = box.run("preflight.sh", [box.config]);
    assert.equal(result.status, 0, result.stderr);
    assert.match(result.stderr, /INFO +GitHub App checks skipped/);
    assert.ok(!result.calls.some((call) => call.startsWith("curl")), "no GitHub call");
});

// A node pool without availability zones only needs the size in the region, so a
// restriction of single zones must not fail the preflight; the vCPU quota must cover it.
const defaultEnvLines = [
    "SUBSCRIPTION_ID=sub-1",
    "LOCATION=germanywestcentral",
    "ENVIRONMENT=staging",
    "GITHUB_REPO=mmkdigitalio/hackathon-platform-team",
    "GITHUB_APP_ID=5542094",
    'AGENT_REPOS="mmkdigitalio/hackathon-platform-team mmkdigitalio/hackathon-platform-affine"',
    "STATE_STORAGE_ACCOUNT=stmmkstatetest",
];
const sku = (name, family, vcpus, restrictions) =>
    JSON.stringify([{ name, family, capabilities: [{ name: "vCPUs", value: String(vcpus) }], restrictions }]);
const usage = (entries) =>
    JSON.stringify(entries.map(([value, currentValue, limit]) => ({ name: { value }, currentValue, limit })));
const zoneRestriction = { type: "Zone", reasonCode: "NotAvailableForSubscription", restrictionInfo: { zones: ["1"] } };
const locationRestriction = { type: "Location", reasonCode: "NotAvailableForSubscription", restrictionInfo: {} };

test("a VM size restricted only in single zones passes and its vCPU quota is checked", { skip }, () => {
    const box = sandbox(
        { az: azStub, gh: ghStub, curl: curlStub },
        { envFile: [...defaultEnvLines, "NODE_VM_SIZE=Standard_D2als_v6", "NODE_COUNT=1"].join("\n") },
    );
    const result = box.run("preflight.sh", [box.config], {
        SKUS_JSON: sku("Standard_D2als_v6", "standardDalv6Family", 2, [zoneRestriction]),
        USAGE_JSON: usage([["standardDalv6Family", "0", "10"], ["cores", "0", "10"]]),
    });
    assert.equal(result.status, 0, result.stderr);
    assert.match(result.stderr, /OK +VM size Standard_D2als_v6 is available in germanywestcentral/);
    assert.match(result.stderr, /INFO +Standard_D2als_v6 is restricted in zones 1/);
    assert.match(result.stderr, /OK +vCPU quota standardDalv6Family in germanywestcentral: 2 needed, 10 free/);
    assert.match(result.stderr, /OK +vCPU quota cores in germanywestcentral: 2 needed, 10 free/);
});

test("a regional restriction and a missing vCPU quota are both reported", { skip }, () => {
    const box = sandbox(
        { az: azStub, gh: ghStub, curl: curlStub },
        { envFile: [...defaultEnvLines, "NODE_VM_SIZE=Standard_D4s_v5", "NODE_COUNT=2"].join("\n") },
    );
    const result = box.run("preflight.sh", [box.config], {
        SKUS_JSON: sku("Standard_D4s_v5", "standardDSv5Family", 4, [locationRestriction, zoneRestriction]),
        USAGE_JSON: usage([["standardDSv5Family", "0", "0"], ["cores", "6", "10"]]),
    });
    assert.equal(result.status, 1);
    assert.match(result.stderr, /- VM size Standard_D4s_v5 in germanywestcentral \(restriction: NotAvailableForSubscription\)/);
    assert.match(result.stderr, /- vCPU quota standardDSv5Family in germanywestcentral: 2 x Standard_D4s_v5 need 8, 0 free/);
    assert.match(result.stderr, /- vCPU quota cores in germanywestcentral: 2 x Standard_D4s_v5 need 8, 4 free/);
});

test("a region where PostgreSQL Flexible Server is restricted fails the preflight", { skip }, () => {
    const box = newSandbox();
    const ok = box.run("preflight.sh", [box.config]);
    assert.match(ok.stderr, /OK +PostgreSQL Flexible Server can be provisioned in germanywestcentral/);
    const result = box.run("preflight.sh", [box.config], {
        PG_REASON: "Provisioning is restricted in this region. Please choose a different region.",
    });
    assert.equal(result.status, 1);
    assert.match(result.stderr, /- PostgreSQL Flexible Server in germanywestcentral \(Provisioning is restricted in this region/);
});

test("an invalid env file is rejected", { skip }, () => {
    const box = sandbox({}, { envFile: "SUBSCRIPTION_ID=sub-1\n" });
    const result = box.run("preflight.sh", [box.config]);
    assert.equal(result.status, 1);
    assert.match(result.stderr, /LOCATION is empty/);
});

// ---- bootstrap ----

function assertNoSecretInArgv(calls) {
    for (const value of [stateKey, pat, "first-passphrase"]) {
        assert.ok(!calls.some((call) => call.includes(value)), `${value} appeared on a command line`);
    }
}

test("first run creates the state backend and all four repository secrets, the PAT when given", { skip }, () => {
    const box = newSandbox();
    const result = box.run("bootstrap.sh", [box.config], { OPS_GITHUB_TOKEN: pat, PULUMI_CONFIG_PASSPHRASE: "first-passphrase" });
    assert.equal(result.status, 0, result.stderr);
    for (const call of ["az group create", "az storage account create", "az storage container create"]) {
        assert.ok(result.calls.some((c) => c.startsWith(call)), call);
    }
    const account = result.calls.find((c) => c.startsWith("az storage account create"));
    assert.match(account, /--allow-shared-key-access true/);
    assert.match(result.calls.find((c) => c.includes("blob-service-properties")), /--enable-versioning true .*--delete-retention-days 30 .*--container-delete-retention-days 30/);
    assert.equal(box.read("secret-PULUMI_BACKEND_ACCOUNT"), "stmmkstatetest");
    assert.equal(box.read("secret-PULUMI_BACKEND_KEY"), stateKey);
    assert.equal(box.read("secret-PULUMI_CONFIG_PASSPHRASE"), "first-passphrase");
    assert.equal(box.read("secret-OPS_GITHUB_TOKEN"), pat);
    assert.ok(!result.calls.some((c) => / lock /.test(c) || /role assignment/.test(c) || / ad /.test(c)), "Contributor only");
    assert.match(result.stdout, /Bootstrap complete for staging/);
    assertNoSecretInArgv(result.calls);
});

test("spec scenario: a repeated bootstrap changes nothing and keeps the passphrase", { skip }, () => {
    const box = newSandbox();
    box.run("bootstrap.sh", [box.config], { OPS_GITHUB_TOKEN: pat, PULUMI_CONFIG_PASSPHRASE: "first-passphrase" });
    const result = box.run("bootstrap.sh", [box.config], { OPS_GITHUB_TOKEN: pat, PULUMI_CONFIG_PASSPHRASE: "other-passphrase" });
    assert.equal(result.status, 0, result.stderr);
    assert.ok(!result.calls.some((c) => /group create|storage account create/.test(c)));
    assert.ok(!result.calls.some((c) => c.startsWith("gh secret set PULUMI_CONFIG_PASSPHRASE")));
    assert.equal(box.read("secret-PULUMI_CONFIG_PASSPHRASE"), "first-passphrase");
    assert.match(result.stderr, /PULUMI_CONFIG_PASSPHRASE exists; value left unchanged/);
});

test("spec scenario: an interrupted first bootstrap is completed without clean-up", { skip }, () => {
    const box = newSandbox();
    box.touch("rg", "account");
    const result = box.run("bootstrap.sh", [box.config], { OPS_GITHUB_TOKEN: pat, PULUMI_CONFIG_PASSPHRASE: "first-passphrase" });
    assert.equal(result.status, 0, result.stderr);
    assert.ok(!result.calls.some((c) => /group create|storage account create/.test(c)));
    assert.equal(box.read("secret-PULUMI_BACKEND_KEY"), stateKey);
    assert.equal(box.read("secret-PULUMI_CONFIG_PASSPHRASE"), "first-passphrase");
});

test("a bootstrap without OPS_GITHUB_TOKEN creates the backend and every secret except the PAT", { skip }, () => {
    const box = newSandbox();
    const result = box.run("bootstrap.sh", [box.config], { PULUMI_CONFIG_PASSPHRASE: "first-passphrase" });
    assert.equal(result.status, 0, result.stderr);
    assert.equal(box.read("secret-PULUMI_BACKEND_KEY"), stateKey);
    assert.equal(box.read("secret-PULUMI_CONFIG_PASSPHRASE"), "first-passphrase");
    assert.ok(!result.calls.some((call) => call.startsWith("gh secret set OPS_GITHUB_TOKEN")));
});

test("a first run without passphrase stops before anything is created", { skip }, () => {
    const box = newSandbox();
    const result = box.run("bootstrap.sh", [box.config], { OPS_GITHUB_TOKEN: pat });
    assert.equal(result.status, 1);
    assert.match(result.stderr, /PULUMI_CONFIG_PASSPHRASE is missing/);
    assert.deepEqual(created(result.calls), []);
});

test("a failed secret listing stops the run instead of replacing the passphrase", { skip }, () => {
    const box = newSandbox();
    box.touch("secret-PULUMI_CONFIG_PASSPHRASE");
    const result = box.run("bootstrap.sh", [box.config], {
        OPS_GITHUB_TOKEN: pat,
        PULUMI_CONFIG_PASSPHRASE: "other-passphrase",
        FAIL_SECRET_LIST: "1",
    });
    assert.notEqual(result.status, 0);
    assert.deepEqual(created(result.calls), []);
});

test("a failed preflight stops the bootstrap before any change", { skip }, () => {
    const box = newSandbox();
    const result = box.run("bootstrap.sh", [box.config], {
        OPS_GITHUB_TOKEN: pat,
        PULUMI_CONFIG_PASSPHRASE: "first-passphrase",
        NOT_CONTRIBUTOR: "1",
    });
    assert.equal(result.status, 1);
    assert.deepEqual(created(result.calls), []);
});
