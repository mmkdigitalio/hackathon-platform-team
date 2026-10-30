import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { chmodSync, mkdtempSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
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
    "kind: Config",
    "clusters:",
    "- cluster:",
    "    certificate-authority-data: LS0tLS1CRUdJTiBDRVJUSUZJQ0FURS0tLS0t",
    "    server: https://mmk-platform-staging.hcp.germanywestcentral.azmk8s.io:443",
    "  name: aks-mmk-platform-staging",
    "contexts:",
    "- context:",
    "    cluster: aks-mmk-platform-staging",
    "    user: clusterAdmin_rg-mmk-platform-staging_aks-mmk-platform-staging",
    "  name: aks-mmk-platform-staging",
    "current-context: aks-mmk-platform-staging",
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
    githubAppPrivateKey: "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASC\n-----END PRIVATE KEY-----",
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
    for (const visible of ["v1", "apiVersion: v1", "kind: Config", "users:", "aks-mmk-platform-staging"]) {
        assert.ok(!masks.includes(`::add-mask::${visible}`), `${visible} stays visible`);
    }
    assert.ok(!masks.some((m) => m.includes("\n")), "a mask command never spans lines");
    assert.ok(!masks.some((m) => m.includes("acrmmkplatform")), "non-secret values stay visible");
});

test("mask values are escaped like @actions/core, so the runner registers the real value", () => {
    const masks = maskCommands({ githubWebhookSecret: "abc%25def\rx" }, ["githubWebhookSecret"]);
    assert.deepEqual(masks, ["::add-mask::abc%2525def%0Dx"]);
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
    assert.match(stdout, /Read 32 foundation outputs \(placeholder\)/);
    assert.match(readFileSync(outputFile, "utf8"), /^baseDomain=203-0-113-10\.sslip\.io$/m);
});

test("source stack masks every output Pulumi holds as secret, also ones unknown to the schema", () => {
    const bin = mkdtempSync(join(tmpdir(), "fake-pulumi-"));
    const full = { ...outputs, futureSecret: "future-secret-value-123" };
    const plain = Object.fromEntries(
        Object.entries(full).map(([name, value]) => [name, name === "futureSecret" || secretOutputNames(schema).includes(name) ? "[secret]" : value]),
    );
    writeFileSync(join(bin, "full.json"), JSON.stringify(full));
    writeFileSync(join(bin, "plain.json"), JSON.stringify(plain));
    writeFileSync(
        join(bin, "pulumi"),
        `#!/bin/sh\ncase "$*" in\n  "stack output --json --show-secrets --stack organization/foundation/staging") cat "${bin}/full.json" ;;\n  "stack output --json --stack organization/foundation/staging") cat "${bin}/plain.json" ;;\n  *) exit 99 ;;\nesac\n`,
    );
    chmodSync(join(bin, "pulumi"), 0o755);
    const outputFile = tempFile();
    const stdout = execFileSync(process.execPath, [new URL("read-outputs.mjs", actionDir).pathname], {
        encoding: "utf8",
        env: {
            PATH: `${bin}:${process.env.PATH}`,
            OUTPUTS_SOURCE: "stack",
            FOUNDATION_STACK: "organization/foundation/staging",
            EXPECTED_CONTRACT_VERSION: "1",
            GITHUB_OUTPUT: outputFile,
            AZURE_STORAGE_ACCOUNT: "stmmkstate",
            AZURE_STORAGE_KEY: "state-key",
            PULUMI_CONFIG_PASSPHRASE: "passphrase",
        },
    });
    assert.match(stdout, /^::add-mask::future-secret-value-123$/m);
    assert.match(stdout, /^::add-mask::pg-secret$/m);
    // A multi-line PEM is masked line by line, as the runner masks only single lines.
    assert.match(stdout, /^::add-mask::MIIEvQIBADANBgkqhkiG9w0BAQEFAASC$/m);
    assert.match(stdout, /Read 33 foundation outputs \(stack\)/);
    assert.match(readFileSync(outputFile, "utf8"), /^futureSecret=future-secret-value-123$/m);
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
    assert.equal(action.inputs["expected-contract-version"].default, "1");
});

test("every workflow in the repository uses only inputs and outputs the action declares", () => {
    const action = YAML.parse(readFileSync(new URL("action.yml", actionDir), "utf8"));
    const workflowsDir = new URL("../../.github/workflows/", import.meta.url);
    let consumers = 0;
    for (const file of readdirSync(workflowsDir).filter((name) => /\.ya?ml$/.test(name))) {
        const text = readFileSync(new URL(file, workflowsDir), "utf8");
        const steps = Object.values(YAML.parse(text).jobs ?? {}).flatMap((job) => job.steps ?? []);
        for (const step of steps.filter((s) => s.uses === "./.github/actions/foundation-outputs")) {
            consumers += 1;
            for (const input of Object.keys(step.with ?? {})) {
                assert.ok(input in action.inputs, `${file}: unknown input ${input}`);
            }
            if (!step.id) continue;
            for (const [, name] of text.matchAll(new RegExp(`steps\\.${step.id}\\.outputs\\.([A-Za-z]+)`, "g"))) {
                assert.ok(name in action.outputs, `${file}: unknown output ${name}`);
            }
        }
    }
    assert.ok(consumers >= 2, "the foundation workflows use the action");
});
