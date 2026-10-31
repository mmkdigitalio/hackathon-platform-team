import { strict as assert } from "node:assert";
import { before, describe, it } from "node:test";
import * as pulumi from "@pulumi/pulumi";
import * as kubernetes from "@pulumi/kubernetes";
import type { BlockResult } from "../src/blocks";

const created = new Map<string, Record<string, any>>();
const options = new Map<string, pulumi.CustomResourceOptions>();
const deployments = new Map<string, any>();
const services = new Map<string, any>();
const secrets = new Map<string, any>();
const value = <T>(o: pulumi.Input<T>): Promise<T> => new Promise((resolve) => pulumi.output(o).apply(resolve as any));

// Der Mock-Monitor erhält protect und dependsOn nicht; die Registrierung separat erfassen.
const runtimeResource = require("@pulumi/pulumi/runtime/resource");
const registerResource = runtimeResource.registerResource;
runtimeResource.registerResource = (res: unknown, parent: unknown, type: string, name: string, ...rest: any[]) => {
  options.set(`${type}::${name}`, rest[4] ?? {});
  return registerResource(res, parent, type, name, ...rest);
};
pulumi.runtime.setMocks({
  newResource(args) {
    created.set(args.name, { type: args.type, ...args.inputs });
    if (args.type === "kubernetes:apps/v1:Deployment") deployments.set(args.name, args.inputs);
    if (args.type === "kubernetes:core/v1:Service") services.set(args.name, args.inputs);
    if (args.type === "kubernetes:core/v1:Secret") secrets.set(args.name, args.inputs);
    return { id: args.name, state: args.type === "random:index/randomPassword:RandomPassword"
      ? { ...args.inputs, result: `${args.name.replace(/[^a-z0-9]/gi, "")}123ABC` } : args.inputs };
  },
  call: (args) => args.inputs,
}, "blocks-test", "staging", false);

