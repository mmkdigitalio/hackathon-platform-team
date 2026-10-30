// Foundation program against Pulumi mocks: output contract, secrets, tags, Contributor-only
// resources, catalog-generated monitoring and resource ownership.
import assert from "node:assert/strict";
import { before, test } from "node:test";
import { contractValidator, runFoundation, schema } from "./helpers/foundation-mocks.mjs";

let run;
before(async () => {
    run = await runFoundation();
});

const byType = (type) => run.resources.filter((r) => r.type === type && !r.read);
const one = (type, name) => {
    const found = byType(type).find((r) => r.name === name || r.logicalName === name);
    assert.ok(found, `${type} ${name} not registered`);
    return found;
};

test("stack outputs satisfy the output contract", () => {
    const validate = contractValidator();
    assert.ok(validate(run.outputs), JSON.stringify(validate.errors, null, 2));
});

test("spec: baseDomain is the ingress IP with dashes under sslip.io", () => {
    assert.equal(run.outputs.ingressIp, "20.79.1.2");
    assert.equal(run.outputs.baseDomain, "20-79-1-2.sslip.io");
    assert.equal(run.outputs.operationsApiHost, "operations-agent.20-79-1-2.sslip.io");
    assert.equal(run.outputs.publicScheme, "https");
    assert.deepEqual(run.outputs.modelApiKeys, { "azure-openai": "aoai-test-key" });
});

test("platformVersions reports the configured component versions", () => {
    assert.deepEqual(run.outputs.platformVersions, {
        kubernetes: "1.34.0",
        traefikChart: "42.1.3",
        certManagerChart: "v1.21.3",
        postgres: "16",
    });
});

test("program exports every contract output", () => {
    for (const key of Object.keys(schema.properties)) {
        assert.ok(key in run.program, `missing export ${key}`);
    }
});

test("secret outputs are Pulumi secrets, plain outputs are not", async () => {
    for (const [key, property] of Object.entries(schema.properties)) {
        assert.equal(await run.pulumi.isSecret(run.pulumi.output(run.program[key])), property["x-secret"] === true, key);
    }
});

test("spec: no role assignments, managed identities or Entra objects are created", () => {
    const forbidden = run.resources.filter((r) => /:(authorization|managedidentity|aad|azuread):/i.test(r.type));
    assert.deepEqual(forbidden.map((r) => r.type), []);
});

test("keys instead of roles: ACR admin user, AKS local accounts, system-assigned identity", () => {
    assert.equal(one("azure-native:containerregistry:Registry", "registry").inputs.adminUserEnabled, true);
    const aks = one("azure-native:containerservice:ManagedCluster", "aks");
    assert.equal(aks.inputs.disableLocalAccounts, false);
    assert.equal(aks.inputs.identity.type, "SystemAssigned");
    assert.equal(aks.inputs.aadProfile, undefined);
    assert.equal(aks.inputs.networkProfile.vnetSubnetID, undefined, "AKS-managed network, no subnet role needed");
    assert.equal(one("azure-native:storage:StorageAccount", "diagnostics").inputs.allowSharedKeyAccess, true);
});

test("every taggable Azure resource carries the four required tags", () => {
    const untaggedTypes = new Set([
        "azure-native:monitor:DataCollectionRuleAssociation",
        "azure-native:dbforpostgresql:FirewallRule",
        "azure-native:dbforpostgresql:Configuration",
        "azure-native:dbforpostgresql:Database",
        "azure-native:storage:BlobContainer",
        "azure-native:storage:ManagementPolicy",
        "azure-native:consumption:Budget",
    ]);
    const azureResources = run.resources.filter((r) => r.type.startsWith("azure-native:") && !untaggedTypes.has(r.type));
    assert.ok(azureResources.length >= 14, `only ${azureResources.length} taggable resources recorded`);
    for (const resource of azureResources) {
        const tags = resource.inputs.tags ?? {};
        const label = `${resource.type} ${resource.name}`;
        const expectedAppId = resource.name.match(/^(?:webtest|alert)-(.+)-staging-[a-z]+$/)?.[1] ?? "mmk-platform";
        assert.equal(tags["app-id"], expectedAppId, label);
        assert.equal(tags.environment, "staging", label);
        assert.equal(tags.stack, "organization/foundation/staging", label);
        assert.equal(tags["managed-by"], "pulumi", label);
    }
});

test("spec: catalog entries get an availability test and alert rule to ops-intake", () => {
    const test_ = one("azure-native:applicationinsights:WebTest", "webtest-affine-staging-availability");
    assert.equal(test_.inputs.request.requestUrl, "https://affine.20-79-1-2.sslip.io/info");
    assert.equal(test_.inputs.frequency, 300);
    assert.equal(test_.inputs.validationRules.sSLCheck, true);
    const rule = one("azure-native:monitor:MetricAlert", "alert-affine-staging-availability");
    assert.equal(rule.inputs.autoMitigate, true);
    assert.equal(rule.inputs.windowSize, "PT5M");
    assert.match(rule.inputs.actions[0].actionGroupId, /ops-intake$/);
    assert.deepEqual(rule.inputs.actions[0].webHookProperties, { "app-id": "affine", environment: "staging" });
});

test("spec: operations-agent never alerts itself through ops-intake", () => {
    const agentRules = byType("azure-native:monitor:MetricAlert").filter((r) => r.name.startsWith("alert-operations-agent-"));
    assert.deepEqual(agentRules.map((r) => r.name), ["alert-operations-agent-staging-healthz"]);
    assert.match(agentRules[0].inputs.actions[0].actionGroupId, /ops-humans$/);
    const healthz = one("azure-native:applicationinsights:WebTest", "webtest-operations-agent-staging-healthz");
    assert.equal(healthz.inputs.request.requestUrl, "https://operations-agent.20-79-1-2.sslip.io/healthz");
    assert.equal(healthz.inputs.frequency, 300);
    assert.equal(
        byType("azure-native:applicationinsights:WebTest").some((r) => r.name === "webtest-operations-agent-staging-availability"),
        false,
    );
});

