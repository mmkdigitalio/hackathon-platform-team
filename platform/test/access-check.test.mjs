// access-check.sh against stubbed kubectl/docker/psql/az/curl: every category is
// reported on its own, a broken credential fails exactly its category, and secrets never
// reach a command line.
import assert from "node:assert/strict";
import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";
import { appKeys, curlStub, sandbox, skipWithoutBash as skip } from "./helpers/script-stubs.mjs";

const kubeconfig = [
    "apiVersion: v1",
    "users:",
    "- name: clusterAdmin",
    "  user:",
    "    client-certificate-data: CLIENT-CERT-DATA",
    "    client-key-data: CLIENT-KEY-DATA",
    "    token: KUBE-TOKEN-VALUE",
].join("\n");

const stubs = {
    kubectl: `
config="$(arg --kubeconfig "$@")"
[[ "$(stat -c %a "$config" 2>/dev/null || stat -f %Lp "$config")" == 600 ]] || { echo "kubeconfig not private" >&2; exit 5; }
grep -q 'token: KUBE-TOKEN-VALUE' "$config" && echo yes || { echo no; exit 1; }
`,
    docker: `
password="$(cat)"
[[ "$password" == "acr-admin-password" && -n "$DOCKER_CONFIG" ]] || exit 1
`,
    psql: `
[[ "$PGPASSWORD" == "pg-admin-password" && "$PGSSLMODE" == require ]] || exit 2
echo t
`,
    az: `
[[ "$AZURE_STORAGE_KEY" == "ZGlhZ25vc3RpY3Mta2V5" && "$AZURE_STORAGE_ACCOUNT" == stmmkdiag ]] || exit 1
file="$(arg --file "$@")"
case "$*" in
  "storage blob upload"*) cp "$file" "$s/blob" ;;
  "storage blob download"*) cp "$s/blob" "$file" ;;
  "storage blob delete"*) rm "$s/blob" ;;
  *) exit 99 ;;
esac
`,
    curl: curlStub,
};

const app = appKeys();
const outputs = {
    FOUNDATION_STATE_OUTCOME: "success",
    KUBECONFIG_CONTENT: kubeconfig,
    ACR_LOGIN_SERVER: "acrmmkplatformstagingab12cd.azurecr.io",
    ACR_USERNAME: "acrmmkplatformstagingab12cd",
    ACR_PASSWORD: "acr-admin-password",
    POSTGRES_HOST: "psql-mmk-platform-staging-ab12cd.postgres.database.azure.com",
    POSTGRES_USER: "opsadmin",
    POSTGRES_PASSWORD: "pg-admin-password",
    POSTGRES_DATABASE: "operations",
    DIAGNOSTICS_ACCOUNT: "stmmkdiag",
    DIAGNOSTICS_CONTAINER: "diagnostics",
    DIAGNOSTICS_KEY: "ZGlhZ25vc3RpY3Mta2V5",
    // The foundation output may carry the PEM with escaped line breaks.
    GITHUB_APP_PRIVATE_KEY: app.privateKey.replaceAll("\n", "\\n"),
    GITHUB_APP_ID: "5542094",
    GITHUB_APP_INSTALLATION_ID: "176901698",
    APP_PUBLIC_KEY: app.publicKeyFile,
    AGENT_REPOS: "mmkdigitalio/hackathon-platform-team mmkdigitalio/hackathon-platform-affine",
};

function runCheck(env = {}) {
    const box = sandbox(stubs);
    const summary = join(box.dir, "summary.md");
    writeFileSync(summary, "");
    const result = box.run("access-check.sh", [], { ...outputs, GITHUB_STEP_SUMMARY: summary, ...env });
    return { ...result, box, summary: readFileSync(summary, "utf8") };
}

const row = (summary, category) => summary.split("\n").find((line) => line.startsWith(`| \`${category}\` |`));

test("spec scenario: all access paths pass and every category is reported on its own", { skip }, () => {
    const result = runCheck({ WORKFLOW_GITHUB_TOKEN: "github_pat_workflow_value" });
    assert.equal(result.status, 0, result.stderr);
    for (const category of ["foundation-state", "kubernetes", "acr", "postgres", "diagnostics-storage", "github", "github-workflow-token"]) {
        assert.match(row(result.summary, category), /\| pass \|/, category);
    }
    assert.throws(() => result.box.read("blob"), /ENOENT/, "the test blob is deleted again");
    assert.match(result.box.read("curl-requests"), /POST https:\/\/api.github.com\/app\/installations\/176901698\/access_tokens/);
    for (const secret of ["acr-admin-password", "pg-admin-password", "ZGlhZ25vc3RpY3Mta2V5", "KUBE-TOKEN-VALUE", "PRIVATE KEY", "ghs_installation_token"]) {
        assert.ok(!result.calls.some((call) => call.includes(secret)), `${secret} appeared on a command line`);
        assert.ok(!result.stderr.includes(secret) && !result.summary.includes(secret), `${secret} was printed`);
    }
});

for (const category of ["kubernetes", "acr", "postgres", "diagnostics-storage", "github"]) {
    test(`spec scenario: a broken ${category} credential fails exactly that category`, { skip }, () => {
        const result = runCheck({ BREAK_CATEGORY: category });
        assert.equal(result.status, 1);
        assert.match(row(result.summary, category), /\| fail \|/);
        for (const other of ["foundation-state", "kubernetes", "acr", "postgres", "diagnostics-storage", "github"]) {
            if (other !== category) assert.match(row(result.summary, other), /\| pass \|/, other);
        }
        assert.match(result.summary, new RegExp(`Deliberately broken category: \`${category}\``));
        assert.match(result.stderr, /the bootstrap is not fully functional/);
    });
}

test("without foundation outputs the state category fails and the others are skipped", { skip }, () => {
    const result = runCheck({ FOUNDATION_STATE_OUTCOME: "failure", BREAK_CATEGORY: "foundation-state" });
    assert.equal(result.status, 1);
    assert.match(row(result.summary, "foundation-state"), /\| fail \| foundation-outputs step: failure \|/);
    assert.match(row(result.summary, "postgres"), /\| fail \| skipped: no foundation outputs \|/);
    assert.deepEqual(result.calls, []);
});

test("an unknown break category is rejected before any check", { skip }, () => {
    const result = runCheck({ BREAK_CATEGORY: "github-workflow-token" });
    assert.equal(result.status, 1);
    assert.match(result.stderr, /BREAK_CATEGORY must be none or one of/);
    assert.deepEqual(result.calls, []);
});

test("a missing output fails its category with the variable name", { skip }, () => {
    const result = runCheck({ POSTGRES_PASSWORD: "" });
    assert.equal(result.status, 1);
    assert.match(row(result.summary, "postgres"), /POSTGRES_PASSWORD is empty/);
});
