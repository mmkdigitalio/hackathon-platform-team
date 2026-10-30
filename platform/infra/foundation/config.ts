import * as path from "node:path";
import * as pulumi from "@pulumi/pulumi";
import { assertEnvironment, Environment } from "../components/conventions";

// Values that depend on the Azure subscription (region, capacity, AKS version,
// recipients, budget) have no defaults on purpose: they are chosen at bootstrap and
// recorded in Pulumi.<env>.yaml. See operations/foundation/bootstrap.md.
export interface FoundationConfig {
    env: Environment;
    location: string;
    kubernetesVersion: string;
    nodeVmSize: string;
    nodeCount: number;
    humansEmail: string;
    acmeEmail: string;
    acmeServer: string;
    /** Documented Let's Encrypt fallback: availability tests and ops-intake use http://. */
    httpFallback: boolean;
    containerInsights: boolean;
    budgetAmount: number;
    budgetStartDate: string;
    postgres: { version: string; skuName: string; skuTier: string; storageSizeGb: number };
    traefikChartVersion: string;
    certManagerChartVersion: string;
    catalogDir: string;
    modelApiKeys: pulumi.Output<Record<string, string>>;
    githubWebhookSecret: pulumi.Output<string>;
    opsGithubToken: pulumi.Output<string>;
}

// platform/catalog, seen from dist/infra/foundation/ after `npm run build`.
const defaultCatalogDir = path.resolve(__dirname, "../../../catalog");

export function loadConfig(): FoundationConfig {
    const config = new pulumi.Config();
    const env = pulumi.getStack();
    assertEnvironment(env);

    const budgetStartDate = config.require("budgetStartDate");
    if (!/^\d{4}-\d{2}-01$/.test(budgetStartDate)) {
        throw new Error(`budgetStartDate must be the first day of a month (YYYY-MM-01), got "${budgetStartDate}".`);
    }
    const humansEmail = config.require("humansEmail");
    const modelApiKeys = config.requireSecretObject<Record<string, string>>("modelApiKeys").apply((keys) => {
        const entries = Object.entries(keys ?? {});
        if (entries.length === 0 || entries.some(([, value]) => typeof value !== "string" || value === "")) {
            throw new Error("modelApiKeys needs at least one provider key, e.g. modelApiKeys.azure-openai (non-empty strings).");
        }
        return keys;
    });

    return {
        env,
        location: config.require("location"),
        kubernetesVersion: config.require("kubernetesVersion"),
        nodeVmSize: config.require("nodeVmSize"),
        nodeCount: config.requireNumber("nodeCount"),
        humansEmail,
        acmeEmail: config.get("acmeEmail") ?? humansEmail,
        acmeServer: config.get("acmeServer") ?? "https://acme-v02.api.letsencrypt.org/directory",
        httpFallback: config.getBoolean("httpFallback") ?? false,
        containerInsights: config.getBoolean("containerInsights") ?? true,
        budgetAmount: config.requireNumber("budgetAmount"),
        budgetStartDate,
        postgres: {
            version: config.get("postgresVersion") ?? "16",
            skuName: config.get("postgresSkuName") ?? "Standard_B1ms",
            skuTier: config.get("postgresSkuTier") ?? "Burstable",
            storageSizeGb: config.getNumber("postgresStorageGb") ?? 32,
        },
        traefikChartVersion: config.get("traefikChartVersion") ?? "42.1.3",
        certManagerChartVersion: config.get("certManagerChartVersion") ?? "v1.21.3",
        catalogDir: config.get("catalogDir") ?? defaultCatalogDir,
        modelApiKeys,
        githubWebhookSecret: config.requireSecret("githubWebhookSecret"),
        opsGithubToken: config.requireSecret("opsGithubToken"),
    };
}
