import * as path from "node:path";
import * as kubernetes from "@pulumi/kubernetes";
import * as postgresql from "@pulumi/postgresql";
import * as pulumi from "@pulumi/pulumi";
import * as random from "@pulumi/random";
import {
  derivedNames,
  imageDigest,
  loadApp,
  loadContracts,
  resolveImage,
  type CatalogEntry,
  type TreeLayout,
} from "../../../schemas/app-catalog/src/catalog";
import { provisionBlocks } from "./blocks";

export interface AppStackOptions {
  appId: string;
  environment: string;
  repoRoot: string;
  layout?: TreeLayout;
  foundationStack: string;
  expectedContractVersion: number;
  gitSha: string;
  /** Rollout-Markierung; ändert sich je Lauf und erzwingt so ein Helm-Upgrade mit Drei-Wege-Merge. */
  operationId: string;
  storageClass: string;
  /** Lokale Übersteuerungen; sonst gelten die Foundation-Ausgaben ingressClassName, clusterIssuerName, publicScheme. */
  ingressClassName?: string;
  clusterIssuer?: string;
  publicScheme?: "https" | "http";
  postgresSslMode: string;
  /** Nur lokal: Verbindung des Pulumi-Providers, wenn der Server für Pods unter einer anderen Adresse erreichbar ist */
  postgresAdminHost?: string;
  postgresAdminPort?: number;
  /** Geschütztes CI-Secret als Rückfall für noch nicht aktualisierte Foundation-Ausgaben. */
  githubAppPrivateKey?: pulumi.Output<string>;
}

const ANNOTATION = "platform.mmk";
const RELEASE_ANNOTATION = "ops.mmk/release";

function chartSource(
  chart: CatalogEntry["chart"],
  repoRoot: string,
  acr: { server: pulumi.Output<string>; username: pulumi.Output<string>; password: pulumi.Output<string> },
): { chart: pulumi.Input<string>; repositoryOpts?: kubernetes.types.input.helm.v3.RepositoryOpts } {
  if (chart.platform) return { chart: path.join(repoRoot, "platform/charts", chart.platform.name) };
  if (chart.acr) {
    return {
      chart: pulumi.interpolate`oci://${acr.server}/${chart.acr.repository}`,
      repositoryOpts: { username: acr.username, password: acr.password },
    };
  }
  if (chart.oci) return { chart: chart.oci.url };
  return { chart: chart.repository!.name, repositoryOpts: { repo: chart.repository!.url } };
}

