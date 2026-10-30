import * as azure from "@pulumi/azure-native";
import * as pulumi from "@pulumi/pulumi";
import { FoundationConfig } from "./config";

// Streams collected by Container Insights. ContainerLogV2 carries stdout/stderr of all
// namespaces; only humans query it, neither the agent nor its outage alert depends on it.
const containerInsightsStreams = [
    "Microsoft-ContainerLogV2",
    "Microsoft-KubeEvents",
    "Microsoft-KubePodInventory",
    "Microsoft-KubeNodeInventory",
    "Microsoft-KubeServices",
    "Microsoft-KubePVInventory",
    "Microsoft-KubeMonAgentEvents",
    "Microsoft-ContainerInventory",
    "Microsoft-ContainerNodeInventory",
    "Microsoft-InsightsMetrics",
    "Microsoft-Perf",
];

export interface RuntimeArgs {
    cfg: FoundationConfig;
    resourceGroupName: string;
    suffix: pulumi.Input<string>;
    tags: Record<string, string>;
}

function required<T>(value: T | undefined | null, what: string): T {
    if (value === undefined || value === null || value === "") {
        throw new Error(`Azure did not report ${what}.`);
    }
    return value;
}

// Everything here works with Contributor rights alone: no role assignments, no Entra
// integration. Access goes through keys (ACR admin user, admin kubeconfig).
export function createRuntime({ cfg, resourceGroupName, suffix, tags }: RuntimeArgs) {
    const { env, location } = cfg;

    // Resources whose replacement would lose data or break consumers are protected: a
    // config change that would replace them fails instead (operations/foundation/operations.md).
    const keep = { protect: true };
    const resourceGroup = new azure.resources.ResourceGroup(
        "platform",
        { resourceGroupName, location, tags },
        keep,
    );

    const workspace = new azure.operationalinsights.Workspace(
        "logs",
        {
            workspaceName: `log-mmk-platform-${env}`,
            resourceGroupName: resourceGroup.name,
            location,
            sku: { name: "PerGB2018" },
            retentionInDays: 30,
            tags,
        },
        keep,
    );

    const appInsights = new azure.applicationinsights.Component("app-insights", {
        resourceName: `appi-mmk-platform-${env}`,
        resourceGroupName: resourceGroup.name,
        location,
        kind: "web",
        applicationType: "web",
        ingestionMode: "LogAnalytics",
        workspaceResourceId: workspace.id,
        tags,
    });

    // Admin user instead of AcrPush/AcrPull role assignments (design decision 3).
    const registry = new azure.containerregistry.Registry(
        "registry",
        {
            registryName: pulumi.interpolate`acrmmkplatform${env}${suffix}`,
            resourceGroupName: resourceGroup.name,
            location,
            sku: { name: "Basic" },
            adminUserEnabled: true,
            tags,
        },
        keep,
    );
    const registryCredentials = azure.containerregistry.listRegistryCredentialsOutput({
        resourceGroupName: resourceGroup.name,
        registryName: registry.name,
    });

    // System-assigned identity and the AKS-managed network in the node resource group:
    // creating the cluster needs no role assignment.
    const clusterName = `aks-mmk-platform-${env}`;
    const cluster = new azure.containerservice.ManagedCluster("aks", {
        resourceName: clusterName,
        resourceGroupName: resourceGroup.name,
        location,
        dnsPrefix: `mmk-platform-${env}`,
        nodeResourceGroup: `${resourceGroupName}-aks-nodes`,
        kubernetesVersion: cfg.kubernetesVersion,
        identity: { type: "SystemAssigned" },
        sku: { name: "Base", tier: "Free" },
        enableRBAC: true,
        // The admin kubeconfig is the access path for workflows (public API server, local accounts enabled).
        disableLocalAccounts: false,
        agentPoolProfiles: [
            {
                name: "system",
                mode: "System",
                osType: "Linux",
                type: "VirtualMachineScaleSets",
                vmSize: cfg.nodeVmSize,
                count: cfg.nodeCount,
                orchestratorVersion: cfg.kubernetesVersion,
            },
        ],
        networkProfile: {
            networkPlugin: "azure",
            networkPluginMode: "overlay",
            loadBalancerSku: "standard",
        },
        // Upgrades are explicit: change kubernetesVersion in the stack config (operations/foundation/operations.md).
        autoUpgradeProfile: { upgradeChannel: "none" },
        addonProfiles: cfg.containerInsights
            ? {
                  omsagent: {
                      enabled: true,
                      config: { logAnalyticsWorkspaceResourceID: workspace.id, useAADAuth: "true" },
                  },
              }
            : undefined,
        tags,
    });

    if (cfg.containerInsights) {
        const containerInsightsRule = new azure.monitor.DataCollectionRule("container-insights", {
            dataCollectionRuleName: `MSCI-${location}-${clusterName}`,
            resourceGroupName: resourceGroup.name,
            location,
            kind: "Linux",
            dataSources: {
                extensions: [
                    {
                        name: "ContainerInsightsExtension",
                        extensionName: "ContainerInsights",
                        streams: containerInsightsStreams,
                        extensionSettings: {
                            dataCollectionSettings: {
                                interval: "1m",
                                namespaceFilteringMode: "Off",
                                enableContainerLogV2: true,
                            },
                        },
                    },
                ],
            },
            destinations: { logAnalytics: [{ name: "ciworkspace", workspaceResourceId: workspace.id }] },
            dataFlows: [{ streams: containerInsightsStreams, destinations: ["ciworkspace"] }],
            tags,
        });
        new azure.monitor.DataCollectionRuleAssociation("container-insights", {
            associationName: "ContainerInsightsExtension",
            resourceUri: cluster.id,
            dataCollectionRuleId: containerInsightsRule.id,
        });
    }

    const adminCredentials = azure.containerservice.listManagedClusterAdminCredentialsOutput({
        resourceGroupName: resourceGroup.name,
        resourceName: cluster.name,
    });
    const kubeconfig = pulumi.secret(
        adminCredentials.kubeconfigs.apply((configs) =>
            Buffer.from(required(configs[0]?.value, "an admin kubeconfig"), "base64").toString("utf8"),
        ),
    );

    return {
        resourceGroup,
        workspace,
        appInsights,
        registry,
        acrUsername: registryCredentials.apply((credentials) => required(credentials.username, "the ACR admin user")),
        acrPassword: pulumi.secret(
            registryCredentials.apply((credentials) =>
                required(credentials.passwords?.[0]?.value, "an ACR admin password"),
            ),
        ),
        cluster,
        kubeconfig,
    };
}
