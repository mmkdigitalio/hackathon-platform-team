import { strict as assert } from "node:assert";
import * as path from "node:path";
import { before, describe, it } from "node:test";
import * as pulumi from "@pulumi/pulumi";
import type { AppStackOptions } from "../src/program";

const repoRoot = path.resolve(__dirname, "../../../..");
const catalogDir = path.join(repoRoot, "platform/schemas/app-catalog");

import { FOUNDATION } from "./foundation-fixture";

interface Created {
  type: string;
  name: string;
  inputs: Record<string, any>;
}

let created: Created[] = [];
let foundation = FOUNDATION;

/** Mock-Eingaben enthalten Secrets in Pulumis Secret-Hülle; für Prüfungen entpacken. */
const SECRET_SIG = "4dabf18193072939515e22adb298388d";
function unwrap(v: any): any {
  if (Array.isArray(v)) return v.map(unwrap);
  if (v && typeof v === "object") {
    if (SECRET_SIG in v) return unwrap(v.value);
    return Object.fromEntries(Object.entries(v).map(([k, x]) => [k, unwrap(x)]));
  }
  return v;
}

function mock(project: string) {
  created = [];
  pulumi.runtime.setMocks(
    {
      newResource(args) {
        created.push({ type: args.type, name: args.name, inputs: unwrap(args.inputs) });
        if (args.type === "pulumi:pulumi:StackReference") {
          return { id: args.name, state: { outputs: foundation, secretOutputNames: ["kubeconfig", "acrPassword", "postgresAdminPassword"] } };
        }
        const state = args.type.endsWith("helm.sh/v3:Release")
          ? { ...args.inputs, status: { name: args.inputs.name } }
          : args.type === "random:index/randomPassword:RandomPassword"
            ? { ...args.inputs, result: `generated-${args.name}` }
            : args.inputs;
        return { id: `${args.name}-id`, state };
      },
      call: (args) => args.inputs,
    },
    project,
    "staging",
    false,
  );
}

const value = <T>(o: pulumi.Input<T>): Promise<T> => new Promise((resolve) => pulumi.output(o).apply(resolve as any));

const options = (appId: string): AppStackOptions => ({
  appId,
  environment: "staging",
  repoRoot,
  layout: {
    catalogDir: path.join(catalogDir, "examples/valid/catalog"),
    environmentsDir: path.join(catalogDir, "examples/valid/environments"),
  },
  foundationStack: "organization/foundation/staging",
  expectedContractVersion: 1,
  gitSha: "f".repeat(40),
  operationId: "op-test-1",
  storageClass: "managed-csi",
  postgresSslMode: "require",
});

async function run(appId: string, overrides: Partial<AppStackOptions> = {}) {
  mock(`app-${appId}`);
  const { appStack } = await import("../src/program");
  const outputs = appStack({ ...options(appId), ...overrides });
  await value(outputs.url);
  await value(outputs.helmRelease);
  await new Promise((r) => setTimeout(r, 50));
  return outputs;
}

const ofType = (suffix: string) => created.filter((r) => r.type.endsWith(suffix));

