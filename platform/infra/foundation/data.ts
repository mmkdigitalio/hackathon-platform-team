import * as azure from "@pulumi/azure-native";
import * as pulumi from "@pulumi/pulumi";
import * as random from "@pulumi/random";
import { FoundationConfig } from "./config";

export const postgresDatabaseName = "operations";
export const postgresAdminLogin = "opsadmin";
export const diagnosticsContainerName = "diagnostics";

export interface DataArgs {
    cfg: FoundationConfig;
    resourceGroupName: pulumi.Input<string>;
    suffix: pulumi.Input<string>;
    tags: Record<string, string>;
}

// Agent persistence outside the cluster and the diagnostics artifact store. The
// foundation owns server, database and access; tables and migrations belong to strand C.
// App stacks may add databases and logins over the PostgreSQL protocol with the admin login.
export function createData({ cfg, resourceGroupName, suffix, tags }: DataArgs) {
    const { env, location } = cfg;
    const keepData = { protect: true };

    const postgresPassword = new random.RandomPassword("postgres-admin", {
        length: 32,
        special: true,
        overrideSpecial: "-_",
    });

    const postgres = new azure.dbforpostgresql.Server(
        "postgres",
        {
            serverName: pulumi.interpolate`psql-mmk-platform-${env}-${suffix}`,
            resourceGroupName,
            location,
            version: cfg.postgres.version,
            sku: { name: cfg.postgres.skuName, tier: cfg.postgres.skuTier },
            storage: { storageSizeGB: cfg.postgres.storageSizeGb },
            backup: { backupRetentionDays: 7, geoRedundantBackup: "Disabled" },
            highAvailability: { mode: "Disabled" },
            network: { publicNetworkAccess: "Enabled" },
            authConfig: { passwordAuth: "Enabled", activeDirectoryAuth: "Disabled" },
            administratorLogin: postgresAdminLogin,
            administratorLoginPassword: postgresPassword.result,
            tags,
        },
        keepData,
    );

    // Phase 1 accepted risk: public endpoint for all IPv4 addresses, protected by TLS and password.
    // Flexible Server rejects concurrent operations on one server, so the child
    // resources are created one after another.
    const firewallRule = new azure.dbforpostgresql.FirewallRule("postgres-allow-all-ipv4", {
        firewallRuleName: "allow-all-ipv4",
        resourceGroupName,
        serverName: postgres.name,
        startIpAddress: "0.0.0.0",
        endIpAddress: "255.255.255.255",
    });

    const requireTls = new azure.dbforpostgresql.Configuration(
        "postgres-require-tls",
        {
            configurationName: "require_secure_transport",
            resourceGroupName,
            serverName: postgres.name,
            value: "on",
            source: "user-override",
        },
        { dependsOn: [firewallRule] },
    );

    const database = new azure.dbforpostgresql.Database(
        "operations",
        {
            databaseName: postgresDatabaseName,
            resourceGroupName,
            serverName: postgres.name,
            charset: "UTF8",
            collation: "en_US.utf8",
        },
        { ...keepData, dependsOn: [requireTls] },
    );

    const diagnosticsAccount = new azure.storage.StorageAccount(
        "diagnostics",
        {
            accountName: pulumi.interpolate`stmmkdiag${suffix}`,
            resourceGroupName,
            location,
            kind: "StorageV2",
            sku: { name: "Standard_LRS" },
            minimumTlsVersion: "TLS1_2",
            allowBlobPublicAccess: false,
            // The agent uses the storage key instead of a data role (design decision 3).
            allowSharedKeyAccess: true,
            tags,
        },
        keepData,
    );

    const diagnosticsContainer = new azure.storage.BlobContainer("diagnostics", {
        containerName: diagnosticsContainerName,
        accountName: diagnosticsAccount.name,
        resourceGroupName,
        publicAccess: "None",
    });

    new azure.storage.ManagementPolicy("diagnostics-retention", {
        managementPolicyName: "default",
        accountName: diagnosticsAccount.name,
        resourceGroupName,
        policy: {
            rules: [
                {
                    name: "delete-diagnostics-after-30-days",
                    enabled: true,
                    type: "Lifecycle",
                    definition: {
                        filters: {
                            blobTypes: ["blockBlob", "appendBlob"],
                            prefixMatch: [`${diagnosticsContainerName}/`],
                        },
                        actions: {
                            baseBlob: { delete: { daysAfterModificationGreaterThan: 30 } },
                        },
                    },
                },
            ],
        },
    });

    const diagnosticsKeys = azure.storage.listStorageAccountKeysOutput({
        resourceGroupName,
        accountName: diagnosticsAccount.name,
    });

    return {
        postgres,
        postgresPassword: postgresPassword.result,
        database,
        diagnosticsAccount,
        diagnosticsContainer,
        diagnosticsStorageKey: pulumi.secret(
            diagnosticsKeys.keys.apply((keys) => {
                const value = keys[0]?.value;
                if (!value) {
                    throw new Error("Azure did not report a key for the diagnostics storage account.");
                }
                return value;
            }),
        ),
    };
}
