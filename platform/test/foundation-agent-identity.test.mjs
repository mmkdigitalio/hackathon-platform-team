// Opt-in agentWorkspaceIdentity: read-only Azure identity for the agent workspace, federated to its
// service account. Off by default (E3); see foundation-program.test.mjs for the Contributor-only stack.
import assert from "node:assert/strict";
import { before, test } from "node:test";
import { contractValidator, runFoundation } from "./helpers/foundation-mocks.mjs";

let run;
before(async () => {
    run = await runFoundation({ agentWorkspaceIdentity: "true" });
});

const of = (type) => run.resources.filter((r) => r.type === type && !r.read);

test("outputs still satisfy the contract and carry the workspace identity", () => {
    const validate = contractValidator();
    assert.ok(validate(run.outputs), JSON.stringify(validate.errors, null, 2));
    assert.deepEqual(run.outputs.agentWorkspaceAzure, {
        clientId: "44444444-4444-4444-4444-444444444444",
        tenantId: "tenant",
        subscriptionId: run.outputs.agentWorkspaceAzure.subscriptionId,
    });
});

test("one identity federated to the workspace service account of the agent namespace", () => {
    const [identity] = of("azure-native:managedidentity:UserAssignedIdentity");
    assert.equal(identity.name, "id-mmk-ops-workspace-staging");
    const [fic] = of("azure-native:managedidentity:FederatedIdentityCredential");
    assert.equal(fic.inputs.subject, "system:serviceaccount:operations-agent-staging:operations-agent-workspace");
    assert.deepEqual(fic.inputs.audiences, ["api://AzureADTokenExchange"]);
    assert.equal(fic.inputs.issuer, "https://westeurope.oic.prod-aks.azure.com/tenant/cluster/");
    const aks = of("azure-native:containerservice:ManagedCluster")[0];
    assert.deepEqual(aks.inputs.oidcIssuerProfile, { enabled: true });
});

test("only read roles: Reader on both resource groups, Log Analytics Reader on the workspace", () => {
    const roles = of("azure-native:authorization:RoleAssignment").map((r) => [r.inputs.roleDefinitionId.split("/").pop(), r.inputs.scope.split("/").pop()]);
    assert.deepEqual(roles.sort(), [
        ["73c42c96-874c-492b-b04d-ab87d138a893", "log-mmk-platform-staging"],
        ["acdd72a7-3385-48ef-bd42-f606fba81ae7", "rg-mmk-platform-staging"],
        ["acdd72a7-3385-48ef-bd42-f606fba81ae7", "rg-mmk-platform-staging-aks-nodes"],
    ].sort());
    for (const r of of("azure-native:authorization:RoleAssignment")) assert.equal(r.inputs.principalType, "ServicePrincipal");
});