describe("App-Stack affine (Platzhalter-Ausgaben)", () => {
  let outputs: Awaited<ReturnType<typeof run>>;
  before(async () => {
    foundation = FOUNDATION;
    outputs = await run("affine");
  });

  it("enthält nur Kubernetes-, PostgreSQL- und Random-Ressourcen", () => {
    const allowed = /^(kubernetes:|postgresql:|random:|pulumi:providers:(kubernetes|postgresql)$|pulumi:pulumi:StackReference$)/;
    const types = [...new Set(created.map((r) => r.type))].sort();
    assert.deepEqual(types.filter((t) => !allowed.test(t)), [], types.join("\n"));
    assert.ok(!types.some((t) => /azure/i.test(t)));
  });

  it("liest Foundation-Werte per StackReference", () => {
    assert.deepEqual(ofType("StackReference").map((r) => r.name), ["organization/foundation/staging"]);
  });

  it("leitet Namespace und Host nach Konvention ab", async () => {
    assert.equal(ofType("core/v1:Namespace")[0].inputs.metadata.name, "affine-staging");
    assert.equal(await value(outputs.host), "affine.20-0-0-1.sslip.io");
    const ingress = ofType("networking.k8s.io/v1:Ingress")[0].inputs;
    assert.equal(ingress.spec.rules[0].host, "affine.20-0-0-1.sslip.io");
    assert.deepEqual(ingress.spec.rules[0].http.paths[0].backend.service, { name: "affine", port: { number: 3010 } });
    assert.equal(ingress.spec.tls[0].secretName, "affine-tls");
    assert.equal(ingress.spec.ingressClassName, "traefik");
    assert.equal(ingress.metadata.annotations["cert-manager.io/cluster-issuer"], "letsencrypt");
  });

  it("legt Image-Pull-Secret aus dem ACR-Admin-User an", () => {
    const secret = ofType("core/v1:Secret").find((r) => r.name === "acr-pull")!;
    assert.equal(secret.inputs.type, "kubernetes.io/dockerconfigjson");
    const auths = JSON.parse(secret.inputs.stringData[".dockerconfigjson"]).auths;
    assert.deepEqual(Object.keys(auths), ["mmkplatformstaging.azurecr.io"]);
  });

  it("löst App- und Test-Secret-Referenzen in <app-id>-platform auf", () => {
    const platform = ofType("core/v1:Secret").find((r) => r.name === "affine-platform")!.inputs.stringData;
    assert.deepEqual(Object.keys(platform).sort(), ["DATABASE_URL", "REDIS_SERVER_HOST", "REDIS_SERVER_PASSWORD", "SMOKE_USER_PASSWORD"]);
    assert.match(platform.DATABASE_URL, /^postgresql:\/\/affine:.+@psql-mmk-platform-staging\.postgres\.database\.azure\.com:5432\/affine\?sslmode=require$/);
    assert.equal(platform.REDIS_SERVER_HOST, "affine-redis.affine-staging.svc.cluster.local");
    assert.equal(ofType("core/v1:Secret").find((r) => r.name === "affine-smoke"), undefined);
  });

  it("legt Datenbank und Login nach App-ID sowie PVC managed-csi an", () => {
    assert.equal(ofType("index/role:Role")[0].inputs.name, "affine");
    assert.equal(ofType("index/database:Database")[0].inputs.name, "affine");
    const grant = ofType("index/grant:Grant")[0].inputs;
    assert.deepEqual([grant.database, grant.role, grant.schema, grant.objectType], ["affine", "affine", "public", "schema"]);
    assert.deepEqual(grant.privileges, ["CREATE", "USAGE"]);
    const pvc = ofType("core/v1:PersistentVolumeClaim")[0].inputs;
    assert.equal(pvc.metadata.name, "affine-data");
    assert.equal(pvc.spec.storageClassName, "managed-csi");
    assert.deepEqual(pvc.spec.accessModes, ["ReadWriteOnce"]);
    assert.equal(pvc.spec.resources.requests.storage, "10Gi");
  });

  it("rollt das Chart als Helm-Release mit Werte-Vertrag und Rollout-Markierung aus", () => {
    const release = ofType("helm.sh/v3:Release")[0].inputs;
    assert.equal(release.chart, "oci://ghcr.io/mmkdigitalio/charts/affine");
    assert.equal(release.version, "0.1.0");
    assert.equal(release.atomic, false);
    assert.equal(release.skipAwait, false);
    const v = release.values;
    assert.equal(v.secretName, "affine-platform");
    assert.equal(v.imagePullSecretName, "acr-pull");
    assert.equal(v.rolloutMarker, "op-test-1");
    assert.equal(v.image.repository, "ghcr.io/toeverything/affine-graphql");
    assert.equal(v.image.digest, "sha256:e442d8e6a4923ecf037c44cfe6ce9a6c78de415250f029c1a57aac6ca37dc7c7");
    assert.equal(v.platform.persistentVolumeClaim, "affine-data");
    assert.equal(v.platform.replicas, 1);
    assert.equal(v.platform.host, "affine.20-0-0-1.sslip.io");
    assert.equal(release.values.affine.telemetry, false);
  });

  it("annotiert den Namespace erst nach dem Release mit der Release-Referenz", () => {
    const patch = ofType("core/v1:NamespacePatch")[0].inputs;
    assert.equal(patch.metadata.annotations["ops.mmk/release-git-sha"], "f".repeat(40));
    assert.equal(patch.metadata.annotations["ops.mmk/release-chart-version"], "0.1.0");
  });
});

