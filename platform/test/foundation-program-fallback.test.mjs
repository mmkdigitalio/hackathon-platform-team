// Foundation program with the documented staging fallbacks: HTTP instead of HTTPS when
// Let's Encrypt does not issue certificates, and Container Insights switched off.
import assert from "node:assert/strict";
import { before, test } from "node:test";
import { contractValidator, runFoundation } from "./helpers/foundation-mocks.mjs";

let run;
before(async () => {
    // Also the first deployment without the agent PAT (DEFER_PAT, bootstrap.md).
    run = await runFoundation({ httpFallback: "true", containerInsights: "false", opsGithubToken: undefined });
});

const webTest = (name) =>
    run.resources.find((r) => r.type === "azure-native:applicationinsights:WebTest" && r.name === name);

test("outputs still satisfy the contract and announce the http scheme", () => {
    const validate = contractValidator();
    assert.ok(validate(run.outputs), JSON.stringify(validate.errors, null, 2));
    assert.equal(run.outputs.publicScheme, "http");
});

test("availability tests and ops-intake use http without certificate checks", () => {
    const affine = webTest("webtest-affine-staging-availability");
    assert.equal(affine.inputs.request.requestUrl, "http://affine.20-79-1-2.sslip.io/info");
    assert.equal(affine.inputs.validationRules.sSLCheck, undefined);
    assert.equal(
        webTest("webtest-operations-agent-staging-healthz").inputs.request.requestUrl,
        "http://operations-agent.20-79-1-2.sslip.io/healthz",
    );
    const intake = run.resources.find((r) => r.type === "azure-native:monitor:ActionGroup" && r.name === "ops-intake");
    assert.match(intake.inputs.webhookReceivers[0].serviceUri, /^http:\/\/operations-agent\./);
});

test("without Container Insights there is no add-on and no data collection rule", () => {
    const aks = run.resources.find((r) => r.type === "azure-native:containerservice:ManagedCluster");
    assert.equal(aks.inputs.addonProfiles, undefined);
    assert.equal(run.resources.some((r) => r.type.startsWith("azure-native:monitor:DataCollectionRule")), false);
});

test("without opsGithubToken the contract output is an empty secret", () => {
    assert.equal(run.outputs.opsGithubToken, "");
});
