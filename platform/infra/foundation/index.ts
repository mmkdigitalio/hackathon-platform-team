// Foundation stack organization/foundation/<env>: shared runtime, data and monitoring.
// Runs locally under `az login` of a Contributor (E4); stack outputs form the versioned
// contract in schemas/foundation-outputs/.
import * as azure from "@pulumi/azure-native";
import * as k8s from "@pulumi/kubernetes";
import * as pulumi from "@pulumi/pulumi";
import * as random from "@pulumi/random";
import { foundationNames, operationsAgentAppId, platformAppId, requiredTags } from "../components/conventions";
import { readCatalog } from "./catalog";
import { loadConfig } from "./config";
import { createData, diagnosticsContainerName, postgresAdminLogin, postgresDatabaseName } from "./data";
import { clusterIssuerName, createIngress, ingressClassName } from "./ingress";
import { createMonitoring } from "./monitoring";
import { createRuntime } from "./runtime";

/** Increment on every incompatible change of the outputs below (rename or removal). */
const contractVersion = 1;

const cfg = loadConfig();
const catalog = readCatalog(cfg.catalogDir);
const names = foundationNames(cfg.env);
const tags = requiredTags(platformAppId, cfg.env, names.stack);
const subscriptionId = azure.authorization.getClientConfigOutput().subscriptionId;

// Suffix for globally unique names (registry, storage, database server); stable in state.
const suffix = new random.RandomString("global-name-suffix", {
    length: 6,
    upper: false,
    special: false,
}).result;

const runtime = createRuntime({ cfg, resourceGroupName: names.resourceGroup, suffix, tags });
const resourceGroupName = runtime.resourceGroup.name;
const data = createData({ cfg, resourceGroupName, suffix, tags });

const provider = new k8s.Provider("aks", { kubeconfig: runtime.kubeconfig });
const ingress = createIngress({ cfg, provider });
const operationsApiHost = pulumi.interpolate`${operationsAgentAppId}.${ingress.baseDomain}`;

const generatedKey = (name: string) => new random.RandomPassword(name, { length: 40, special: false }).result;
const alertKey = generatedKey("alert-webhook-key");
const maintenanceKey = generatedKey("maintenance-api-key");

const monitoring = createMonitoring({
    cfg,
    subscriptionId,
    resourceGroupName,
    appInsights: runtime.appInsights,
    baseDomain: ingress.baseDomain,
    operationsApiHost,
    alertWebhookKey: alertKey,
    catalog,
});

function required<T>(value: T | undefined | null, what: string): T {
    if (value === undefined || value === null || value === "") {
        throw new Error(`Azure did not report ${what}.`);
    }
    return value;
}

// ---- Output contract, contractVersion 1 (schemas/foundation-outputs/) ----
export { contractVersion, resourceGroupName, operationsApiHost, ingressClassName, clusterIssuerName };
export const environment = cfg.env;
export const location = cfg.location;
export const baseDomain = ingress.baseDomain;
export const ingressIp = ingress.ingressIp;
export const publicScheme = cfg.httpFallback ? "http" : "https";
export const aksClusterName = runtime.cluster.name;
export const kubeconfig = runtime.kubeconfig;
export const acrLoginServer = runtime.registry.loginServer.apply((server) => required(server, "the ACR login server"));
export const acrUsername = runtime.acrUsername;
export const acrPassword = runtime.acrPassword;
export const appInsightsConnectionString = runtime.appInsights.connectionString.apply((value) =>
    required(value, "the Application Insights connection string"),
);
export const actionGroupIntakeId = monitoring.intake.id;
export const actionGroupHumansId = monitoring.humans.id;
export const postgresHost = data.postgres.fullyQualifiedDomainName.apply((host) => required(host, "the PostgreSQL host"));
export const postgresAdminUser = postgresAdminLogin;
export const postgresAdminPassword = pulumi.secret(data.postgresPassword);
export const postgresOperationsDatabase = data.database.name.apply(() => postgresDatabaseName);
export const diagnosticsStorageAccount = data.diagnosticsAccount.name;
export const diagnosticsContainer = data.diagnosticsContainer.name.apply(() => diagnosticsContainerName);
export const diagnosticsStorageKey = data.diagnosticsStorageKey;
export const alertWebhookKey = pulumi.secret(alertKey);
export const maintenanceApiKey = pulumi.secret(maintenanceKey);
export const githubWebhookSecret = pulumi.secret(cfg.githubWebhookSecret);
export const modelApiKeys = pulumi.secret(cfg.modelApiKeys);
export const opsGithubToken = pulumi.secret(cfg.opsGithubToken);
export const platformVersions = {
    kubernetes: cfg.kubernetesVersion,
    traefikChart: cfg.traefikChartVersion,
    certManagerChart: cfg.certManagerChartVersion,
    postgres: cfg.postgres.version,
};
