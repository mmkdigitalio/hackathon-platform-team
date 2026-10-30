import * as azure from "@pulumi/azure-native";
import * as pulumi from "@pulumi/pulumi";
import {
    alertRuleName,
    availabilityTestName,
    foundationNames,
    operationsAgentAppId,
    platformAppId,
    requiredTags,
} from "../components/conventions";
import { CatalogEntry } from "./catalog";
import { FoundationConfig } from "./config";

// Availability test locations in Europe (Application Insights location IDs).
const webTestLocations = [
    "emea-nl-ams-azr",
    "emea-gb-db3-azr",
    "emea-fr-pra-edge",
    "emea-ch-zrh-edge",
    "emea-se-sto-edge",
];

export interface MonitoringArgs {
    cfg: FoundationConfig;
    subscriptionId: pulumi.Input<string>;
    resourceGroupName: pulumi.Input<string>;
    appInsights: azure.applicationinsights.Component;
    baseDomain: pulumi.Input<string>;
    operationsApiHost: pulumi.Input<string>;
    alertWebhookKey: pulumi.Input<string>;
    catalog: CatalogEntry[];
}

// Alert targets, the staging budget and every availability test with its alert rule.
// All of them are Azure resources and therefore belong to the foundation stack (E5);
// app stacks stay free of Azure resources.
export function createMonitoring(args: MonitoringArgs) {
    const { cfg, subscriptionId, resourceGroupName, appInsights } = args;
    const { env } = cfg;
    const stack = foundationNames(env).stack;
    const platformTags = requiredTags(platformAppId, env, stack);
    const scheme = cfg.httpFallback ? "http" : "https";

    const intake = new azure.monitor.ActionGroup("ops-intake", {
        actionGroupName: "ops-intake",
        groupShortName: "ops-intake",
        resourceGroupName,
        location: "global",
        enabled: true,
        webhookReceivers: [
            {
                name: "operations-agent-intake",
                serviceUri: pulumi.secret(
                    pulumi.interpolate`${scheme}://${args.operationsApiHost}/events/azure-monitor?key=${args.alertWebhookKey}`,
                ),
                useCommonAlertSchema: true,
            },
        ],
        tags: platformTags,
    });

    // E-mail only; never a webhook to the agent, so humans hear about agent outages.
    const humans = new azure.monitor.ActionGroup("ops-humans", {
        actionGroupName: "ops-humans",
        groupShortName: "ops-humans",
        resourceGroupName,
        location: "global",
        enabled: true,
        emailReceivers: [
            { name: "team-distribution-list", emailAddress: cfg.humansEmail, useCommonAlertSchema: true },
        ],
        tags: platformTags,
    });

    // Subscription scope filtered to the platform resource group and the AKS node
    // resource group, so the cluster's VM costs are included (decisions.md).
    const platformResourceGroup = foundationNames(env).resourceGroup;
    new azure.consumption.Budget("staging-budget", {
        budgetName: `budget-mmk-platform-${env}`,
        scope: pulumi.interpolate`/subscriptions/${subscriptionId}`,
        amount: cfg.budgetAmount,
        category: "Cost",
        timeGrain: "Monthly",
        timePeriod: { startDate: `${cfg.budgetStartDate}T00:00:00Z` },
        filter: {
            dimensions: {
                name: "ResourceGroupName",
                operator: "In",
                values: [platformResourceGroup, `${platformResourceGroup}-aks-nodes`],
            },
        },
        notifications: {
            actual80: {
                enabled: true,
                operator: "GreaterThan",
                threshold: 80,
                thresholdType: "Actual",
                contactEmails: [cfg.humansEmail],
                contactGroups: [humans.id],
            },
            forecast100: {
                enabled: true,
                operator: "GreaterThan",
                threshold: 100,
                thresholdType: "Forecasted",
                contactEmails: [cfg.humansEmail],
                contactGroups: [humans.id],
            },
        },
    });

    // Standard test every five minutes plus a metric alert that fires when two
    // locations fail and resolves automatically (autoMitigate), so Resolved events exist.
    function availabilityCheck(appId: string, check: string, url: pulumi.Input<string>, actionGroup: azure.monitor.ActionGroup) {
        const tags = requiredTags(appId, env, stack);
        const testName = availabilityTestName(appId, env, check);
        const ruleName = alertRuleName(appId, env, check);
        const test = new azure.applicationinsights.WebTest(testName, {
            webTestName: testName,
            syntheticMonitorId: testName,
            resourceGroupName,
            location: cfg.location,
            kind: "standard",
            webTestKind: "standard",
            enabled: true,
            frequency: 300,
            timeout: 30,
            retryEnabled: true,
            locations: webTestLocations.map((id) => ({ location: id })),
            request: { requestUrl: url, httpVerb: "GET", parseDependentRequests: false },
            validationRules: cfg.httpFallback
                ? { expectedHttpStatusCode: 200 }
                : { expectedHttpStatusCode: 200, sSLCheck: true, sSLCertRemainingLifetimeCheck: 7 },
            tags: appInsights.id.apply((id) => ({ ...tags, [`hidden-link:${id}`]: "Resource" })),
        });
        new azure.monitor.MetricAlert(ruleName, {
            ruleName,
            resourceGroupName,
            location: "global",
            description: pulumi.interpolate`Environment ${env}, app ${appId}: ${url} failed from at least two locations (test ${testName}).`,
            severity: 1,
            enabled: true,
            scopes: [test.id, appInsights.id],
            evaluationFrequency: "PT1M",
            windowSize: "PT5M",
            autoMitigate: true,
            criteria: {
                odataType: "Microsoft.Azure.Monitor.WebtestLocationAvailabilityCriteria",
                webTestId: test.id,
                componentId: appInsights.id,
                failedLocationCount: 2,
            },
            actions: [{ actionGroupId: actionGroup.id, webHookProperties: { "app-id": appId, environment: env } }],
            tags,
        });
        return ruleName;
    }

    // operations-agent is excluded: it must not alert itself through ops-intake.
    const appAlertRules = args.catalog
        .filter((entry) => entry.appId !== operationsAgentAppId)
        .map((entry) =>
            availabilityCheck(
                entry.appId,
                "availability",
                pulumi.interpolate`${scheme}://${entry.appId}.${args.baseDomain}${entry.availabilityPath}`,
                intake,
            ),
        );

    // Independent outage signal for the agent (E10): /healthz answers 503 when the
    // database is unreachable or the worker heartbeat is older than ten minutes.
    const healthzAlertRule = availabilityCheck(
        operationsAgentAppId,
        "healthz",
        pulumi.interpolate`${scheme}://${args.operationsApiHost}/healthz`,
        humans,
    );

    return { intake, humans, appAlertRules, healthzAlertRule };
}
