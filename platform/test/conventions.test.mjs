import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { test } from "node:test";

const require = createRequire(import.meta.url);
const conventions = require("../dist/infra/components/conventions.js");
const cli = new URL("../dist/schemas/conventions/check.js", import.meta.url).pathname;

test("spec scenario: names for affine are derived without further agreement", () => {
    assert.deepEqual(conventions.appNames("affine", "staging", "20-79-1-2.sslip.io"), {
        stack: "organization/app-affine/staging",
        namespace: "affine-staging",
        host: "affine.20-79-1-2.sslip.io",
    });
    assert.equal(conventions.alertRuleName("affine", "staging", "availability"), "alert-affine-staging-availability");
    assert.equal(conventions.availabilityTestName("affine", "staging", "availability"), "webtest-affine-staging-availability");
});

test("foundation stack and resource group follow the convention", () => {
    assert.deepEqual(conventions.foundationNames("staging"), {
        stack: "organization/foundation/staging",
        resourceGroup: "rg-mmk-platform-staging",
    });
    assert.deepEqual(conventions.stateBackend, { resourceGroup: "rg-mmk-platform-state", container: "pulumi-state" });
});

test("spec scenario: app ID Demo_App is rejected", () => {
    assert.notEqual(conventions.appIdProblems("Demo_App").length, 0);
    assert.throws(() => conventions.appNames("Demo_App", "staging", "example.org"), conventions.ConventionError);
});

test("app ID boundaries", () => {
    for (const valid of ["ab", "affine", "operations-agent", "a".repeat(31)]) {
        assert.deepEqual(conventions.appIdProblems(valid), [], valid);
    }
    for (const invalid of ["a", "1app", "-app", "app_x", "App", "a".repeat(32), "", "mmk-platform", "foundation", "demo-"]) {
        assert.notEqual(conventions.appIdProblems(invalid).length, 0, invalid);
    }
});

test("only staging and production are environments", () => {
    assert.throws(() => conventions.foundationNames("dev"), conventions.ConventionError);
    assert.doesNotThrow(() => conventions.foundationNames("production"));
});

test("check names in alert rules are validated", () => {
    assert.throws(() => conventions.alertRuleName("affine", "staging", "Availability"), conventions.ConventionError);
    assert.throws(() => conventions.alertRuleName("affine", "staging", "x"), conventions.ConventionError);
    assert.equal(conventions.alertRuleName("operations-agent", "staging", "healthz"), "alert-operations-agent-staging-healthz");
});

test("baseDomain is the ingress IP with dashes under sslip.io", () => {
    assert.equal(conventions.baseDomainFor("20.79.1.2"), "20-79-1-2.sslip.io");
    for (const invalid of ["20.79.1", "256.1.1.1", "2001:db8::1", "20.79.1.2 "]) {
        assert.throws(() => conventions.baseDomainFor(invalid), conventions.ConventionError, invalid);
    }
});

test("required tags", () => {
    assert.deepEqual(conventions.requiredTags("mmk-platform", "staging", "organization/foundation/staging"), {
        "app-id": "mmk-platform",
        environment: "staging",
        stack: "organization/foundation/staging",
        "managed-by": "pulumi",
    });
});

test("release reference needs git SHA, image digest and chart version", () => {
    const valid = { gitSha: "a".repeat(40), imageDigest: `sha256:${"b".repeat(64)}`, chartVersion: "1.2.3" };
    assert.deepEqual(conventions.releaseReferenceProblems(valid), []);
    assert.equal(conventions.releaseReferenceProblems({ ...valid, imageDigest: "latest" }).length, 1);
    assert.equal(conventions.releaseReferenceProblems({ ...valid, gitSha: "abc123" }).length, 1);
    assert.equal(conventions.releaseReferenceProblems({ ...valid, chartVersion: "v1" }).length, 1);
    assert.equal(conventions.releaseReferenceProblems({}).length, 3);
});

test("CLI exits non-zero for an invalid app ID and prints derived names", () => {
    assert.throws(
        () => execFileSync(process.execPath, [cli, "app-id", "Demo_App"], { stdio: "pipe" }),
        (error) => error.status === 1 && /does not match/.test(error.stderr.toString()),
    );
    assert.doesNotThrow(() => execFileSync(process.execPath, [cli, "app-id", "affine"], { stdio: "pipe" }));
    const names = JSON.parse(execFileSync(process.execPath, [cli, "names", "affine", "staging"], { encoding: "utf8" }));
    assert.deepEqual(names, {
        stack: "organization/app-affine/staging",
        namespace: "affine-staging",
        host: "affine.<baseDomain>",
        availabilityAlert: "alert-affine-staging-availability",
    });
    assert.throws(
        () => execFileSync(process.execPath, [cli, "names", "Demo_App", "staging"], { stdio: "pipe" }),
        (error) => error.status === 1,
    );
});

// The platform README shows one naming example; it must match the conventions.
test("naming example in platform/README.md matches the conventions", () => {
    const readme = readFileSync(new URL("../README.md", import.meta.url), "utf8");
    const block = readme.match(/## Naming example[\s\S]*?```text\n([\s\S]*?)```/);
    assert.ok(block, "platform/README.md has no naming example");
    const example = Object.fromEntries(block[1].trim().split("\n").map((line) => line.split(/:\s+/)));
    const names = conventions.appNames("affine", "staging", "<baseDomain>");
    const foundation = conventions.foundationNames("staging");
    assert.deepEqual(example, {
        "app-id": "affine",
        stack: names.stack,
        namespace: names.namespace,
        host: names.host,
        "alert rule": conventions.alertRuleName("affine", "staging", "availability"),
        "foundation stack": foundation.stack,
        "foundation resource group": foundation.resourceGroup,
    });
});
