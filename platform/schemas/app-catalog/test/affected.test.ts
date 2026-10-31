import { strict as assert } from "node:assert";
import * as path from "node:path";
import { describe, it } from "node:test";
import { affectedStacks } from "../src/affected";

const base = path.resolve(__dirname, "..");
const layout = {
  catalogDir: path.join(base, "examples/valid/catalog"),
  environmentsDir: path.join(base, "examples/valid/environments"),
};
const run = (diff: string, mode: "plan" | "deploy") => affectedStacks(diff, mode, layout, base);

describe("betroffene Stacks", () => {
  it("Sollzustandsänderung betrifft genau diesen Stack", () => {
    const r = run("M\tplatform/environments/staging/affine.yaml\n", "deploy");
    assert.deepEqual(r.stacks, [{ appId: "affine", environment: "staging" }]);
  });

  it("Katalogänderung löst kein Deployment aus, aber ein Preview", () => {
    const diff = "M\tplatform/catalog/affine/app.yaml\n";
    assert.deepEqual(run(diff, "deploy").stacks, []);
    assert.deepEqual(run(diff, "plan").stacks, [{ appId: "affine", environment: "staging" }]);
  });

  it("Änderung am App-Programm betrifft im Plan alle Stacks", () => {
    const r = run("M\tplatform/infra/app-runtime/src/program.ts\n", "plan");
    assert.equal(r.stacks.length, 3);
  });

  it("Änderung an einem Plattform-Chart betrifft im Plan nur die Apps, die es verwenden", () => {
    const r = run("M\tplatform/charts/web-app/templates/deployment.yaml\n", "plan");
    assert.deepEqual(r.stacks, [{ appId: "whoami", environment: "staging" }]);
    assert.deepEqual(run("M\tplatform/charts/web-app/values.yaml\n", "deploy").stacks, []);
  });

  it("gelöschter Sollzustand führt zu keinem Stack, sondern zum Außerbetriebnahme-Hinweis", () => {
    const r = run("D\tplatform/environments/staging/affine.yaml\n", "deploy");
    assert.deepEqual(r.stacks, []);
    assert.deepEqual(r.decommission, [
      { appId: "affine", environment: "staging", file: "platform/environments/staging/affine.yaml" },
    ]);
  });
});