describe("App-Stack operations-agent (Platzhalter-Ausgaben)", () => {
  before(async () => {
    foundation = FOUNDATION;
    await run("operations-agent");
  });

  it("braucht keinen Datenbaustein und löst Chart und Secrets aus der Foundation auf", () => {
    assert.equal(ofType("index/database:Database").length, 0);
    assert.equal(ofType("pulumi:providers:postgresql").length, 0);
    const release = ofType("helm.sh/v3:Release")[0].inputs;
    assert.equal(release.chart, "oci://mmkplatformstaging.azurecr.io/helm/operations-agent");
    assert.equal(release.values.image.repository, "mmkplatformstaging.azurecr.io/operations-agent");
    const secret = ofType("core/v1:Secret").find((r) => r.name === "operations-agent-platform")!.inputs.stringData;
    assert.equal(secret.POSTGRES_PASSWORD, "pg-secret");
    assert.deepEqual(JSON.parse(secret.OPS_MODEL_API_KEYS), { "azure-openai": "model-secret" });
    assert.match(secret.OPS_GITHUB_APP_PRIVATE_KEY, /^-----BEGIN RSA PRIVATE KEY-----/);
    assert.equal(secret.OPS_PUBLIC_SCHEME, "https");
  });
});

describe("GitHub-App-Schlüssel aus geschützter CI-Konfiguration", () => {
  it("überbrückt einen fehlenden Foundation-Output beim Agenten", async () => {
    foundation = { ...FOUNDATION };
    delete foundation.githubAppPrivateKey;
    await run("operations-agent", { githubAppPrivateKey: pulumi.secret("ci-app-key") });
    const secret = ofType("core/v1:Secret").find((r) => r.name === "operations-agent-platform")!.inputs.stringData;
    assert.equal(secret.OPS_GITHUB_APP_PRIVATE_KEY, "ci-app-key");
  });

  it("behält einen vorhandenen Foundation-Schlüssel als erste Quelle", async () => {
    foundation = FOUNDATION;
    await run("operations-agent", { githubAppPrivateKey: pulumi.secret("ci-app-key") });
    const secret = ofType("core/v1:Secret").find((r) => r.name === "operations-agent-platform")!.inputs.stringData;
    assert.equal(secret.OPS_GITHUB_APP_PRIVATE_KEY, FOUNDATION.githubAppPrivateKey);
  });

  it("verwendet den CI-Schlüssel auch bei leerem Foundation-Wert", async () => {
    foundation = { ...FOUNDATION, githubAppPrivateKey: "" };
    await run("operations-agent", { githubAppPrivateKey: pulumi.secret("ci-app-key") });
    const secret = ofType("core/v1:Secret").find((r) => r.name === "operations-agent-platform")!.inputs.stringData;
    assert.equal(secret.OPS_GITHUB_APP_PRIVATE_KEY, "ci-app-key");
  });
});

describe("HTTP-Fallback (publicScheme http)", () => {
  let outputs: Awaited<ReturnType<typeof run>>;
  before(async () => {
    foundation = { ...FOUNDATION, publicScheme: "http" };
    outputs = await run("affine");
  });

  it("legt Ingress ohne TLS und ohne Zertifikatsanforderung an", async () => {
    const ingress = ofType("networking.k8s.io/v1:Ingress")[0].inputs;
    assert.deepEqual(ingress.spec.tls, []);
    assert.deepEqual(ingress.metadata.annotations, {});
    assert.equal(await value(outputs.url), "http://affine.20-0-0-1.sslip.io");
  });
});
