// preflight.sh and bootstrap.sh against stateful az/gh/curl stubs: rights are checked
// before anything is created, every step only creates what is missing, and secret values
// never reach a command line.
import assert from "node:assert/strict";
import { test } from "node:test";
import { curlStub, sandbox, skipWithoutBash as skip } from "./helpers/script-stubs.mjs";

const stateKey = "STATE-ACCOUNT-KEY-VALUE";
const pat = "github_pat_agent_token_value";

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

test("preflight passes with Contributor, repository admin and a complete PAT, creating nothing", { skip }, () => {
    const box = newSandbox();
    const result = box.run("preflight.sh", [box.config], { OPS_GITHUB_TOKEN: pat });
    assert.equal(result.status, 0, result.stderr);
    assert.match(result.stderr, /OK +Contributor \(or higher\)/);
    assert.match(result.stderr, /OK +PAT Actions \(write\) on mmkdigitalio\/hackathon-platform-affine/);
    assert.match(result.stderr, /Preflight passed/);
    assert.deepEqual(created(result.calls), []);
});

test("spec scenario: a PAT without Actions rights stops the preflight and names the right", { skip }, () => {
    const box = newSandbox();
    const result = box.run("preflight.sh", [box.config], {
        OPS_GITHUB_TOKEN: pat,
        CURL_DENY: "GET:/actions/runs@mmkdigitalio/hackathon-platform-affine POST:/actions/workflows/0/dispatches@mmkdigitalio/hackathon-platform-affine",
    });
    assert.equal(result.status, 1);
    assert.match(result.stderr, /- PAT permission Actions \(read\) on mmkdigitalio\/hackathon-platform-affine \(HTTP 403\)/);
    assert.match(result.stderr, /- PAT permission Actions \(write\) on mmkdigitalio\/hackathon-platform-affine \(HTTP 403\)/);
    assert.doesNotMatch(result.stderr, /MISSING.*hackathon-platform-team/);
});

test("missing write permissions are detected through the invalid-body probes", { skip }, () => {
    const box = newSandbox();
    const result = box.run("preflight.sh", [box.config], {
        OPS_GITHUB_TOKEN: pat,
        CURL_DENY: "POST:/issues@mmkdigitalio/hackathon-platform-team POST:/git/refs@mmkdigitalio/hackathon-platform-team",
    });
    assert.equal(result.status, 1);
    assert.match(result.stderr, /PAT permission Issues \(write\) on mmkdigitalio\/hackathon-platform-team/);
    assert.match(result.stderr, /PAT permission Contents \(write\) on mmkdigitalio\/hackathon-platform-team/);
});

test("missing Contributor and missing repository admin are both reported", { skip }, () => {
    const box = newSandbox();
    const result = box.run("preflight.sh", [box.config], { OPS_GITHUB_TOKEN: pat, NOT_CONTRIBUTOR: "1", NOT_ADMIN: "1" });
    assert.equal(result.status, 1);
    assert.match(result.stderr, /- Contributor on subscription sub-1/);
    assert.match(result.stderr, /- Admin on mmkdigitalio\/hackathon-platform-team/);
});

test("an invalid PAT is reported per repository", { skip }, () => {
    const box = newSandbox();
    const result = box.run("preflight.sh", [box.config], { OPS_GITHUB_TOKEN: "invalid-token" });
    assert.equal(result.status, 1);
    assert.match(result.stderr, /PAT access to repository mmkdigitalio\/hackathon-platform-affine \(HTTP 401\)/);
});

test("preflight without OPS_GITHUB_TOKEN stops before any call", { skip }, () => {
    const box = newSandbox();
    const result = box.run("preflight.sh", [box.config]);
    assert.equal(result.status, 1);
    assert.match(result.stderr, /OPS_GITHUB_TOKEN is not set/);
    assert.deepEqual(result.calls, []);
});

// A node pool without availability zones only needs the size in the region, so a
// restriction of single zones must not fail the preflight; the vCPU quota must cover it.
const defaultEnvLines = [
    "SUBSCRIPTION_ID=sub-1",
    "LOCATION=germanywestcentral",
    "ENVIRONMENT=staging",
    "GITHUB_REPO=mmkdigitalio/hackathon-platform-team",
    'PAT_REPOS="mmkdigitalio/hackathon-platform-team mmkdigitalio/hackathon-platform-affine"',
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
        OPS_GITHUB_TOKEN: pat,
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
        OPS_GITHUB_TOKEN: pat,
        SKUS_JSON: sku("Standard_D4s_v5", "standardDSv5Family", 4, [locationRestriction, zoneRestriction]),
        USAGE_JSON: usage([["standardDSv5Family", "0", "0"], ["cores", "6", "10"]]),
    });
    assert.equal(result.status, 1);
    assert.match(result.stderr, /- VM size Standard_D4s_v5 in germanywestcentral \(restriction: NotAvailableForSubscription\)/);
    assert.match(result.stderr, /- vCPU quota standardDSv5Family in germanywestcentral: 2 x Standard_D4s_v5 need 8, 0 free/);
    assert.match(result.stderr, /- vCPU quota cores in germanywestcentral: 2 x Standard_D4s_v5 need 8, 4 free/);
});

test("an invalid env file is rejected", { skip }, () => {
    const box = sandbox({}, { envFile: "SUBSCRIPTION_ID=sub-1\n" });
    const result = box.run("preflight.sh", [box.config], { OPS_GITHUB_TOKEN: pat });
    assert.equal(result.status, 1);
    assert.match(result.stderr, /LOCATION is empty/);
});

// ---- bootstrap ----

function assertNoSecretInArgv(calls) {
    for (const value of [stateKey, pat, "first-passphrase"]) {
        assert.ok(!calls.some((call) => call.includes(value)), `${value} appeared on a command line`);
    }
}

test("first run creates the state backend and all four repository secrets", { skip }, () => {
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
