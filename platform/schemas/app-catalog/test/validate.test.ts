import { strict as assert } from "node:assert";
import * as fs from "node:fs";
import * as path from "node:path";
import * as os from "node:os";
import { parse as parseYaml } from "yaml";
import { describe, it } from "node:test";
import { formatIssues, loadContracts, validateTree, validateCatalogEntry, validateDesiredState } from "../src/catalog";

const base = path.resolve(__dirname, "..");
const repoRoot = path.resolve(base, "../../..");
// A's Verträge unter platform/schemas/conventions/ und platform/schemas/foundation-outputs/
const contracts = loadContracts(repoRoot);
const tree = (dir: string) => ({ catalogDir: path.join(dir, "catalog"), environmentsDir: path.join(dir, "environments") });

describe("gültige Beispiele", () => {
  it("AFFiNE-artiger Eintrag, operations-agent und whoami bestehen die Validierung", () => {
    const result = validateTree(tree(path.join(base, "examples/valid")), contracts, repoRoot);
    assert.deepEqual(result.errors, [], formatIssues(result.errors));
  });
});

describe("ungültige Beispiele", () => {
  const invalidDir = path.join(base, "examples/invalid");
  for (const name of fs.readdirSync(invalidDir).sort()) {
    it(name, () => {
      const expected = fs.readFileSync(path.join(invalidDir, name, "expect.txt"), "utf8").trim();
      const result = validateTree(tree(path.join(invalidDir, name)), contracts, repoRoot);
      const text = formatIssues(result.errors);
      assert.ok(result.errors.length > 0, "Validierung hätte scheitern müssen");
      assert.ok(text.includes(expected), `erwartet "${expected}" in:\n${text}`);
    });
  }
});

describe("keine Secret-Werte in Ausgaben", () => {
  it("ein Klartextwert statt Referenz erscheint nicht in der Fehlermeldung", () => {
    const result = validateTree(tree(path.join(base, "examples/invalid/plaintext-secret")), contracts, repoRoot);
    assert.ok(!formatIssues(result.errors).includes("hunter2"));
  });
});

describe("Verträge von Strang A", () => {
  it("fehlende Konventionen brechen mit Hinweis auf die Lieferung ab", () => {
    assert.throws(
      () => loadContracts(repoRoot, { conventions: path.join(base, "gibt-es-nicht.json") }),
      /Konventionen fehlt: .*Lieferung von Strang A/,
    );
  });
});


describe("lokale Plattform-Charts", () => {
  const entry = () => parseYaml(fs.readFileSync(path.join(base, "examples/valid/catalog/whoami/app.yaml"), "utf8"));
  const desired = () => parseYaml(fs.readFileSync(path.join(base, "examples/valid/environments/staging/whoami.yaml"), "utf8"));

  it("akzeptiert genau eine Chart-Quelle als Objekt", () => {
    for (const chart of [{ platform: "web-app" }, { platform: { name: "../web-app" } },
      { platform: { name: "/tmp/web-app" } }, { platform: { name: "web-app" }, acr: { repository: "helm/demo" } }]) {
      const result = validateCatalogEntry("app.yaml", { ...entry(), chart }, "whoami", contracts, repoRoot);
      assert.ok(result.errors.length > 0, JSON.stringify(chart));
    }
  });

  it("vergleicht die vollständige Chart-Version einschließlich Prärelease", () => {
    const doc = desired();
    doc.chart.version = "0.1.0-rc.1";
    const result = validateDesiredState("whoami.yaml", doc, entry(), repoRoot);
    assert.ok(formatIssues(result.errors).includes("0.1.0 aus Chart.yaml"));
  });

  it("meldet fehlende oder unlesbare Chart-Metadaten auf Deutsch", () => {
    const temp = fs.mkdtempSync(path.join(os.tmpdir(), "platform-chart-validation-"));
    try {
      fs.mkdirSync(path.join(temp, "platform/schemas"), { recursive: true });
      fs.cpSync(path.join(base, "v0.1"), path.join(temp, "platform/schemas/app-catalog/v0.1"), { recursive: true });
      const chartDir = path.join(temp, "platform/charts/web-app");
      fs.mkdirSync(chartDir, { recursive: true });
      for (const content of [undefined, "version: [", "name: web-app", "version: 1"]) {
        if (content !== undefined) fs.writeFileSync(path.join(chartDir, "Chart.yaml"), content);
        const result = validateCatalogEntry("app.yaml", entry(), "whoami", contracts, temp);
        assert.ok(formatIssues(result.errors).includes("Chart.yaml fehlt oder enthält keine gültige version"));
      }
      fs.rmSync(chartDir, { recursive: true });
      fs.writeFileSync(chartDir, "kein Verzeichnis");
      const result = validateCatalogEntry("app.yaml", entry(), "whoami", contracts, temp);
      assert.ok(formatIssues(result.errors).includes("Plattform-Chart 'web-app' fehlt"));
    } finally {
      fs.rmSync(temp, { recursive: true, force: true });
    }
  });
});
