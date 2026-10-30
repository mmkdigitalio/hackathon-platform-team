import * as fs from "node:fs";
import * as path from "node:path";
import Ajv2020, { type ValidateFunction } from "ajv/dist/2020";
import addFormats from "ajv-formats";
import { schemaDir, SUPPORTED_SCHEMA_VERSION } from "./catalog";

export interface Release {
  gitSha: string;
  imageDigest: string;
  chartVersion: string;
}

export interface Evidence {
  check: string;
  success: boolean;
  detail?: string;
  ref?: string;
}

export interface VerificationResult {
  schemaVersion: string;
  appId: string;
  environment: string;
  release: Release;
  desiredRelease: Release;
  releaseMatchesDesired: boolean;
  deviations: Array<{ field: keyof Release; desired: string; running: string }>;
  checkRef: string;
  startedAt: string;
  finishedAt: string;
  success: boolean;
  exitCode: number;
  failureReason?: string;
  evidence: Evidence[];
  operationId?: string;
  workflowRun?: string;
}

export interface VerificationInput {
  /** Gesamte Ausgabe des Smoke-Test-Containers; das Ergebnis ist die letzte nicht leere Zeile. */
  smokeStdout: string;
  exitCode: number;
  appId: string;
  environment: string;
  checkRef: string;
  running: Release;
  desired: Release;
  operationId?: string;
  workflowRun?: string;
}

interface Schemas {
  smokeOutput: ValidateFunction;
  verification: ValidateFunction;
  deployment: ValidateFunction;
}

let cached: Schemas | undefined;

export function resultSchemas(repoRoot?: string): Schemas {
  if (cached) return cached;
  const ajv = new Ajv2020({ allErrors: true, strict: true });
  addFormats(ajv);
  ajv.addKeyword({ keyword: "x-error", schemaType: "string" });
  const dir = schemaDir(repoRoot);
  const load = (f: string) => JSON.parse(fs.readFileSync(path.join(dir, f), "utf8")) as { $id: string };
  const ids = ["smoke-test-output", "verification-result", "deployment-result"].map((n) => {
    const s = load(`${n}.schema.json`);
    ajv.addSchema(s);
    return s.$id;
  });
  cached = {
    smokeOutput: ajv.getSchema(ids[0])!,
    verification: ajv.getSchema(ids[1])!,
    deployment: ajv.getSchema(ids[2])!,
  };
  return cached;
}

export interface DeploymentResult {
  schemaVersion: string;
  operationId: string;
  actionId?: string;
  workflowRun: string;
  appId: string;
  environment: string;
  release: Release;
  status: "succeeded" | "failed";
  failedStep?: string;
  breakGlass: boolean;
  verification: VerificationResult | null;
}

/** Erfolg nur mit erfolgreicher Verifikation, deren laufendes Release dem Sollzustand entspricht. */
export function buildDeploymentResult(
  input: Omit<DeploymentResult, "schemaVersion" | "status"> & { failedStep?: string },
  repoRoot?: string,
): DeploymentResult {
  const v = input.verification;
  let failedStep = input.failedStep;
  if (!failedStep && !v) failedStep = "verification";
  if (!failedStep && v && !v.success) failedStep = "smoke-test";
  if (!failedStep && v && !v.releaseMatchesDesired) failedStep = "release-mismatch";
  const result: DeploymentResult = {
    schemaVersion: SUPPORTED_SCHEMA_VERSION,
    ...input,
    ...(failedStep ? { failedStep } : {}),
    status: failedStep ? "failed" : "succeeded",
  };
  const validate = resultSchemas(repoRoot).deployment;
  if (!validate(result)) throw new Error(`Deployment-Ergebnis ungültig: ${JSON.stringify(validate.errors)}`);
  return result;
}

function lastLine(text: string): string {
  const lines = text.split("\n").map((l) => l.trim()).filter(Boolean);
  return lines[lines.length - 1] ?? "";
}

/**
 * Wertet einen Smoke-Test nach Vertrag aus: Erfolg nur bei Exit-Code 0, gültigem JSON und success=true.
 * Abweichungen zwischen Soll- und Iststand werden ausgewiesen, ändern aber nicht das Testergebnis.
 */
export function buildVerificationResult(input: VerificationInput, repoRoot?: string): VerificationResult {
  const schemas = resultSchemas(repoRoot);
  const now = new Date().toISOString();
  let output: { success?: boolean; startedAt?: string; finishedAt?: string; evidence?: Evidence[] } = {};
  let failureReason: string | undefined;

  try {
    const parsed: unknown = JSON.parse(lastLine(input.smokeStdout));
    if (!schemas.smokeOutput(parsed)) {
      failureReason = `Ausgabe verletzt den Smoke-Test-Vertrag: ${schemas.smokeOutput.errors
        ?.map((e) => `${e.instancePath || "/"} ${e.message}`)
        .join("; ")}`;
    } else {
      output = parsed as typeof output;
    }
  } catch {
    failureReason = "letzte Ausgabezeile ist kein gültiges JSON";
  }

  if (!failureReason) {
    if (input.exitCode !== 0 && output.success === true) {
      failureReason = `Exit-Code ${input.exitCode} widerspricht success=true`;
    } else if (input.exitCode === 0 && output.success !== true) {
      failureReason = "Exit-Code 0, aber success ist nicht true";
    } else if (input.exitCode !== 0) {
      failureReason = "Smoke-Test meldet Fehlschlag";
    }
  }

  const deviations = (Object.keys(input.desired) as Array<keyof Release>)
    .filter((k) => input.desired[k] !== input.running[k])
    .map((field) => ({ field, desired: input.desired[field], running: input.running[field] }));

  const result: VerificationResult = {
    schemaVersion: SUPPORTED_SCHEMA_VERSION,
    appId: input.appId,
    environment: input.environment,
    release: input.running,
    desiredRelease: input.desired,
    releaseMatchesDesired: deviations.length === 0,
    deviations,
    checkRef: input.checkRef,
    startedAt: output.startedAt ?? now,
    finishedAt: output.finishedAt ?? now,
    success: failureReason === undefined,
    exitCode: input.exitCode,
    ...(failureReason ? { failureReason } : {}),
    evidence: output.evidence ?? [],
    ...(input.operationId ? { operationId: input.operationId } : {}),
    ...(input.workflowRun ? { workflowRun: input.workflowRun } : {}),
  };
  if (!schemas.verification(result)) {
    throw new Error(`Verifikationsergebnis ungültig: ${JSON.stringify(schemas.verification.errors)}`);
  }
  return result;
}