test("ops-humans is e-mail only; ops-intake posts the common alert schema with the key", () => {
    const humans = one("azure-native:monitor:ActionGroup", "ops-humans");
    assert.equal(humans.inputs.webhookReceivers, undefined);
    assert.equal(humans.inputs.emailReceivers[0].emailAddress, "platform-team@example.org");
    const intake = one("azure-native:monitor:ActionGroup", "ops-intake");
    assert.equal(intake.inputs.webhookReceivers[0].useCommonAlertSchema, true);
    assert.equal(
        intake.inputs.webhookReceivers[0].serviceUri,
        "https://operations-agent.20-79-1-2.sslip.io/events/azure-monitor?key=generated-alert-webhook-key",
    );
});

test("budget covers the platform and AKS node resource groups and notifies ops-humans", () => {
    const budget = one("azure-native:consumption:Budget", "staging-budget");
    assert.deepEqual(budget.inputs.filter.dimensions.values, ["rg-mmk-platform-staging", "rg-mmk-platform-staging-aks-nodes"]);
    assert.match(budget.inputs.notifications.actual80.contactGroups[0], /ops-humans$/);
});

test("data: 7 days of backups, open firewall with TLS, operations database, 30-day diagnostics retention", () => {
    const server = one("azure-native:dbforpostgresql:Server", "postgres");
    assert.equal(server.inputs.backup.backupRetentionDays, 7);
    assert.equal(server.inputs.network.publicNetworkAccess, "Enabled");
    const firewall = one("azure-native:dbforpostgresql:FirewallRule", "postgres-allow-all-ipv4");
    assert.deepEqual([firewall.inputs.startIpAddress, firewall.inputs.endIpAddress], ["0.0.0.0", "255.255.255.255"]);
    assert.equal(one("azure-native:dbforpostgresql:Configuration", "postgres-require-tls").inputs.value, "on");
    assert.equal(one("azure-native:dbforpostgresql:Database", "operations").inputs.databaseName, "operations");
    const password = one("random:index/randomPassword:RandomPassword", "postgres-admin");
    assert.deepEqual([password.inputs.minUpper, password.inputs.minLower, password.inputs.minNumeric], [1, 1, 1]);
    const policy = one("azure-native:storage:ManagementPolicy", "diagnostics-retention");
    assert.equal(policy.inputs.policy.rules[0].definition.actions.baseBlob.delete.daysAfterModificationGreaterThan, 30);
});

test("ingress: Traefik without HTTPS redirect, Let's Encrypt HTTP-01 issuer on the Traefik class", () => {
    const traefik = one("kubernetes:helm.sh/v3:Release", "traefik");
    assert.equal(traefik.inputs.values.ingressClass.name, "traefik");
    assert.equal(traefik.inputs.values.ports, undefined, "no global redirect, HTTP stays available as fallback");
    const issuer = one("kubernetes:cert-manager.io/v1:ClusterIssuer", "letsencrypt");
    assert.deepEqual(issuer.inputs.spec.acme.solvers, [{ http01: { ingress: { ingressClassName: "traefik" } } }]);
    assert.equal(issuer.inputs.spec.acme.email, "platform-team@example.org");
    assert.equal(run.outputs.clusterIssuerName, "letsencrypt");
    assert.equal(run.outputs.ingressClassName, "traefik");
    const service = run.resources.find((r) => r.type === "kubernetes:core/v1:Service" && r.read);
    assert.equal(service.id, "platform-ingress/traefik", "baseDomain comes from the chart's LoadBalancer service");
});

test("container insights is enabled through a data collection rule without role assignment", () => {
    const aks = one("azure-native:containerservice:ManagedCluster", "aks");
    assert.equal(aks.inputs.addonProfiles.omsagent.enabled, true);
    assert.equal(byType("azure-native:monitor:DataCollectionRuleAssociation").length, 1);
});

test("foundation creates no app-owned Kubernetes resources", () => {
    const namespaces = byType("kubernetes:core/v1:Namespace").map((r) => r.inputs.metadata.name).sort();
    assert.deepEqual(namespaces, ["platform-certs", "platform-ingress"]);
    const appNamespaced = run.resources.filter((r) => /-(staging|production)$/.test(r.inputs?.metadata?.namespace ?? ""));
    assert.deepEqual(appNamespaced, []);
});

test("resources whose replacement loses data or breaks consumers are protected", () => {
    for (const key of [
        "azure-native:resources:ResourceGroup::platform",
        "azure-native:operationalinsights:Workspace::logs",
        "azure-native:containerregistry:Registry::registry",
        "azure-native:dbforpostgresql:Server::postgres",
        "azure-native:dbforpostgresql:Database::operations",
        "azure-native:storage:StorageAccount::diagnostics",
    ]) {
        assert.equal(run.resourceOptions.get(key)?.protect, true, key);
    }
    assert.equal(run.resourceOptions.get("azure-native:containerservice:ManagedCluster::aks")?.protect, false);
});

test("PostgreSQL child resources are created one after another", () => {
    const dependsOn = (key) => run.resourceOptions.get(`azure-native:dbforpostgresql:${key}`)?.dependsOn;
    assert.deepEqual(dependsOn("Configuration::postgres-require-tls"), ["postgres-allow-all-ipv4"]);
    assert.deepEqual(dependsOn("Database::operations"), ["postgres-require-tls"]);
});
