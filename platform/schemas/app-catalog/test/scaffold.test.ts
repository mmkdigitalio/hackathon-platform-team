import { strict as assert } from "node:assert";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { execFileSync, spawnSync } from "node:child_process";
import { afterEach, beforeEach, describe, it } from "node:test";
import { parse as parseYaml } from "yaml";
import { loadApp, loadContracts } from "../src/catalog";
import { scaffoldNewApp, type ScaffoldOptions } from "../src/scaffold";

const repoRoot = path.resolve(__dirname, "../../../..");
const cli = path.resolve(__dirname, "../src/cli.ts");
const digest = "a".repeat(64);
const commit = "b".repeat(40);
const defaults: ScaffoldOptions = {
  appId: "zeiterfassung", description: "Zeiten erfassen", image: `ghcr.io/mmkdigitalio/zeiterfassung@sha256:${digest}`,
  port: "8080", runbookCommit: commit,
};

describe("Gerüst für neue Apps", () => {
  let root: string;
  const catalog = () => path.join(root, "platform/catalog/zeiterfassung/app.yaml");
  const desired = () => path.join(root, "platform/environments/staging/zeiterfassung.yaml");
  const generate = (options: Partial<ScaffoldOptions> = {}) => scaffoldNewApp(root, { ...defaults, ...options });
  const read = (file: string) => parseYaml(fs.readFileSync(file, "utf8"));
  const runCli = (args: string[]) => spawnSync(process.execPath, ["--import", require.resolve("tsx"), cli, ...args], {
    cwd: root, encoding: "utf8",
  });
  const cliArgs = ["new-app", "--app-id", defaults.appId, "--description", defaults.description,
    "--image", defaults.image!, "--port", defaults.port, "--runbook-commit", commit];

  beforeEach(() => {
    root = fs.mkdtempSync(path.join(os.tmpdir(), "scaffold-test-"));
    // Verträge und Chart unverändert übernehmen; Zieldateien bleiben im temporären Repository.
    for (const relative of ["platform/schemas/app-catalog/v0.1", "platform/schemas/conventions", "platform/schemas/foundation-outputs",
      "platform/charts/web-app", "platform/catalog/operations-agent/app.yaml"]) {
      fs.cpSync(path.join(repoRoot, relative), path.join(root, relative), { recursive: true });
    }
  });

  afterEach(() => fs.rmSync(root, { recursive: true, force: true }));

  it("erzeugt ohne Datenbausteine einen gültigen Katalog und Sollzustand", () => {
    generate({ sourceRepository: "mmkdigitalio/zeiterfassung", healthPath: "/health", env: ["LOG_LEVEL=info", "MESSAGE=a=b", "language=de"] });
    const app = loadApp(defaults.appId, "staging", loadContracts(root), root);
    assert.deepEqual(app.catalog.chart, { platform: { name: "web-app" } });
    assert.deepEqual(app.catalog.source, { repository: "mmkdigitalio/zeiterfassung" });
    assert.deepEqual(app.catalog.ingress, { service: defaults.appId, port: 80 });
    assert.deepEqual(app.catalog.dataBlocks, []);
    assert.deepEqual(app.catalog.secrets, {});
    assert.deepEqual(app.catalog.allowedActions, ["restart", "redeploy"]);
    assert.deepEqual(app.catalog.runbook, { repository: "mmkdigitalio/hackathon-platform-team", path: "platform/operations/apps/standard-runbook.md", commit });
    assert.equal(app.catalog.smokeTest.image, read(path.join(root, "platform/catalog/operations-agent/app.yaml")).smokeTest.image);
    assert.deepEqual(app.catalog.smokeTest.env, { CHECK_PATH: "/health" });
    assert.deepEqual(app.catalog.availability, { path: "/health" });
    assert.deepEqual(app.desired.values, { container: { port: 8080, env: { LOG_LEVEL: "info", MESSAGE: "a=b", language: "de" } }, probes: { path: "/health" } });
    assert.deepEqual(app.desired.sizes, { replicas: 1, resources: { requests: { cpu: "50m", memory: "128Mi" }, limits: { memory: "512Mi" } } });
    assert.deepEqual(app.catalog.dataProcedures, { migration: "Die App migriert beim Start selbst oder hat keine Datenbank", backup: "not-applicable", restore: "nur manuell" });
    assert.match(fs.readFileSync(catalog(), "utf8"), /# Der Service nutzt Port 80/);
  });

  it("verknüpft alle Datenbausteine mit den exakten Secret-Referenzen", () => {
    generate({ image: undefined, acrImage: `zeiterfassung@sha256:${digest}`, dataBlocks: "postgres-database,redis,persistent-volume" });
    const app = read(catalog());
    assert.deepEqual(app.secrets, {
      DATABASE_URL: "app:postgres-url", PGHOST: "app:postgres-host", PGDATABASE: "app:postgres-database", PGUSER: "app:postgres-user",
      PGPASSWORD: "app:postgres-password", REDIS_HOST: "app:redis-host", REDIS_PASSWORD: "app:redis-password",
    });
    assert.equal(read(desired()).image, `acr:zeiterfassung@sha256:${digest}`);
    assert.equal(read(desired()).sizes.volumeSize, "5Gi");
    assert.deepEqual(read(desired()).values.persistence, { mountPath: "/data" });
    assert.match(app.dataProcedures.backup, /Foundation-PostgreSQL.*PITR, 7 Tage.*Volumes ohne Backup \(Staging-Risiko\)/);
  });

  for (const block of ["postgres-database", "redis", "persistent-volume"]) {
    it(`erzeugt auch den einzelnen Datenbaustein ${block}`, () => {
      generate({ dataBlocks: block, ...(block === "persistent-volume" ? { volumeSize: "8Gi", mountPath: "/var/lib/app" } : {}) });
      assert.deepEqual(read(catalog()).dataBlocks, [block]);
      if (block === "persistent-volume") {
        assert.equal(read(desired()).sizes.volumeSize, "8Gi");
        assert.equal(read(desired()).values.persistence.mountPath, "/var/lib/app");
      } else {
        assert.equal(read(desired()).sizes.volumeSize, undefined);
        assert.equal(read(desired()).values.persistence, undefined);
      }
    });
  }

  it("liest die Version aus Chart.yaml zur Laufzeit", () => {
    const file = path.join(root, "platform/charts/web-app/Chart.yaml");
    fs.writeFileSync(file, fs.readFileSync(file, "utf8").replace(/^version:.*$/m, "version: 0.9.7"));
    generate();
    assert.equal(read(desired()).chart.version, "0.9.7");
  });

  it("nutzt ohne Quellangabe das Plattform-Repository und erklärt dies", () => {
    generate();
    assert.equal(read(catalog()).source.repository, "mmkdigitalio/hackathon-platform-team");
    assert.match(fs.readFileSync(catalog(), "utf8"), /Kein Quell-Repository angegeben/);
  });

  for (const appId of ["Meine_App", "../fremde-app", "demo-", "foundation", "a"]) {
    it(`lehnt die ungültige oder reservierte App-ID ${appId} ohne Dateien ab`, () => {
      assert.throws(() => generate({ appId }), /Konvention.*Beispiel: zeiterfassung/);
      assert.equal(fs.existsSync(desired()), false);
      assert.deepEqual(fs.readdirSync(path.join(root, "platform/catalog")), ["operations-agent"]);
    });
  }

  for (const target of ["catalog", "desired"]) {
    it(`überschreibt einen vorhandenen ${target}-Eintrag nicht und schreibt keine zweite Datei`, () => {
      const file = target === "catalog" ? catalog() : desired();
      fs.mkdirSync(path.dirname(file), { recursive: true });
      fs.writeFileSync(file, "Fremder Inhalt\n");
      assert.throws(() => generate(), /existiert bereits; nichts überschrieben/);
      assert.equal(fs.readFileSync(file, "utf8"), "Fremder Inhalt\n");
      assert.equal(fs.existsSync(target === "catalog" ? desired() : catalog()), false);
    });
  }

  for (const key of ["DATABASE_PASSWORD", "client_secret", "Api_Token", "PUBLIC_KEY"]) {
    it(`lehnt Secret-artiges --env ${key} ohne Ausgabe des Werts ab`, () => {
      assert.throws(() => generate({ env: [`${key}=nicht-ausgeben`] }), (error: Error) => {
        assert.match(error.message, /stattdessen Data-Blocks nutzen/);
        assert.ok(!error.message.includes("nicht-ausgeben"));
        return true;
      });
      assert.equal(fs.existsSync(catalog()), false);
      assert.equal(fs.existsSync(desired()), false);
    });
  }

  const invalid: [string, Partial<ScaffoldOptions>, RegExp][] = [
    ["fehlender Digest", { image: "ghcr.io/mmkdigitalio/app:latest" }, /@sha256-Digest/],
    ["beide Image-Optionen", { acrImage: `app@sha256:${digest}` }, /Genau eines/],
    ["keine Image-Option", { image: undefined }, /Genau eines/],
    ["falsche Umgebung", { environment: "production" }, /nur --environment staging/],
    ["unbekannter Datenbaustein", { dataBlocks: "mysql" }, /--data-blocks erlaubt/],
    ["doppelter Datenbaustein", { dataBlocks: "redis,redis" }, /jeweils einmal/],
    ["Volume-Option ohne Volume", { volumeSize: "8Gi" }, /brauchen den Datenbaustein/],
    ["ungültiger Port", { port: "65536" }, /--port muss/],
    ["ungültiger Prüfpfad", { healthPath: "health" }, /--health-path muss/],
    ["ungültiger Commit", { runbookCommit: "main" }, /vollständiger Commit-SHA/],
    ["Secret-Kollision", { dataBlocks: "postgres-database", env: ["PGHOST=anders"] }, /bereits vom Data-Block/],
    ["fehlendes Gleichheitszeichen", { env: ["LOG_LEVEL"] }, /KEY=VALUE/],
  ];
  for (const [name, options, message] of invalid) {
    it(`lehnt ${name} vor dem Schreiben ab`, () => {
      assert.throws(() => generate(options), message);
      assert.equal(fs.existsSync(catalog()), false);
      assert.equal(fs.existsSync(desired()), false);
    });
  }

  it("prüft ungültige Image-Referenzen auch gegen das Katalogschema vor dem Schreiben", () => {
    assert.throws(() => generate({ image: `UNGÜLTIG@sha256:${digest}` }), /Gerüst ungültig/);
    assert.equal(fs.existsSync(catalog()), false);
  });

  it("CLI dry-run gibt beide YAML-Dokumente aus und schreibt nichts", () => {
    const result = runCli([...cliArgs, "--dry-run", "--env", "LOG_LEVEL=info", "--env", "MESSAGE=a=b"]);
    assert.equal(result.status, 0, result.stderr);
    const documents = result.stdout.split(/^---\n/m).filter(Boolean).map((doc) => parseYaml(doc));
    assert.equal(documents.length, 2);
    assert.equal(documents[0].appId, defaults.appId);
    assert.deepEqual(documents[1].values.container.env, { LOG_LEVEL: "info", MESSAGE: "a=b" });
    assert.equal(fs.existsSync(catalog()), false);
    assert.equal(fs.existsSync(desired()), false);
  });

  it("CLI schreibt beide Dateien und gibt nächste Schritte aus", () => {
    const result = runCli(cliArgs);
    assert.equal(result.status, 0, result.stderr);
    assert.ok(fs.existsSync(catalog()));
    assert.ok(fs.existsSync(desired()));
    assert.match(result.stdout, /Nächste Schritte:.*Pull Request.*app-plan/);
    assert.equal(runCli(cliArgs).status, 1);
  });

  it("CLI meldet ungültige Optionen auf Deutsch und gibt Secret-Werte nicht aus", () => {
    const result = runCli([...cliArgs, "--unbekannt=nicht-ausgeben"]);
    assert.equal(result.status, 1);
    assert.match(result.stderr, /Ungültige Optionen für new-app/);
    assert.ok(!result.stderr.includes("nicht-ausgeben"));
    assert.equal(fs.existsSync(catalog()), false);
  });

  it("CLI behält die einzelne --env-Option bestehender Befehle bei", () => {
    generate();
    const result = runCli(["show", "--app", defaults.appId, "--env", "staging"]);
    assert.equal(result.status, 0, result.stderr);
    assert.equal(JSON.parse(result.stdout).environment, "staging");
  });

  it("liest den Runbook-Commit ohne Flag mit git rev-parse HEAD", () => {
    const expected = execFileSync("git", ["rev-parse", "HEAD"], { cwd: repoRoot, encoding: "utf8" }).trim();
    // Nur den vorhandenen Git-Verweis lesen, keine Commits im Test anlegen.
    const gitDir = execFileSync("git", ["rev-parse", "--absolute-git-dir"], { cwd: repoRoot, encoding: "utf8" }).trim();
    fs.writeFileSync(path.join(root, ".git"), `gitdir: ${gitDir}\n`);
    generate({ runbookCommit: undefined });
    assert.equal(read(catalog()).runbook.commit, expected);
  });
});
