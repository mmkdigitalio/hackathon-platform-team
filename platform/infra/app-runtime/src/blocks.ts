import * as kubernetes from "@pulumi/kubernetes";
import * as postgresql from "@pulumi/postgresql";
import * as pulumi from "@pulumi/pulumi";
import * as random from "@pulumi/random";
import type { DataBlockName } from "../../../schemas/app-catalog/src/catalog";

/** Redis ohne Persistenz; Version per Digest fixiert. */
export const REDIS_IMAGE = "redis:7.4-alpine@sha256:8da19c7399dca900c6822a1cf30f9d2285585e2c4b457ff4095b0f4500054d3f";

export interface BlockContext {
  appId: string;
  namespace: pulumi.Input<string>;
  labels: Record<string, string>;
  k8s: kubernetes.Provider;
  postgres?: postgresql.Provider;
  postgresHost: pulumi.Output<string>;
  postgresSslMode: string;
  operationsDatabase: pulumi.Output<string>;
  storageClass: string;
  volumeSize?: string;
}

export interface BlockResult {
  /** Werte für app:<name>-Referenzen */
  secrets: Record<string, pulumi.Output<string>>;
  /** Ressourcen, die vor dem Helm-Release existieren müssen */
  resources: pulumi.Resource[];
  persistentVolumeClaim?: string;
}

function postgresDatabase(ctx: BlockContext): BlockResult {
  if (!ctx.postgres) throw new Error("postgres-database ohne PostgreSQL-Provider");
  const name = ctx.appId.replace(/-/g, "_");
  // Die Datenbank operations gehört C und bleibt unberührt
  const checkedName = ctx.operationsDatabase.apply((ops) => {
    if (ops === name) throw new Error(`App-ID ${ctx.appId} würde die Datenbank ${ops} übernehmen`);
    return name;
  });
  const password = new random.RandomPassword(`${ctx.appId}-postgres-password`, { length: 32, special: false });
  const role = new postgresql.Role(
    `${ctx.appId}-postgres-role`,
    { name: checkedName, login: true, password: password.result },
    { provider: ctx.postgres, protect: true },
  );
  const database = new postgresql.Database(
    `${ctx.appId}-postgres-database`,
    { name, owner: role.name },
    { provider: ctx.postgres, protect: true },
  );
  // Auf Azure Flexible Server gehört das Schema public azure_pg_admin; der Datenbankbesitzer darf dort
  // ohne diese Berechtigung keine Tabellen anlegen ("permission denied for schema public").
  const schemaGrant = new postgresql.Grant(
    `${ctx.appId}-postgres-schema-public`,
    { database: database.name, role: role.name, schema: "public", objectType: "schema", privileges: ["CREATE", "USAGE"] },
    { provider: ctx.postgres },
  );
  const url = pulumi.interpolate`postgresql://${name}:${password.result}@${ctx.postgresHost}:5432/${name}?sslmode=${ctx.postgresSslMode}`;
  return {
    secrets: {
      "postgres-host": ctx.postgresHost,
      "postgres-database": database.name,
      "postgres-user": role.name,
      "postgres-password": password.result,
      "postgres-url": pulumi.secret(url),
    },
    resources: [role, database, schemaGrant],
  };
}

function redis(ctx: BlockContext): BlockResult {
  const name = `${ctx.appId}-redis`;
  const labels = { ...ctx.labels, "app.kubernetes.io/name": "redis", "app.kubernetes.io/instance": name };
  const password = new random.RandomPassword(`${ctx.appId}-redis-password`, { length: 32, special: false });
  const opts = { provider: ctx.k8s };
  const secret = new kubernetes.core.v1.Secret(
    name,
    { metadata: { name, namespace: ctx.namespace, labels }, stringData: { password: password.result } },
    opts,
  );
  const deployment = new kubernetes.apps.v1.Deployment(
    name,
    {
      metadata: { name, namespace: ctx.namespace, labels },
      spec: {
        replicas: 1,
        selector: { matchLabels: { "app.kubernetes.io/instance": name } },
        template: {
          metadata: { labels },
          spec: {
            securityContext: { runAsNonRoot: true, runAsUser: 999, fsGroup: 999 },
            containers: [
              {
                name: "redis",
                image: REDIS_IMAGE,
                args: ["--requirepass", "$(REDIS_PASSWORD)", "--save", "", "--appendonly", "no"],
                env: [{ name: "REDIS_PASSWORD", valueFrom: { secretKeyRef: { name, key: "password" } } }],
                ports: [{ name: "redis", containerPort: 6379 }],
                readinessProbe: { tcpSocket: { port: 6379 }, periodSeconds: 5 },
                livenessProbe: { tcpSocket: { port: 6379 }, initialDelaySeconds: 10 },
                resources: { requests: { cpu: "50m", memory: "64Mi" }, limits: { memory: "256Mi" } },
                securityContext: { allowPrivilegeEscalation: false, readOnlyRootFilesystem: true },
                volumeMounts: [{ name: "data", mountPath: "/data" }],
              },
            ],
            volumes: [{ name: "data", emptyDir: {} }],
          },
        },
      },
    },
    { ...opts, dependsOn: [secret] },
  );
  const service = new kubernetes.core.v1.Service(
    name,
    {
      metadata: { name, namespace: ctx.namespace, labels },
      spec: { selector: { "app.kubernetes.io/instance": name }, ports: [{ name: "redis", port: 6379, targetPort: 6379 }] },
    },
    opts,
  );
  return {
    secrets: {
      "redis-host": pulumi.interpolate`${service.metadata.name}.${ctx.namespace}.svc.cluster.local`,
      "redis-password": password.result,
    },
    resources: [deployment, service],
  };
}

function persistentVolume(ctx: BlockContext): BlockResult {
  if (!ctx.volumeSize) throw new Error("persistent-volume ohne sizes.volumeSize");
  const name = `${ctx.appId}-data`;
  const claim = new kubernetes.core.v1.PersistentVolumeClaim(
    name,
    {
      metadata: {
        name,
        namespace: ctx.namespace,
        labels: ctx.labels,
        // WaitForFirstConsumer: gebunden wird erst mit dem Pod aus dem Helm-Release
        annotations: { "pulumi.com/skipAwait": "true" },
      },
      spec: {
        accessModes: ["ReadWriteOnce"],
        storageClassName: ctx.storageClass,
        resources: { requests: { storage: ctx.volumeSize } },
      },
    },
    { provider: ctx.k8s, protect: true },
  );
  return { secrets: {}, resources: [claim], persistentVolumeClaim: name };
}

const IMPLEMENTATIONS: Record<DataBlockName, (ctx: BlockContext) => BlockResult> = {
  "postgres-database": postgresDatabase,
  redis,
  "persistent-volume": persistentVolume,
};

export function provisionBlocks(blocks: DataBlockName[], ctx: BlockContext): BlockResult {
  const result: BlockResult = { secrets: {}, resources: [] };
  for (const block of blocks) {
    const r = IMPLEMENTATIONS[block](ctx);
    Object.assign(result.secrets, r.secrets);
    result.resources.push(...r.resources);
    result.persistentVolumeClaim ??= r.persistentVolumeClaim;
  }
  return result;
}
