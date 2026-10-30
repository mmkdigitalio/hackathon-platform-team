import { strict as assert } from "node:assert";
import * as fs from "node:fs";
import * as path from "node:path";
import { describe, it } from "node:test";
import { buildDeploymentResult, buildVerificationResult, resultSchemas, type VerificationInput } from "../src/verification";

const base = path.resolve(__dirname, "..");
const release = { gitSha: "a".repeat(40), imageDigest: `sha256:${"b".repeat(64)}`, chartVersion: "0.1.0" };
const input = (over: Partial<VerificationInput>): VerificationInput => ({
  smokeStdout: "",
  exitCode: 0,
  appId: "affine",
  environment: "staging",
  checkRef: `ghcr.io/mmkdigitalio/affine-smoke@sha256:${"c".repeat(64)}`,
  running: release,
  desired: release,
  ...over,
});
const example = (name: string) => fs.readFileSync(path.join(base, "examples/results", name), "utf8");

describe("Verifikationsergebnis", () => {
  it("Erfolg bei Exit-Code 0 und success=true", () => {
    const r = buildVerificationResult(input({ smokeStdout: example("smoke-output-success.json") }));
    assert.equal(r.success, true);
    assert.equal(r.releaseMatchesDesired, true);
    assert.equal(r.evidence.length, 3);
  });

  it("erreichbar, aber fachlich defekt: Exit-Code 1 und success=false", () => {
    const r = buildVerificationResult(input({ smokeStdout: example("smoke-output-failed.json"), exitCode: 1 }));
    assert.equal(r.success, false);
    assert.match(r.failureReason!, /Fehlschlag/);
    assert.deepEqual(JSON.parse(example("verification-result-failed.json")).evidence, r.evidence);
  });

  it("Beispiel eines fehlgeschlagenen Ergebnisses ist schemakonform", () => {
    const v = resultSchemas().verification;
    assert.ok(v(JSON.parse(example("verification-result-failed.json"))), JSON.stringify(v.errors));
  });

  it("Exit-Code 0 ohne gültiges JSON gilt als Fehlschlag", () => {
    const r = buildVerificationResult(input({ smokeStdout: "alles gut\n" }));
    assert.equal(r.success, false);
    assert.match(r.failureReason!, /kein gültiges JSON/);
  });

  it("Exit-Code 0 mit success=false gilt als Fehlschlag", () => {
    const r = buildVerificationResult(input({ smokeStdout: example("smoke-output-failed.json") }));
    assert.equal(r.success, false);
    assert.match(r.failureReason!, /Exit-Code 0, aber success/);
  });

  it("Exit-Code ungleich 0 mit success=true gilt als Fehlschlag", () => {
    const r = buildVerificationResult(input({ smokeStdout: example("smoke-output-success.json"), exitCode: 3 }));
    assert.equal(r.success, false);
    assert.match(r.failureReason!, /widerspricht success=true/);
  });

  it("Diagnosezeilen vor der JSON-Zeile sind erlaubt", () => {
    const r = buildVerificationResult(
      input({ smokeStdout: `prüfe login\nprüfe dokument\n${example("smoke-output-success.json")}` }),
    );
    assert.equal(r.success, true);
  });

  it("weist eine Abweichung des laufenden Digests vom Sollzustand aus", () => {
    const running = { ...release, imageDigest: `sha256:${"d".repeat(64)}` };
    const r = buildVerificationResult(input({ smokeStdout: example("smoke-output-success.json"), running }));
    assert.equal(r.releaseMatchesDesired, false);
    assert.deepEqual(r.deviations, [{ field: "imageDigest", desired: release.imageDigest, running: running.imageDigest }]);
    assert.equal(r.release.imageDigest, running.imageDigest);
  });
});

describe("Deployment-Ergebnis", () => {
  const base = {
    operationId: "gha-1-1-affine-staging",
    workflowRun: "https://github.com/mmkdigitalio/hackathon-platform-team/actions/runs/1",
    appId: "affine",
    environment: "staging",
    release,
    breakGlass: false,
  };
  const ok = () => buildVerificationResult(input({ smokeStdout: example("smoke-output-success.json") }));

  it("ist erfolgreich nur mit erfolgreicher, zum Sollzustand passender Verifikation", () => {
    assert.equal(buildDeploymentResult({ ...base, verification: ok() }).status, "succeeded");
  });

  it("ohne Verifikation gilt ein nur gestarteter Lauf nicht als erfolgreich", () => {
    const r = buildDeploymentResult({ ...base, verification: null });
    assert.equal(r.status, "failed");
    assert.equal(r.failedStep, "verification");
  });

  it("scheitert mit dem Verifikationsergebnis, wenn der Smoke-Test scheitert", () => {
    const v = buildVerificationResult(input({ smokeStdout: example("smoke-output-failed.json"), exitCode: 1 }));
    const r = buildDeploymentResult({ ...base, verification: v });
    assert.equal(r.status, "failed");
    assert.equal(r.failedStep, "smoke-test");
    assert.equal(r.verification?.success, false);
  });

  it("scheitert, wenn ein anderes Release läuft als gewünscht", () => {
    const running = { ...release, chartVersion: "0.0.9" };
    const v = buildVerificationResult(input({ smokeStdout: example("smoke-output-success.json"), running }));
    assert.equal(buildDeploymentResult({ ...base, verification: v }).failedStep, "release-mismatch");
  });
});
