import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import YAML from "yaml";
import {
    checkContract,
    ContractError,
    maskCommands,
    missingBackendSettings,
    run,
    secretOutputNames,
    sourceReaders,
    stepOutputLines,
} from "../../.github/actions/foundation-outputs/read-outputs.mjs";

const actionDir = new URL("../../.github/actions/foundation-outputs/", import.meta.url);
const schema = JSON.parse(
    readFileSync(new URL("../schemas/foundation-outputs/foundation-outputs.schema.json", import.meta.url), "utf8"),
);
const example = JSON.parse(
    readFileSync(new URL("../schemas/foundation-outputs/example.staging.json", import.meta.url), "utf8"),
);

const kubeconfig = [
    "apiVersion: v1",
    "clusters:",
    "- cluster:",
    "    certificate-authority-data: LS0tLS1CRUdJTiBDRVJUSUZJQ0FURS0tLS0t",
    "    server: https://mmk-platform-staging.hcp.germanywestcentral.azmk8s.io:443",
    "users:",
    "- name: clusterAdmin",
    "  user:",
    "    token: 0123456789abcdef0123456789abcdef",
].join("\n");

const outputs = {
    ...example,
    kubeconfig,
    acrPassword: "acr-admin-password",
    postgresAdminPassword: "pg-secret",
    modelApiKeys: { anthropic: "sk-ant", "azure-openai": "aoai" },
};

const tempFile = () => join(mkdtempSync(join(tmpdir(), "foundation-outputs-")), "out");

test("secret names come from x-secret markers", () => {
    assert.ok(secretOutputNames(schema).includes("kubeconfig"));
    assert.ok(secretOutputNames(schema).includes("opsGithubToken"));
    assert.ok(!secretOutputNames(schema).includes("acrUsername"));
});

test("spec scenario: a different contract version aborts with expected and actual version", () => {
    assert.throws(() => checkContract({ contractVersion: 2 }, 1), (error) => {
        return error instanceof ContractError && /expected 1/.test(error.message) && /provides 2/.test(error.message);
    });
    assert.doesNotThrow(() => checkContract({ contractVersion: 1 }, 1));
});

test("secrets are masked whole, per line and per embedded YAML value", () => {
    const masks = maskCommands(outputs, secretOutputNames(schema));
    for (const expected of [
        "pg-secret",
        "acr-admin-password",
        "sk-ant",
        "aoai",
        "token: 0123456789abcdef0123456789abcdef",
        "0123456789abcdef0123456789abcdef",
        "LS0tLS1CRUdJTiBDRVJUSUZJQ0FURS0tLS0t",
    ]) {
        assert.ok(masks.includes(`::add-mask::${expected}`), expected);
    }
    assert.ok(!masks.includes("::add-mask::v1"), "short YAML values are not masked on their own");
    assert.ok(!masks.some((m) => m.includes("\n")), "a mask command never spans lines");
    assert.ok(!masks.some((m) => m.includes("acrmmkplatform")), "non-secret values stay visible");
});

test("multi-line values use the heredoc step-output form", () => {
    const lines = stepOutputLines({ single: "a", multi: "x\ny", obj: { k: 1 } }, "DELIM");
    assert.deepEqual(lines, ["single=a", "multi<<DELIM", "x\ny", "DELIM", 'obj={"k":1}']);
});

test("run masks secrets before failing on a version mismatch and writes nothing", () => {
    const outputFile = tempFile();
    const written = [];
    assert.throws(() =>
        run({
            stack: "organization/foundation/staging",
            expectedVersion: 2,
            outputFile,
            readOutputs: () => outputs,
            readSecretNames: () => [],
            write: (line) => written.push(line),
        }),
    );
    assert.ok(written.includes("::add-mask::pg-secret"));
    assert.throws(() => readFileSync(outputFile), /ENOENT/);
});

test("run writes all outputs for a matching version", () => {
    const outputFile = tempFile();
    const count = run({
        stack: "organization/foundation/staging",
        expectedVersion: 1,
        outputFile,
        readOutputs: () => outputs,
        readSecretNames: () => [],
        write: () => {},
    });
    assert.equal(count, Object.keys(outputs).length);
    const written = readFileSync(outputFile, "utf8");
    assert.match(written, /^acrLoginServer=acrmmkplatformstagingab12cd\.azurecr\.io$/m);
    assert.match(written, /^modelApiKeys=\{"anthropic":"sk-ant","azure-openai":"aoai"\}$/m);
});

test("a secret output unknown to this schema version is masked when the stack marks it secret", () => {
    const written = [];
    run({
        stack: "organization/foundation/staging",
        expectedVersion: 1,
        outputFile: tempFile(),
        readOutputs: () => ({ ...outputs, futureToken: "future-secret-value" }),
        readSecretNames: () => ["futureToken"],
        write: (line) => written.push(line),
    });
    assert.ok(written.includes("::add-mask::future-secret-value"));
});

test("empty secret objects produce no mask for the literal {}", () => {
    assert.deepEqual(maskCommands({ modelApiKeys: {} }, ["modelApiKeys"]), []);
});

test("source stack names the missing backend inputs; placeholder needs none", () => {
    assert.deepEqual(missingBackendSettings({ AZURE_STORAGE_ACCOUNT: "st" }), ["backend-key", "config-passphrase"]);
    assert.throws(() => sourceReaders("stack", {}), /backend-account, backend-key, config-passphrase/);
    assert.throws(() => sourceReaders("other", {}), /source must be/);
    assert.deepEqual(sourceReaders("placeholder", {}).readOutputs(), example);
});

test("spec scenario: the action reads the placeholder outputs end to end", () => {
    const outputFile = tempFile();
    const stdout = execFileSync(process.execPath, [new URL("read-outputs.mjs", actionDir).pathname], {
        encoding: "utf8",
        env: {
            PATH: process.env.PATH,
            OUTPUTS_SOURCE: "placeholder",
            FOUNDATION_STACK: "organization/foundation/staging",
            EXPECTED_CONTRACT_VERSION: "1",
            GITHUB_OUTPUT: outputFile,
        },
    });
    assert.match(stdout, /Read 30 foundation outputs \(placeholder\)/);
    assert.match(readFileSync(outputFile, "utf8"), /^baseDomain=203-0-113-10\.sslip\.io$/m);
});

test("spec scenario: a wrong expected contract version fails the action", () => {
    assert.throws(
        () =>
            execFileSync(process.execPath, [new URL("read-outputs.mjs", actionDir).pathname], {
                stdio: "pipe",
                env: {
                    PATH: process.env.PATH,
                    OUTPUTS_SOURCE: "placeholder",
                    FOUNDATION_STACK: "organization/foundation/staging",
                    EXPECTED_CONTRACT_VERSION: "2",
                    GITHUB_OUTPUT: tempFile(),
                },
            }),
        (error) => error.status === 1 && /expected 2, stack provides 1/.test(error.stdout.toString()),
    );
});

test("action.yml declares one output per contract property and the inputs consumers use", () => {
    const action = YAML.parse(readFileSync(new URL("action.yml", actionDir), "utf8"));
    assert.deepEqual(Object.keys(action.outputs).sort(), Object.keys(schema.properties).sort());
    for (const [name, output] of Object.entries(action.outputs)) {
        assert.equal(output.value, `\${{ steps.read.outputs.${name} }}`, name);
    }
    // Inputs used by .github/workflows/agent-build.yml (strand D).
    for (const input of ["backend-account", "backend-key", "config-passphrase"]) {
        assert.ok(input in action.inputs, input);
    }
    assert.equal(action.inputs["expected-contract-version"].default, "1");
});
