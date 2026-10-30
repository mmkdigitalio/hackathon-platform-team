import { strict as assert } from "node:assert";
import * as fs from "node:fs";
import * as path from "node:path";
import { describe, it } from "node:test";
import { formatIssues, loadContracts, validateTree } from "../src/catalog";

const base = path.resolve(__dirname, "..");
const repoRoot = path.resolve(base, "../../..");
const contracts = loadContracts(repoRoot, {
  conventions: path.join(base, "test/fixtures/conventions.json"),
  foundationOutputs: path.join(base, "test/fixtures/foundation-outputs.schema.json"),
});
const tree = (dir: string) => ({ catalogDir: path.join(dir, "catalog"), environmentsDir: path.join(dir, "environments") });

describe("gültige Beispiele", () => {
  it("AFFiNE-artiger Eintrag und operations-agent bestehen die Validierung", () => {
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
