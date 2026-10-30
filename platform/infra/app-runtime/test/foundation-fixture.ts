/** Platzhalter-Ausgaben nach dem Foundation-Ausgabevertrag */
export const FOUNDATION: Record<string, unknown> = {
  contractVersion: 1,
  environment: "staging",
  baseDomain: "20-0-0-1.sslip.io",
  operationsApiHost: "operations-agent.20-0-0-1.sslip.io",
  kubeconfig: "apiVersion: v1\nkind: Config\n",
  acrLoginServer: "mmkplatformstaging.azurecr.io",
  acrUsername: "mmkplatformstaging",
  acrPassword: "acr-secret",
  postgresHost: "psql-mmk-platform-staging.postgres.database.azure.com",
  postgresAdminUser: "platformadmin",
  postgresAdminPassword: "pg-secret",
  postgresOperationsDatabase: "operations",
};
