// Runs the compiled foundation program against Pulumi mocks (no Azure or Kubernetes
// access) and records every registered resource with its inputs and options. Pulumi
// mocks are process-wide, so each test file runs the program once.
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import Ajv2020 from "ajv/dist/2020.js";

const require = createRequire(import.meta.url);

export const subscriptionId = "00000000-0000-0000-0000-000000000000";
export const ingressIp = "20.79.1.2";
export const schema = JSON.parse(
    readFileSync(new URL("../../schemas/foundation-outputs/foundation-outputs.schema.json", import.meta.url), "utf8"),
);

export const baseConfig = {
    location: "germanywestcentral",
    kubernetesVersion: "1.34.0",
    nodeVmSize: "Standard_D4s_v5",
    nodeCount: "2",
    // A comma-separated team list; whitespace and a repeated address are tolerated.
    humansEmail: "platform-team@example.org, oncall@example.org,platform-team@example.org",
    budgetAmount: "500",
    budgetStartDate: "2026-11-01",
    catalogDir: new URL("../fixtures/catalog", import.meta.url).pathname,
    modelApiKeys: JSON.stringify({ "azure-openai": "aoai-test-key" }),
    githubWebhookSecret: "github-webhook-secret-value",
    opsGithubToken: "github_pat_test_value",
};

// Input property that carries the Azure name, per resource type.
const nameInputs = {
    "azure-native:resources:ResourceGroup": "resourceGroupName",
    "azure-native:operationalinsights:Workspace": "workspaceName",
    "azure-native:applicationinsights:Component": "resourceName",
    "azure-native:applicationinsights:WebTest": "webTestName",
    "azure-native:containerregistry:Registry": "registryName",
    "azure-native:containerservice:ManagedCluster": "resourceName",
    "azure-native:dbforpostgresql:Server": "serverName",
    "azure-native:dbforpostgresql:Database": "databaseName",
    "azure-native:storage:StorageAccount": "accountName",
    "azure-native:storage:BlobContainer": "containerName",
    "azure-native:monitor:ActionGroup": "actionGroupName",
    "azure-native:monitor:MetricAlert": "ruleName",
    "azure-native:monitor:DataCollectionRule": "dataCollectionRuleName",
};

function computedOutputs(type, name) {
    switch (type) {
        case "azure-native:applicationinsights:Component":
            return { connectionString: "InstrumentationKey=33333333-3333-3333-3333-333333333333" };
        case "azure-native:containerregistry:Registry":
            return { loginServer: `${name}.azurecr.io` };
        case "azure-native:dbforpostgresql:Server":
            return { fullyQualifiedDomainName: `${name}.postgres.database.azure.com` };
        case "kubernetes:helm.sh/v3:Release":
            return { status: { name, namespace: "platform-ingress", status: "deployed" } };
        case "kubernetes:core/v1:Service":
            return { status: { loadBalancer: { ingress: [{ ip: ingressIp }] } } };
        case "random:index/randomString:RandomString":
            return { result: "ab12cd" };
        case "random:index/randomPassword:RandomPassword":
            return { result: `generated-${name}` };
        default:
            return {};
    }
}

// Mocked inputs keep secret markers; unwrap them for assertions.
const secretSig = "4dabf18193072939515e22adb298388d";
function unwrap(value) {
    if (Array.isArray(value)) return value.map(unwrap);
    if (value && typeof value === "object") {
        if (secretSig in value) return unwrap(value.value);
        return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, unwrap(v)]));
    }
    return value;
}

export async function runFoundation(overrides = {}) {
    const config = { ...baseConfig, ...overrides };
    process.env.PULUMI_CONFIG = JSON.stringify(
        Object.fromEntries(Object.entries(config).map(([key, value]) => [`foundation:${key}`, value])),
    );
    // modelApiKeys, githubWebhookSecret and opsGithubToken are secret config values.
    process.env.PULUMI_CONFIG_SECRET_KEYS = JSON.stringify([
        "foundation:modelApiKeys",
        "foundation:githubWebhookSecret",
        "foundation:opsGithubToken",
    ]);

    const resources = [];
    // Resource options per Pulumi type and logical name; the mock monitor does not see them.
    const resourceOptions = new Map();
    const pulumi = require("@pulumi/pulumi");
    const runtimeResource = require("@pulumi/pulumi/runtime/resource");
    const registerResource = runtimeResource.registerResource;
    runtimeResource.registerResource = (res, parent, type, name, ...rest) => {
        const opts = rest[4] ?? {};
        const dependsOn = [opts.dependsOn ?? []].flat().map((dependency) => dependency.__name);
        resourceOptions.set(`${type}::${name}`, { protect: opts.protect === true, dependsOn });
        return registerResource(res, parent, type, name, ...rest);
    };
    await pulumi.runtime.setMocks(
        {
            newResource(args) {
                const nameKey = nameInputs[args.type];
                const name = (nameKey && args.inputs[nameKey]) || args.name;
                resources.push({ type: args.type, logicalName: args.name, name, inputs: unwrap(args.inputs), read: Boolean(args.id), id: args.id });
                const id = args.id || `/subscriptions/${subscriptionId}/resourceGroups/rg-mmk-platform-staging/providers/${args.type}/${name}`;
                return { id, state: { ...args.inputs, name, ...computedOutputs(args.type, name) } };
            },
            call(args) {
                switch (args.token) {
                    case "azure-native:authorization:getClientConfig":
                        return { subscriptionId, tenantId: "tenant", clientId: "client", objectId: "object" };
                    case "azure-native:containerservice:listManagedClusterAdminCredentials":
                        return { kubeconfigs: [{ name: "clusterAdmin", value: Buffer.from("apiVersion: v1\nkind: Config\n").toString("base64") }] };
                    case "azure-native:containerregistry:listRegistryCredentials":
                        return { username: "acrmmkplatformstagingab12cd", passwords: [{ name: "password", value: "acr-admin-password" }] };
                    case "azure-native:storage:listStorageAccountKeys":
                        return { keys: [{ keyName: "key1", value: "diagnostics-storage-key", permissions: "FULL" }] };
                    default:
                        throw new Error(`Unmocked call ${args.token}`);
                }
            },
        },
        "foundation",
        "staging",
        false,
    );
    const program = require("../../dist/infra/foundation/index.js");
    const resolve = (value) =>
        pulumi.Output.isInstance(value) ? new Promise((done) => value.apply(done)) : Promise.resolve(value);
    const outputs = Object.fromEntries(
        await Promise.all(Object.keys(program).map(async (key) => [key, await resolve(program[key])])),
    );
    await registrationsSettled(resources);
    return { program, outputs, resources, resourceOptions, pulumi };
}

// Registrations that depend on other outputs (e.g. the Kubernetes provider) arrive
// asynchronously; wait until the recorded set stops growing.
async function registrationsSettled(resources) {
    let stableRounds = 0;
    let last = -1;
    for (let i = 0; i < 400 && stableRounds < 20; i++) {
        await new Promise((done) => setTimeout(done, 5));
        stableRounds = resources.length === last ? stableRounds + 1 : 0;
        last = resources.length;
    }
}

export function contractValidator() {
    const ajv = new Ajv2020({ strict: true, allErrors: true });
    ajv.addKeyword("x-secret");
    ajv.addFormat("ipv4", /^(\d{1,3}\.){3}\d{1,3}$/);
    return ajv.compile(schema);
}
