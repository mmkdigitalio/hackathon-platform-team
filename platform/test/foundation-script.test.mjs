// foundation.sh and summary.mjs against stubbed az/npm/pulumi: the state backend comes
// from the bootstrap names, stack secrets go through stdin, and the run summary shows
// versions and URLs without secret values.
import assert from "node:assert/strict";
import { test } from "node:test";
import { renderSummary } from "../scripts/bootstrap/summary.mjs";
import { sandbox, skipWithoutBash as skip } from "./helpers/script-stubs.mjs";

const stateKey = "STATE-ACCOUNT-KEY-VALUE";

const stackOutputs = {
    contractVersion: 1,
    location: "germanywestcentral",
    resourceGroupName: "rg-mmk-platform-staging",
    baseDomain: "20-79-1-2.sslip.io",
    operationsApiHost: "operations-agent.20-79-1-2.sslip.io",
    publicScheme: "https",
    platformVersions: { kubernetes: "1.34.0", traefikChart: "39.0.0" },
    kubeconfig: "[secret]",
    postgresAdminPassword: "[secret]",
};

const pulumiStub = `
[[ "$PULUMI_BACKEND_URL" == azblob://pulumi-state && "$AZURE_STORAGE_ACCOUNT" == stmmkstatetest && "$AZURE_STORAGE_KEY" == "${stateKey}" ]] || { echo "backend not configured" >&2; exit 7; }
case "$*" in
  "stack select --create organization/foundation/staging --secrets-provider passphrase") ;;
  "config set --secret --stack organization/foundation/staging --path "*)
    value="$(cat; printf x)"; value="\${value%x}"
    [[ -n "$value" && "$value" != *$'\\n' ]] || exit 3
    name="$(arg --path "$@")"; printf '%s' "$value" >"$s/config-\${name//[^A-Za-z0-9-]/_}" ;;
  "preview --diff --stack organization/foundation/staging"*) ;;
  "up --stack organization/foundation/staging") ;;
  "stack output --json --stack organization/foundation/staging") echo '${JSON.stringify(stackOutputs)}' ;;
  *) echo "unexpected pulumi call: $*" >&2; exit 99 ;;
esac
`;

const stubs = {
    az: `
case "$*" in
  "account set --subscription sub-1") ;;
  "storage account keys list --account-name stmmkstatetest --resource-group rg-mmk-platform-state"*) echo "${stateKey}" ;;
  *) echo "unexpected az call: $*" >&2; exit 99 ;;
esac
`,
    npm: `[[ "$*" == "run --silent build" ]] || exit 99`,
    pulumi: pulumiStub,
};

const secretEnv = {
    PULUMI_CONFIG_PASSPHRASE: "stack-passphrase",
    GITHUB_WEBHOOK_SECRET: "webhook-secret-value",
    OPS_GITHUB_TOKEN: "github_pat_agent_value",
};

test("secrets stores every value through stdin, never on a command line", { skip }, () => {
    const box = sandbox(stubs);
    const result = box.run("foundation.sh", [box.config, "secrets"], {
        ...secretEnv,
        MODEL_KEY_ANTHROPIC: "anthropic-model-key",
        MODEL_KEY_AZURE_OPENAI: "aoai-model-key",
    });
    assert.equal(result.status, 0, result.stderr);
    assert.equal(box.read("config-githubWebhookSecret"), "webhook-secret-value");
    assert.equal(box.read("config-opsGithubToken"), "github_pat_agent_value");
    assert.equal(box.read("config-modelApiKeys__anthropic__"), "anthropic-model-key");
    assert.equal(box.read("config-modelApiKeys__azure-openai__"), "aoai-model-key");
    for (const secret of [stateKey, "stack-passphrase", "webhook-secret-value", "github_pat_agent_value", "anthropic-model-key", "aoai-model-key"]) {
        assert.ok(!result.calls.some((call) => call.includes(secret)), `${secret} appeared on a command line`);
    }
});

test("secrets with only the Azure OpenAI key sets only that provider", { skip }, () => {
    const box = sandbox(stubs);
    const result = box.run("foundation.sh", [box.config, "secrets"], { ...secretEnv, MODEL_KEY_AZURE_OPENAI: "aoai-model-key" });
    assert.equal(result.status, 0, result.stderr);
    assert.ok(!result.calls.some((call) => call.includes('modelApiKeys["anthropic"]')));
    assert.equal(box.read("config-modelApiKeys__azure-openai__"), "aoai-model-key");
});