export function appStack(opts: AppStackOptions) {
  const { appId, environment } = opts;
  const contracts = loadContracts(opts.repoRoot);
  const app = loadApp(appId, environment, contracts, opts.repoRoot, opts.layout);
  const names = derivedNames(appId, environment);

  const foundation = new pulumi.StackReference(opts.foundationStack);
  const contractVersion = foundation.requireOutput("contractVersion").apply((v) => {
    if (Number(v) !== opts.expectedContractVersion) {
      throw new Error(`Foundation-Ausgabevertrag: erwartet contractVersion ${opts.expectedContractVersion}, vorhanden ${v}`);
    }
    return v;
  });
  // Jede Ausgabe läuft über die Versionsprüfung, damit bei falschem Vertrag nichts angelegt wird.
  // Objekte wie modelApiKeys gehen als JSON weiter, nicht als "[object Object]".
  const out = (name: string) => {
    const value = appId === "operations-agent" && name === "githubAppPrivateKey" && opts.githubAppPrivateKey
      ? pulumi.all([foundation.getOutput(name), opts.githubAppPrivateKey]).apply(([stored, fallback]) => stored || fallback)
      : foundation.requireOutput(name);
    return pulumi.all([contractVersion, value])
      .apply(([, v]) => (typeof v === "object" && v !== null ? JSON.stringify(v) : String(v)));
  };
  const baseDomain = out("baseDomain");
  const host = pulumi.interpolate`${appId}.${baseDomain}`;
  const acr = { server: out("acrLoginServer"), username: out("acrUsername"), password: pulumi.secret(out("acrPassword")) };

  const k8s = new kubernetes.Provider("k8s", { kubeconfig: pulumi.secret(out("kubeconfig")) });
  const k8sOpts = { provider: k8s };
  const labels = {
    [`${ANNOTATION}/app-id`]: appId,
    [`${ANNOTATION}/environment`]: environment,
    "app.kubernetes.io/managed-by": "app-runtime",
  };

  // Löschen des Namespace würde PVC und Secrets mitnehmen; Außerbetriebnahme ist ein eigener Vorgang
  const namespace = new kubernetes.core.v1.Namespace(
    names.namespace,
    { metadata: { name: names.namespace, labels } },
    { ...k8sOpts, protect: true },
  );
  const ns = namespace.metadata.name;

  const dockerConfig = pulumi
    .all([acr.server, acr.username, acr.password])
    .apply(([server, username, password]) =>
      JSON.stringify({
        auths: { [server]: { username, password, auth: Buffer.from(`${username}:${password}`).toString("base64") } },
      }),
    );
  const pullSecret = new kubernetes.core.v1.Secret(
    names.imagePullSecret,
    {
      metadata: { name: names.imagePullSecret, namespace: ns, labels },
      type: "kubernetes.io/dockerconfigjson",
      stringData: { ".dockerconfigjson": pulumi.secret(dockerConfig) },
    },
    k8sOpts,
  );

  const needsPostgres = app.catalog.dataBlocks.includes("postgres-database");
  const postgresHost = out("postgresHost");
  const postgres = needsPostgres
    ? new postgresql.Provider("postgres", {
        host: opts.postgresAdminHost ?? postgresHost,
        port: opts.postgresAdminPort ?? 5432,
        username: out("postgresAdminUser"),
        password: pulumi.secret(out("postgresAdminPassword")),
        sslmode: opts.postgresSslMode,
        superuser: false,
        database: "postgres",
        connectTimeout: 15,
      })
    : undefined;

  const blocks = provisionBlocks(app.catalog.dataBlocks, {
    appId,
    namespace: ns,
    labels,
    k8s,
    postgres,
    postgresHost,
    postgresSslMode: opts.postgresSslMode,
    operationsDatabase: out("postgresOperationsDatabase"),
    storageClass: opts.storageClass,
    volumeSize: app.desired.sizes.volumeSize,
  });

  const appSecrets: Record<string, pulumi.Output<string>> = { ...blocks.secrets };
  for (const name of app.catalog.generatedSecrets ?? []) {
    appSecrets[name] = new random.RandomPassword(`${appId}-generated-${name}`, { length: 40, special: false }).result;
  }
  const resolve = (refs: Record<string, string> | undefined) =>
    Object.fromEntries(
      Object.entries(refs ?? {}).map(([key, ref]) => {
        const [kind, name] = ref.split(":", 2);
        return [key, pulumi.secret(kind === "foundation" ? out(name) : appSecrets[name])];
      }),
    );

  // Ein Secret für App und Smoke-Test; der Smoke-Job erhält per secretKeyRef nur seine eigenen Schlüssel
  const platformSecret = new kubernetes.core.v1.Secret(
    names.platformSecret,
    {
      metadata: { name: names.platformSecret, namespace: ns, labels },
      stringData: resolve({ ...app.catalog.secrets, ...(app.catalog.smokeTest.secrets ?? {}) }),
    },
    k8sOpts,
  );

  const image = acr.server.apply((server) => resolveImage(app.desired.image, server));
  const release = { gitSha: opts.gitSha, imageDigest: imageDigest(app.desired.image), chartVersion: app.desired.chart.version };
  // http nur, solange A den dokumentierten Let's-Encrypt-Fallback (E8) aktiviert hat
  const scheme = opts.publicScheme ? pulumi.output(opts.publicScheme) : out("publicScheme");
  const tls = scheme.apply((s) => s === "https");
  const ingressClassName = opts.ingressClassName ?? out("ingressClassName");
  const clusterIssuer = opts.clusterIssuer ?? out("clusterIssuerName");

  // Werte-Vertrag mit den Charts, siehe platform/schemas/app-catalog/README.md
  const desiredValues = app.desired.values ?? {};
  const desiredImage = (desiredValues.image ?? {}) as Record<string, unknown>;
  const platformValues = {
    image: { ...desiredImage, repository: image.apply((i) => i.split("@")[0]), digest: release.imageDigest },
    secretName: names.platformSecret,
    imagePullSecretName: names.imagePullSecret,
    rolloutMarker: opts.operationId,
    platform: {
      appId,
      environment,
      namespace: names.namespace,
      host,
      url: pulumi.interpolate`${scheme}://${host}`,
      persistentVolumeClaim: blocks.persistentVolumeClaim ?? "",
      replicas: app.desired.sizes.replicas,
      resources: app.desired.sizes.resources ?? {},
      release,
    },
  };

  const source = chartSource(app.catalog.chart, opts.repoRoot, acr);
  const helmRelease = new kubernetes.helm.v3.Release(
    appId,
    {
      name: appId,
      namespace: ns,
      chart: source.chart,
      version: app.desired.chart.version,
      repositoryOpts: source.repositoryOpts,
      values: { ...desiredValues, ...platformValues },
      createNamespace: false,
      // Kein atomic: ein fehlgeschlagenes Release bleibt sichtbar und wird nicht still zurückgerollt
      atomic: false,
      cleanupOnFail: false,
      skipAwait: false,
      waitForJobs: true,
      timeout: 900,
      maxHistory: 10,
    },
    { ...k8sOpts, dependsOn: [pullSecret, platformSecret, ...blocks.resources] },
  );

  new kubernetes.networking.v1.Ingress(
    appId,
    {
      metadata: {
        name: appId,
        namespace: ns,
        labels,
        annotations: pulumi
          .all([tls, clusterIssuer])
          .apply(([t, issuer]): Record<string, string> => (t ? { "cert-manager.io/cluster-issuer": issuer } : {})),
      },
      spec: {
        ingressClassName,
        tls: pulumi.all([tls, host]).apply(([t, h]) => (t ? [{ hosts: [h], secretName: `${appId}-tls` }] : [])),
        rules: [
          {
            host,
            http: {
              paths: [
                {
                  path: "/",
                  pathType: "Prefix",
                  backend: { service: { name: app.catalog.ingress.service, port: { number: app.catalog.ingress.port } } },
                },
              ],
            },
          },
        ],
      },
    },
    { ...k8sOpts, dependsOn: [helmRelease] },
  );

  // Release-Referenz des zuletzt erfolgreich ausgerollten Stands; bleibt bei fehlgeschlagenem Helm-Release unverändert
  new kubernetes.core.v1.NamespacePatch(
    `${names.namespace}-release`,
    {
      metadata: {
        name: ns,
        // Schlüssel liest auch D's Kubernetes-Werkzeug (operations-agent/src/tools/kubernetes.py)
        annotations: {
          [`${RELEASE_ANNOTATION}-git-sha`]: release.gitSha,
          [`${RELEASE_ANNOTATION}-image-digest`]: release.imageDigest,
          [`${RELEASE_ANNOTATION}-chart-version`]: release.chartVersion,
          [`${RELEASE_ANNOTATION}-operation-id`]: opts.operationId,
          [`${RELEASE_ANNOTATION}-upstream`]: app.desired.upstreamRelease ?? "",
        },
      },
    },
    { ...k8sOpts, dependsOn: [helmRelease] },
  );

  return {
    appId,
    environment,
    namespace: ns,
    host,
    url: pulumi.interpolate`${scheme}://${host}`,
    release,
    // app-plan.yml übernimmt beides, damit ein Preview nur echte Änderungen zeigt
    rolloutMarker: opts.operationId,
    helmRelease: helmRelease.status.name,
    secretName: names.platformSecret,
    smokeSecretKeys: Object.keys(app.catalog.smokeTest.secrets ?? {}),
    imagePullSecret: names.imagePullSecret,
    availabilityPath: app.catalog.availability.path,
    smokeImage: acr.server.apply((server) => resolveImage(app.catalog.smokeTest.image, server)),
  };
}