describe("MariaDB-Datenbaustein", () => {
  let defaults: BlockResult;
  let sized: BlockResult;
  let MARIADB_IMAGE: string;
  let MARIADB_VERSION: string;

  before(async () => {
    const blocks = await import("../src/blocks");
    ({ MARIADB_IMAGE, MARIADB_VERSION } = blocks);
    const ctx = {
      appId: "team-timer", namespace: "team-timer-staging", labels: { "platform.mmk/app-id": "team-timer" },
      k8s: new kubernetes.Provider("test-k8s"), postgresHost: pulumi.output("unused"), postgresSslMode: "require",
      operationsDatabase: pulumi.output("operations"), storageClass: "managed-csi",
    };
    defaults = blocks.provisionBlocks(["mariadb"], ctx);
    sized = blocks.provisionBlocks(["mariadb", "persistent-volume"], {
      ...ctx, appId: "kimai", namespace: "kimai-staging", databaseSize: "12Gi", volumeSize: "8Gi",
    });
    await Promise.all([...defaults.resources, ...sized.resources].map((r) => value(r.urn)));
  });

  it("verwendet ein separates PVC mit Default 5Gi oder expliziter databaseSize", () => {
    for (const [name, storage] of [["team-timer-mariadb-data", "5Gi"], ["kimai-mariadb-data", "12Gi"]]) {
      const pvc = created.get(name)!;
      assert.equal(pvc.type, "kubernetes:core/v1:PersistentVolumeClaim");
      assert.equal(pvc.metadata.name, name);
      assert.equal(pvc.spec.resources.requests.storage, storage);
      assert.equal(pvc.spec.storageClassName, "managed-csi");
      assert.deepEqual(pvc.spec.accessModes, ["ReadWriteOnce"]);
      assert.equal(pvc.metadata.annotations["pulumi.com/skipAwait"], "true");
      assert.equal(options.get(`kubernetes:core/v1:PersistentVolumeClaim::${name}`)?.protect, true);
    }
    assert.equal(defaults.persistentVolumeClaim, undefined);
    assert.equal(sized.persistentVolumeClaim, "kimai-data");
    assert.equal(created.get("kimai-data")!.spec.resources.requests.storage, "8Gi");
  });

  it("startet genau einen persistenten MariaDB-Container per Recreate mit Proben und Ressourcen", () => {
    const spec = deployments.get("team-timer-mariadb")!.spec;
    assert.equal(spec.replicas, 1);
    assert.deepEqual(spec.strategy, { type: "Recreate" });
    const pod = spec.template.spec;
    const container = pod.containers[0];
    assert.equal(container.image, MARIADB_IMAGE);
    assert.match(MARIADB_IMAGE, /^mariadb:11\.4@sha256:[a-f0-9]{64}$/);
    assert.deepEqual(container.resources, { requests: { cpu: "50m", memory: "256Mi" }, limits: { memory: "512Mi" } });
    for (const probe of [container.readinessProbe, container.livenessProbe, container.startupProbe]) {
      assert.deepEqual(probe.exec.command, ["healthcheck.sh", "--connect", "--innodb_initialized"]);
    }
    assert.deepEqual(container.volumeMounts, [{ name: "data", mountPath: "/var/lib/mysql" }]);
    assert.deepEqual(pod.volumes, [{ name: "data", persistentVolumeClaim: { claimName: "team-timer-mariadb-data" } }]);
    const dependencies = options.get("kubernetes:apps/v1:Deployment::team-timer-mariadb")?.dependsOn as pulumi.Resource[];
    assert.ok(dependencies?.length === 2);
  });

  it("legt unabhängige alphanumerische Passwörter an und übergibt sie per Secret", () => {
    for (const name of ["team-timer-mariadb-password", "team-timer-mariadb-root-password"]) {
      assert.equal(created.get(name)!.special, false);
      assert.equal(created.get(name)!.length, 32);
    }
    const env = deployments.get("team-timer-mariadb")!.spec.template.spec.containers[0].env;
    assert.deepEqual(env.slice(0, 2), [{ name: "MARIADB_DATABASE", value: "team_timer" }, { name: "MARIADB_USER", value: "team_timer" }]);
    assert.deepEqual(env.slice(2), [
      { name: "MARIADB_PASSWORD", valueFrom: { secretKeyRef: { name: "team-timer-mariadb", key: "password" } } },
      { name: "MARIADB_ROOT_PASSWORD", valueFrom: { secretKeyRef: { name: "team-timer-mariadb", key: "root-password" } } },
    ]);
    const stringData = secrets.get("team-timer-mariadb")!.stringData;
    assert.deepEqual(Object.keys(stringData.value ?? stringData).sort(), ["password", "root-password"]);
  });

  it("exponiert genau sechs App-Secrets einschließlich Symfony-URL mit Patch-Version", async () => {
    const resolved = await value(pulumi.all(defaults.secrets));
    assert.deepEqual(Object.keys(resolved).sort(), ["mariadb-database", "mariadb-host", "mariadb-password", "mariadb-port", "mariadb-url", "mariadb-user"]);
    assert.equal(resolved["mariadb-host"], "team-timer-mariadb.team-timer-staging.svc.cluster.local");
    assert.equal(resolved["mariadb-port"], "3306");
    assert.equal(resolved["mariadb-database"], "team_timer");
    assert.equal(resolved["mariadb-user"], "team_timer");
    assert.match(resolved["mariadb-password"], /^[a-z0-9]+$/i);
    assert.equal(resolved["mariadb-url"], `mysql://team_timer:${resolved["mariadb-password"]}@${resolved["mariadb-host"]}:3306/team_timer?charset=utf8mb4&serverVersion=${MARIADB_VERSION}-MariaDB`);
    assert.equal(await pulumi.isSecret(defaults.secrets["mariadb-url"]), true);
    assert.equal(await pulumi.isSecret(defaults.secrets["mariadb-password"]), true);
  });

  it("stellt Port 3306 nur für das MariaDB-Deployment bereit", () => {
    const service = services.get("team-timer-mariadb")!;
    assert.deepEqual(service.spec.ports, [{ name: "mariadb", port: 3306, targetPort: 3306 }]);
    assert.deepEqual(service.spec.selector, deployments.get("team-timer-mariadb")!.spec.selector.matchLabels);
  });
});

