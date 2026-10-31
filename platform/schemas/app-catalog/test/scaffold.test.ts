import { strict as assert } from "node:assert";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { execFileSync, spawnSync } from "node:child_process";
import { afterEach, beforeEach, describe, it } from "node:test";
import { parse as parseYaml } from "yaml";
import { loadApp, loadContracts } from "../src/catalog";
import { addIssueFormOption, scaffoldNewApp, type ScaffoldOptions } from "../src/scaffold";

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

  for (const block of ["postgres-database", "mariadb", "redis", "persistent-volume"]) {
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

  it("verknüpft MariaDB und generierte Secrets ohne Klartextwerte", () => {
    generate({ dataBlocks: "mariadb", generatedSecrets: ["APP_SECRET", "ADMINPASS"] });
    const app = read(catalog());
    assert.deepEqual(app.generatedSecrets, ["app-secret", "adminpass"]);
    assert.deepEqual(app.secrets, {
      DATABASE_URL: "app:mariadb-url", DB_HOST: "app:mariadb-host", DB_PORT: "app:mariadb-port",
      DB_NAME: "app:mariadb-database", DB_USER: "app:mariadb-user", DB_PASSWORD: "app:mariadb-password",
      APP_SECRET: "app:app-secret", ADMINPASS: "app:adminpass",
    });
    assert.match(app.dataProcedures.backup, /MariaDB.*ohne Backup.*Staging-Risiko/);
    assert.equal(read(desired()).sizes.volumeSize, undefined);
  });

  it("erlaubt Datenbank-Variablennamen ohne gewählten Datenbaustein", () => {
    generate({ generatedSecrets: ["DATABASE_URL", "DB_HOST"] });
    assert.deepEqual(read(catalog()).generatedSecrets, ["database-url", "db-host"]);
  });

  it("erzeugt Root-Start mit Begründung und übernimmt runAsNonRoot=false aus dem Chart", () => {
    generate({ rootStart: true });
    assert.deepEqual(read(desired()).values.securityContext, { relaxed: true });
    assert.match(fs.readFileSync(desired(), "utf8"), /Apache\/PHP-Images starten als root.*runAsNonRoot bleibt false/);
    assert.equal(read(path.join(root, "platform/charts/web-app/values.yaml")).securityContext.runAsNonRoot, false);
  });

  for (const [limit, request] of [["512Mi", "128Mi"], ["768Mi", "384Mi"], ["1Gi", "512Mi"], ["2Gi", "512Mi"],
    ["1G", "500000000"], ["2e9", "512Mi"], ["0.5", "128Mi"], ["2", "128Mi"], ["100m", "128Mi"]]) {
    it(`setzt das Speicherlimit ${limit} mit Anfrage ${request}`, () => {
      generate({ memoryLimit: limit, cpuRequest: "100m" });
      assert.deepEqual(read(desired()).sizes.resources, { requests: { cpu: "100m", memory: request }, limits: { memory: limit } });
    });
  }

  for (const cpuRequest of ["0.5", "2"]) {
    it(`akzeptiert CPU-Quantity ${cpuRequest}`, () => {
      generate({ cpuRequest });
      assert.equal(read(desired()).sizes.resources.requests.cpu, cpuRequest);
    });
  }

  const secretInvalid: [string, Partial<ScaffoldOptions>][] = [
    ["Duplikat", { generatedSecrets: ["APP_SECRET", "APP_SECRET"] }],
    ["Kleinbuchstaben", { generatedSecrets: ["app_secret"] }],
    ["Bindestrich", { generatedSecrets: ["APP-SECRET"] }],
    ["Ziffer am Anfang", { generatedSecrets: ["1TOKEN"] }],
    ["leerer Name", { generatedSecrets: [""] }],
    ["zu langer Name", { generatedSecrets: ["A".repeat(64)] }],
    ["SQL-URL", { dataBlocks: "mariadb", generatedSecrets: ["DATABASE_URL"] }],
    ["DB-Variable", { dataBlocks: "mariadb", generatedSecrets: ["DB_CUSTOM"] }],
    ["MariaDB-Schlüssel", { dataBlocks: "mariadb", generatedSecrets: ["MARIADB_PASSWORD"] }],
    ["Postgres-Variable", { dataBlocks: "postgres-database", generatedSecrets: ["PGHOST"] }],
    ["Postgres-Schlüssel", { dataBlocks: "postgres-database", generatedSecrets: ["POSTGRES_URL"] }],
    ["Redis-Schlüssel", { dataBlocks: "redis", generatedSecrets: ["REDIS_PASSWORD"] }],
    ["env-Kollision", { generatedSecrets: ["CUSTOM_VALUE"], env: ["CUSTOM_VALUE=kein-secret"] }],
  ];
  for (const [name, options] of secretInvalid) {
    it(`lehnt generierte Secrets mit ${name} vor dem Schreiben ab`, () => {
      assert.throws(() => generate(options), /--generated-secret|kollidiert|bereits/);
      assert.equal(fs.existsSync(catalog()), false);
      assert.equal(fs.existsSync(desired()), false);
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
    ["zwei SQL-Datenbanken", { dataBlocks: "postgres-database,mariadb" }, /nur eine SQL-Datenbank/],
    ["MariaDB-env-Kollision", { dataBlocks: "mariadb", env: ["DB_HOST=anders"] }, /bereits vom Data-Block/],
    ...["1GB", "512mi", "NaN", "0", "-1Gi", "1 Gi", "1e999"].map((memoryLimit): [string, Partial<ScaffoldOptions>, RegExp] =>
      [`Speicherlimit ${memoryLimit}`, { memoryLimit }, /--memory-limit.*Kubernetes-Quantity/]),
    ["ungültige CPU", { cpuRequest: "hundert" }, /--cpu-request.*Kubernetes-Quantity/],
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

  it("CLI übergibt alle neuen Optionen einschließlich wiederholbarer Secrets", () => {
    const result = runCli([...cliArgs, "--data-blocks", "mariadb", "--generated-secret", "APP_SECRET",
      "--generated-secret", "ADMINPASS", "--root-start", "--memory-limit", "1Gi", "--cpu-request", "100m"]);
    assert.equal(result.status, 0, result.stderr);
    assert.deepEqual(read(catalog()).generatedSecrets, ["app-secret", "adminpass"]);
    assert.equal(read(catalog()).secrets.DATABASE_URL, "app:mariadb-url");
    assert.deepEqual(read(desired()).values.securityContext, { relaxed: true });
    assert.deepEqual(read(desired()).sizes.resources, { requests: { cpu: "100m", memory: "512Mi" }, limits: { memory: "1Gi" } });
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

it("new-app trägt die App alphabetisch ins Support-Issue-Formular ein", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "issue-form-"));
  const file = path.join(root, ".github/ISSUE_TEMPLATE/support.yml");
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, "body:\n  - type: dropdown\n    attributes:\n      label: App\n      options:\n        - affine\n        - platform-status\n  - type: dropdown\n    attributes:\n      label: Umgebung\n      options:\n        - staging\n");
  assert.equal(addIssueFormOption(root, "kimai"), true);
  assert.equal(addIssueFormOption(root, "kimai"), false, "kein Duplikat");
  assert.match(fs.readFileSync(file, "utf8"), /- affine\n {8}- kimai\n {8}- platform-status\n/);
  assert.match(fs.readFileSync(file, "utf8"), /label: Umgebung\n {6}options:\n {8}- staging/);
});
