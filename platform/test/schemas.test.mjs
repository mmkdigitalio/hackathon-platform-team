import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import Ajv2020 from "ajv/dist/2020.js";

const load = (path) => JSON.parse(readFileSync(new URL(`../${path}`, import.meta.url), "utf8"));

const conventions = load("schemas/conventions/conventions.json");
const outputsSchema = load("schemas/foundation-outputs/foundation-outputs.schema.json");
const example = load("schemas/foundation-outputs/example.staging.json");

function newAjv() {
    const ajv = new Ajv2020({ strict: true, allErrors: true });
    ajv.addKeyword("x-secret");
    ajv.addFormat("ipv4", /^(\d{1,3}\.){3}\d{1,3}$/);
    return ajv;
}

test("conventions.json matches its schema", () => {
    const validate = newAjv().compile(load("schemas/conventions/conventions.schema.json"));
    assert.ok(validate(conventions), JSON.stringify(validate.errors));
});

test("app-id and release-reference schemas use the patterns from conventions.json", () => {
    const appId = load("schemas/conventions/app-id.schema.json");
    assert.equal(appId.pattern, conventions.appId.pattern);
    assert.equal(appId.allOf[0].pattern, conventions.appId.endPattern);
    assert.deepEqual(appId.not.enum, conventions.appId.reserved);

    const release = load("schemas/conventions/release-reference.schema.json");
    for (const [field, pattern] of Object.entries(conventions.releaseReference)) {
        assert.equal(release.properties[field].pattern, pattern, field);
    }
});

test("app-id schema rejects Demo_App", () => {
    const validate = newAjv().compile(load("schemas/conventions/app-id.schema.json"));
    assert.equal(validate("Demo_App"), false);
    assert.equal(validate("demo-"), false);
    assert.equal(validate("affine"), true);
});

test("placeholder example validates against the output contract", () => {
    const validate = newAjv().compile(outputsSchema);
    assert.ok(validate(example), JSON.stringify(validate.errors));
});

test("example contains no real secret values", () => {
    for (const [name, property] of Object.entries(outputsSchema.properties)) {
        if (!property["x-secret"]) continue;
        const values = typeof example[name] === "object" ? Object.values(example[name]) : [example[name]];
        for (const value of values) assert.equal(value, "<secret>", name);
    }
});

test("contract marks exactly the secrets named in the spec", () => {
    const secrets = Object.entries(outputsSchema.properties)
        .filter(([, p]) => p["x-secret"])
        .map(([name]) => name)
        .sort();
    assert.deepEqual(secrets, [
        "acrPassword",
        "alertWebhookKey",
        "diagnosticsStorageKey",
        "githubWebhookSecret",
        "kubeconfig",
        "maintenanceApiKey",
        "modelApiKeys",
        "opsGithubToken",
        "postgresAdminPassword",
    ]);
});

test("every spec output is required", () => {
    for (const name of [
        "contractVersion", "environment", "location", "resourceGroupName", "baseDomain", "ingressIp",
        "aksClusterName", "kubeconfig", "acrLoginServer", "acrUsername", "acrPassword",
        "appInsightsConnectionString", "actionGroupIntakeId", "actionGroupHumansId", "operationsApiHost",
        "postgresHost", "postgresAdminUser", "postgresAdminPassword", "postgresOperationsDatabase",
        "diagnosticsStorageAccount", "diagnosticsContainer", "diagnosticsStorageKey", "alertWebhookKey",
        "maintenanceApiKey", "githubWebhookSecret", "modelApiKeys", "opsGithubToken",
    ]) {
        assert.ok(outputsSchema.required.includes(name), name);
    }
});

test("consumers can detect an incompatible contract version", () => {
    const validate = newAjv().compile(outputsSchema);
    assert.equal(validate({ ...example, contractVersion: 2 }), false);
});

test("model API keys need at least one provider", () => {
    const validate = newAjv().compile(outputsSchema);
    assert.equal(validate({ ...example, modelApiKeys: {} }), false);
    assert.equal(validate({ ...example, modelApiKeys: { "azure-openai": "<secret>" } }), true);
});

test("baseDomain and operationsApiHost follow the sslip.io convention", () => {
    const validate = newAjv().compile(outputsSchema);
    assert.equal(validate({ ...example, baseDomain: "platform.example.org" }), false);
    assert.equal(validate({ ...example, operationsApiHost: "agent.203-0-113-10.sslip.io" }), false);
});