test("secrets with DEFER_PAT=1 stores everything but the PAT; without it a missing PAT stops", { skip }, () => {
    const { OPS_GITHUB_TOKEN, ...withoutPat } = secretEnv;
    const deferred = sandbox(stubs);
    const result = deferred.run("foundation.sh", [deferred.config, "secrets"], {
        ...withoutPat,
        DEFER_PAT: "1",
        MODEL_KEY_AZURE_OPENAI: "aoai-model-key",
    });
    assert.equal(result.status, 0, result.stderr);
    assert.equal(deferred.read("config-githubWebhookSecret"), "webhook-secret-value");
    assert.ok(!result.calls.some((call) => call.includes("opsGithubToken")));
    const strict = sandbox(stubs);
    const stopped = strict.run("foundation.sh", [strict.config, "secrets"], { ...withoutPat, MODEL_KEY_AZURE_OPENAI: "aoai-model-key" });
    assert.equal(stopped.status, 1);
    assert.match(stopped.stderr, /OPS_GITHUB_TOKEN is not set/);
});

test("secrets without any model key stops before writing stack config", { skip }, () => {
    const box = sandbox(stubs);
    const result = box.run("foundation.sh", [box.config, "secrets"], secretEnv);
    assert.equal(result.status, 1);
    assert.match(result.stderr, /at least one model key/);
    assert.ok(!result.calls.some((call) => call.startsWith("pulumi config set")));
});

test("preview passes extra pulumi arguments such as --expect-no-changes", { skip }, () => {
    const box = sandbox(stubs);
    const result = box.run("foundation.sh", [box.config, "preview", "--expect-no-changes"], secretEnv);
    assert.equal(result.status, 0, result.stderr);
    assert.ok(result.calls.includes("pulumi preview --diff --stack organization/foundation/staging --expect-no-changes"));
    assert.ok(result.calls.includes("npm run --silent build"), "the program is compiled first");
});

test("up deploys and prints the run summary with the Git SHA", { skip }, () => {
    const box = sandbox(stubs);
    const result = box.run("foundation.sh", [box.config, "up"], secretEnv);
    assert.equal(result.status, 0, result.stderr);
    assert.ok(result.calls.includes("pulumi up --stack organization/foundation/staging"));
    assert.match(result.stdout, /## Foundation organization\/foundation\/staging/);
    assert.match(result.stdout, /\| Git SHA \| `[0-9a-f]{40}(-dirty)?` \|/);
    assert.match(result.stdout, /\| traefikChart \| `39\.0\.0` \|/);
});

test("an unknown command and a missing passphrase are rejected before any change", { skip }, () => {
    const box = sandbox(stubs);
    const unknown = box.run("foundation.sh", [box.config, "destroy"], secretEnv);
    assert.equal(unknown.status, 1);
    assert.match(unknown.stderr, /Usage: .* secrets\|preview\|up\|outputs\|ingress-check/);
    const noPassphrase = box.run("foundation.sh", [box.config, "up"], {});
    assert.equal(noPassphrase.status, 1);
    assert.match(noPassphrase.stderr, /PULUMI_CONFIG_PASSPHRASE is not set/);
    assert.deepEqual([...unknown.calls, ...noPassphrase.calls], []);
});

// ---- summary.mjs ----

test("the run summary lists versions, URLs and outputs, secrets only as [secret]", () => {
    const markdown = renderSummary({ stack: "organization/foundation/staging", gitSha: "abc123", outputs: stackOutputs });
    assert.match(markdown, /\| Git SHA \| `abc123` \|/);
    assert.match(markdown, /\| contractVersion \| `1` \|/);
    assert.match(markdown, /\| kubernetes \| `1\.34\.0` \|/);
    assert.match(markdown, /- Apps: `https:\/\/<app-id>\.20-79-1-2\.sslip\.io`/);
    assert.match(markdown, /- Operations API health: https:\/\/operations-agent\.20-79-1-2\.sslip\.io\/healthz/);
    assert.match(markdown, /\| `postgresAdminPassword` \| \[secret\] \|/);
    assert.match(markdown, /StackReference\("organization\/foundation\/staging"\)/);
});

test("the run summary follows the HTTP fallback and escapes table separators", () => {
    const { platformVersions, ...withoutVersions } = stackOutputs;
    const markdown = renderSummary({
        stack: "organization/foundation/staging",
        gitSha: "abc123",
        outputs: { ...withoutVersions, publicScheme: "http", location: "a|b" },
    });
    assert.match(markdown, /- Apps: `http:\/\/<app-id>\./);
    assert.match(markdown, /\| `location` \| a\\\|b \|/);
    assert.doesNotMatch(markdown, /traefikChart/);
});
