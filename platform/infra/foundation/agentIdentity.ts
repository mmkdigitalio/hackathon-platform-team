// Read-only Azure identity for the operations agent's workspace pod (opt-in, agentWorkspaceIdentity).
// AKS workload identity without the webhook: the agent's code projects the service account token itself,
// so the cluster only needs its OIDC issuer. Role assignments need Owner or User Access Administrator;
// with the switch off the stack keeps the Contributor-only rule of E3.
import * as azure from "@pulumi/azure-native";
import * as pulumi from "@pulumi/pulumi";
import * as random from "@pulumi/random";

// Built-in role definitions, read only: Reader cannot list keys, Log Analytics Reader adds workspace queries.
export const readerRoleId = "acdd72a7-3385-48ef-bd42-f606fba81ae7";
export const logAnalyticsReaderRoleId = "73c42c96-874c-492b-b04d-ab87d138a893";
export const workspaceServiceAccount = "operations-agent-workspace";

interface AgentIdentityArgs {
    env: string;
    location: string;
    subscriptionId: pulumi.Input<string>;
    resourceGroupName: pulumi.Input<string>;
    nodeResourceGroupName: pulumi.Input<string>;
    logAnalyticsWorkspaceId: pulumi.Input<string>;
    oidcIssuerUrl: pulumi.Input<string>;
    /** Namespace of the agent's release, <app-id>-<env>. */
    agentNamespace: string;
    tags: Record<string, string>;
}

export function createAgentIdentity(args: AgentIdentityArgs) {
    const identity = new azure.managedidentity.UserAssignedIdentity("agent-workspace-identity", {
        resourceName: `id-mmk-ops-workspace-${args.env}`,
        resourceGroupName: args.resourceGroupName,
        location: args.location,
        tags: args.tags,
    });

    const federatedCredential = new azure.managedidentity.FederatedIdentityCredential("agent-workspace-federation", {
        federatedIdentityCredentialResourceName: workspaceServiceAccount,
        resourceName: identity.name,
        resourceGroupName: args.resourceGroupName,
        issuer: args.oidcIssuerUrl,
        subject: `system:serviceaccount:${args.agentNamespace}:${workspaceServiceAccount}`,
        audiences: ["api://AzureADTokenExchange"],
    });

    const rgScope = (name: pulumi.Input<string>) =>
        pulumi.interpolate`/subscriptions/${args.subscriptionId}/resourceGroups/${name}`;
    const assign = (name: string, scope: pulumi.Input<string>, roleId: string) =>
        new azure.authorization.RoleAssignment(name, {
            // Names are GUIDs; keep them stable in state.
            roleAssignmentName: new random.RandomUuid(`${name}-name`).result,
            scope,
            principalId: identity.principalId,
            principalType: "ServicePrincipal",
            roleDefinitionId: pulumi.interpolate`/subscriptions/${args.subscriptionId}/providers/Microsoft.Authorization/roleDefinitions/${roleId}`,
        });

    const roleAssignments = [
        assign("agent-workspace-reader-platform", rgScope(args.resourceGroupName), readerRoleId),
        assign("agent-workspace-reader-nodes", rgScope(args.nodeResourceGroupName), readerRoleId),
        assign("agent-workspace-log-analytics-reader", args.logAnalyticsWorkspaceId, logAnalyticsReaderRoleId),
    ];

    return { identity, federatedCredential, roleAssignments };
}
