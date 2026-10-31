import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { execFileSync } from "node:child_process";
import { Document, isNode, parse as parseYaml } from "yaml";
import { formatIssues, loadContracts, validateTree, type Contracts } from "./catalog";

export interface ScaffoldOptions {
  appId: string;
  description: string;
  image?: string;
  acrImage?: string;
  sourceRepository?: string;
  port: string;
  healthPath?: string;
  dataBlocks?: string;
  volumeSize?: string;
  mountPath?: string;
  env?: string[];
  runbookCommit?: string;
  environment?: string;
  dryRun?: boolean;
}

const PLATFORM_REPOSITORY = "mmkdigitalio/hackathon-platform-team";

function yaml(data: object, comments: Record<string, string>): string {
  const doc = new Document(data);
  for (const [key, comment] of Object.entries(comments)) {
    const node = doc.get(key, true);
    if (isNode(node)) node.commentBefore = ` ${comment}`;
  }
  return doc.toString();
}

/** Erzeugt beide Dateien und prüft sie mit derselben Baumvalidierung wie validate. */
export function scaffoldNewApp(repoRoot: string, options: ScaffoldOptions, contracts: Contracts = loadContracts(repoRoot)) {
  const { appId } = options;
  if (!contracts.appIdPattern.test(appId) ||
      (contracts.appIdEndPattern && !contracts.appIdEndPattern.test(appId)) ||
      contracts.reservedAppIds.has(appId)) {
    throw new Error(`App-ID verletzt die Konvention ${contracts.appIdPattern}, Ende ${contracts.appIdEndPattern ?? "beliebig"}; reserviert: ${[...contracts.reservedAppIds].join(", ")}. Beispiel: zeiterfassung`);
  }
  if ((options.environment ?? "staging") !== "staging") throw new Error("new-app erlaubt nur --environment staging");
  if (!options.description.trim()) throw new Error("--description darf nicht leer sein");
  if (Boolean(options.image) === Boolean(options.acrImage)) throw new Error("Genau eines von --image oder --acr-image angeben");
  const image = options.acrImage ? `acr:${options.acrImage}` : options.image!;
  if (!/@sha256:[0-9a-f]{64}$/.test(image)) throw new Error("Image braucht einen festen @sha256-Digest mit 64 Hex-Zeichen");
  if (options.image?.startsWith("acr:")) throw new Error("Für ein ACR-Image --acr-image <repo>@sha256:<digest> nutzen");
  if (!/^[0-9]+$/.test(options.port) || Number(options.port) < 1 || Number(options.port) > 65535) {
    throw new Error("--port muss eine ganze Zahl zwischen 1 und 65535 sein");
  }
  const healthPath = options.healthPath ?? "/";
  if (!/^\/[A-Za-z0-9._~/-]*$/.test(healthPath)) throw new Error("--health-path muss ein HTTP-Pfad sein, zum Beispiel /health");
  if (options.sourceRepository !== undefined && !/^[A-Za-z0-9-]+\/[A-Za-z0-9._-]+$/.test(options.sourceRepository)) {
    throw new Error("--source-repository muss owner/name sein");
  }
  const blocks = options.dataBlocks ? options.dataBlocks.split(",").map((block) => block.trim()) : [];
  if (blocks.some((block) => !["postgres-database", "redis", "persistent-volume"].includes(block)) || new Set(blocks).size !== blocks.length) {
    throw new Error("--data-blocks erlaubt postgres-database,redis,persistent-volume jeweils einmal");
  }
  const volume = blocks.includes("persistent-volume");
  const postgres = blocks.includes("postgres-database");
  if (!volume && (options.volumeSize !== undefined || options.mountPath !== undefined)) {
    throw new Error("--volume-size und --mount-path brauchen den Datenbaustein persistent-volume");
  }
  const volumeSize = options.volumeSize ?? "5Gi";
  const mountPath = options.mountPath ?? "/data";
  if (volume && !/^[1-9][0-9]*(Mi|Gi)$/.test(volumeSize)) throw new Error("--volume-size muss zum Beispiel 5Gi sein");
  if (volume && !/^\/\S*$/.test(mountPath)) throw new Error("--mount-path muss ein absoluter Pfad sein, zum Beispiel /data");
  const env: Record<string, string> = Object.create(null);
  for (const entry of options.env ?? []) {
    const separator = entry.indexOf("=");
    const key = entry.slice(0, separator);
    if (separator < 1) throw new Error("--env erwartet KEY=VALUE mit einem gültigen Variablennamen");
    if (/PASSWORD|SECRET|TOKEN|KEY/i.test(key)) throw new Error(`--env ${key} sieht wie ein Secret aus; stattdessen Data-Blocks nutzen`);
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(key)) throw new Error("--env erwartet KEY=VALUE mit einem gültigen Variablennamen");
    if ((postgres && ["DATABASE_URL", "PGHOST", "PGDATABASE", "PGUSER"].includes(key)) ||
        (blocks.includes("redis") && key === "REDIS_HOST")) throw new Error(`--env ${key} wird bereits vom Data-Block gesetzt`);
    env[key] = entry.slice(separator + 1);
  }
  const files = [
    { path: `platform/catalog/${appId}/app.yaml`, content: "" },
    { path: `platform/environments/staging/${appId}.yaml`, content: "" },
  ];
  for (const file of files) {
    if (fs.existsSync(path.join(repoRoot, file.path)) || fs.lstatSync(path.join(repoRoot, file.path), { throwIfNoEntry: false })) {
      throw new Error(`${file.path} existiert bereits; nichts überschrieben`);
    }
  }
  const chartFile = path.join(repoRoot, "platform/charts/web-app/Chart.yaml");
  if (!fs.existsSync(chartFile)) throw new Error("platform/charts/web-app/Chart.yaml fehlt; zuerst das Standard-Chart bereitstellen");
  const chartVersion = parseYaml(fs.readFileSync(chartFile, "utf8"))?.version;
  if (typeof chartVersion !== "string") throw new Error("Standard-Chart hat keine gültige version in Chart.yaml");
  const smokeImage = parseYaml(fs.readFileSync(path.join(repoRoot, "platform/catalog/operations-agent/app.yaml"), "utf8"))?.smokeTest?.image;
  if (typeof smokeImage !== "string" || !/^acr:platform\/smoke-http@sha256:[0-9a-f]{64}$/.test(smokeImage)) {
    throw new Error("operations-agent braucht eine feste smoke-http-Image-Referenz");
  }
  const commit = options.runbookCommit ?? execFileSync("git", ["rev-parse", "HEAD"], { cwd: repoRoot, encoding: "utf8" }).trim();
  if (!/^[0-9a-f]{40}$/.test(commit)) throw new Error("--runbook-commit muss ein vollständiger Commit-SHA mit 40 Hex-Zeichen sein");
  const secrets: Record<string, string> = {};
  if (postgres) Object.assign(secrets, {
    DATABASE_URL: "app:postgres-url", PGHOST: "app:postgres-host", PGDATABASE: "app:postgres-database",
    PGUSER: "app:postgres-user", PGPASSWORD: "app:postgres-password",
  });
  if (blocks.includes("redis")) Object.assign(secrets, { REDIS_HOST: "app:redis-host", REDIS_PASSWORD: "app:redis-password" });
  const backup = [
    ...(postgres ? ["Datenbank über Foundation-PostgreSQL (PITR, 7 Tage)"] : []),
    ...(volume ? ["Volumes ohne Backup (Staging-Risiko)"] : []),
  ].join("; ") || "not-applicable";
  files[0].content = yaml({
    schemaVersion: "0.1", appId, description: options.description,
    source: { repository: options.sourceRepository ?? PLATFORM_REPOSITORY },
    chart: { platform: { name: "web-app" } },
    ingress: { service: appId, port: 80 }, dataBlocks: blocks, secrets,
    availability: { path: healthPath }, smokeTest: { image: smokeImage, env: { CHECK_PATH: healthPath } },
    runbook: { repository: PLATFORM_REPOSITORY, path: "platform/operations/apps/standard-runbook.md", commit },
    allowedActions: ["restart", "redeploy"],
    dataProcedures: { migration: "Die App migriert beim Start selbst oder hat keine Datenbank", backup, restore: "nur manuell" },
  }, {
    source: options.sourceRepository ? "Quell-Repository für spätere Releases." : "Kein Quell-Repository angegeben; das Plattform-Repository verwaltet diesen Eintrag.",
    chart: "Standard-Chart aus dem Plattform-Repository.",
    ingress: "Der Service nutzt Port 80; der Container-Port steht im Sollzustand.",
    secrets: "Nur Referenzen; die Plattform stellt die Werte aus den Datenbausteinen bereit.",
    smokeTest: "Prüft die Erreichbarkeit; einen fachlichen Smoke-Test als nächsten Schritt ergänzen.",
    dataProcedures: "Vor dem ersten Release prüfen; Staging-Volumes haben keine Sicherung.",
  });
  files[1].content = yaml({
    schemaVersion: "0.1", image, chart: { version: chartVersion },
    sizes: { replicas: 1, resources: { requests: { cpu: "50m", memory: "128Mi" }, limits: { memory: "512Mi" } }, ...(volume ? { volumeSize } : {}) },
    values: { container: { port: Number(options.port), env }, probes: { path: healthPath }, ...(volume ? { persistence: { mountPath } } : {}) },
  }, { sizes: "Kleine Anfragen für Staging, Speicherlimit für Lastspitzen.", values: "Container-Port und nicht geheime Konfiguration des Standard-Charts." });

  // Ein eigener Baum prüft nur diese App und erhält dieselben Verträge wie validate.
  const validate = (written: boolean) => {
    const temporary = fs.mkdtempSync(path.join(os.tmpdir(), "app-scaffold-"));
    try {
      for (const file of files) {
        const target = path.join(temporary, file.path);
        fs.mkdirSync(path.dirname(target), { recursive: true });
        if (written) fs.symlinkSync(path.join(repoRoot, file.path), target);
        else fs.writeFileSync(target, file.content);
      }
      const result = validateTree({ catalogDir: path.join(temporary, "platform/catalog"), environmentsDir: path.join(temporary, "platform/environments") }, contracts, repoRoot);
      if (result.errors.length) throw new Error(`Gerüst ungültig:\n${formatIssues(result.errors)}`);
      return result.warnings;
    } finally {
      fs.rmSync(temporary, { recursive: true, force: true });
    }
  };
  let warnings = validate(false);
  if (!options.dryRun) {
    const created: string[] = [];
    try {
      for (const file of files) {
        const target = path.join(repoRoot, file.path);
        fs.mkdirSync(path.dirname(target), { recursive: true });
        fs.writeFileSync(target, file.content, { flag: "wx" });
        created.push(target);
      }
      warnings = validate(true);
    } catch (error) {
      for (const file of created) fs.unlinkSync(file);
      throw error;
    }
  }
  return { files, warnings };
}
