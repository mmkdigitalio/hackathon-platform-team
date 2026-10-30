import * as fs from "node:fs";
import * as path from "node:path";
import { parseArgs } from "node:util";
import { parseDocument } from "yaml";
import {
  derivedNames,
  findRepoRoot,
  formatIssues,
  loadApp,
  loadContracts,
  repositoryLayout,
  validateTree,
  type TreeLayout,
} from "./catalog";
import { affectedStacks } from "./affected";
import { buildDeploymentResult, buildVerificationResult } from "./verification";

const USAGE = `Verwendung: app-catalog <befehl> [optionen]

  validate [--tree <dir>] [--conventions <f>] [--foundation-outputs <f>] [--json]
      Prüft alle Katalogeinträge und Sollzustände (Standard: platform/).
  show --app <id> --env <env>
      Gibt abgeleitete, nicht geheime Angaben einer App als JSON aus.
  set-desired --app <id> --env <env> --image <ref> --chart-version <v> [--upstream-release <r>]
      Setzt Image und Chart-Version im Sollzustand und erhält Kommentare.
  affected --changed <datei> [--mode plan|deploy]
      Ermittelt betroffene Stacks aus einer Liste "<status>\\t<pfad>" (git diff --name-status).
  verification-result --smoke-output <f> --exit-code <n> --app <id> --env <env> --check-ref <sha256:digest>
      --running-git-sha <sha> --running-image-digest <d> --running-chart-version <v>
      --desired-git-sha <sha> --desired-image-digest <d> --desired-chart-version <v>
      [--operation-id <id>] [--workflow-run <url>]
      Baut das Verifikationsergebnis nach Schema und endet mit 1, wenn es kein Erfolg ist.
  deployment-result --operation-id <id> --workflow-run <url> --app <id> --env <env>
      --desired-git-sha <sha> --desired-image-digest <d> --desired-chart-version <v>
      [--verification <f>] [--failed-step <schritt>] [--break-glass] [--action-id <id>]
      Baut das Deployment-Ergebnis; Erfolg nur mit erfolgreicher, zum Sollzustand passender Verifikation.
`;

function contractsFrom(values: Record<string, unknown>, repoRoot: string) {
  return loadContracts(repoRoot, {
    conventions: values.conventions as string | undefined,
    foundationOutputs: values["foundation-outputs"] as string | undefined,
  });
}

function main(argv: string[]): number {
  const [command, ...rest] = argv;
  const { values } = parseArgs({
    args: rest,
    strict: true,
    options: {
      tree: { type: "string" },
      conventions: { type: "string" },
      "foundation-outputs": { type: "string" },
      json: { type: "boolean" },
      app: { type: "string" },
      env: { type: "string" },
      image: { type: "string" },
      "chart-version": { type: "string" },
      "upstream-release": { type: "string" },
      changed: { type: "string" },
      mode: { type: "string" },
      "smoke-output": { type: "string" },
      "exit-code": { type: "string" },
      "check-ref": { type: "string" },
      "running-git-sha": { type: "string" },
      "running-image-digest": { type: "string" },
      "running-chart-version": { type: "string" },
      "desired-git-sha": { type: "string" },
      "desired-image-digest": { type: "string" },
      "desired-chart-version": { type: "string" },
      "operation-id": { type: "string" },
      "workflow-run": { type: "string" },
      "action-id": { type: "string" },
      verification: { type: "string" },
      "failed-step": { type: "string" },
      "break-glass": { type: "boolean" },
    },
  });
  const repoRoot = findRepoRoot(process.cwd());
  const need = (name: string): string => {
    const v = values[name as keyof typeof values];
    if (typeof v !== "string" || v === "") throw new Error(`--${name} fehlt`);
    return v;
  };

  switch (command) {
    case "validate": {
      const layout: TreeLayout = values.tree
        ? { catalogDir: path.join(values.tree, "catalog"), environmentsDir: path.join(values.tree, "environments") }
        : repositoryLayout(repoRoot);
      const result = validateTree(layout, contractsFrom(values, repoRoot), repoRoot);
      if (values.json) {
        console.log(JSON.stringify(result, null, 2));
      } else {
        if (result.warnings.length) console.log(`Warnungen:\n${formatIssues(result.warnings)}`);
        if (result.errors.length) console.error(`Fehler:\n${formatIssues(result.errors)}`);
        else console.log("Katalog und Sollzustände gültig.");
      }
      return result.errors.length ? 1 : 0;
    }
    case "show": {
      const layout = values.tree
        ? { catalogDir: path.resolve(values.tree, "catalog"), environmentsDir: path.resolve(values.tree, "environments") }
        : undefined;
      const app = loadApp(need("app"), need("env"), contractsFrom(values, repoRoot), repoRoot, layout);
      const names = derivedNames(app.appId, app.environment);
      console.log(
        JSON.stringify(
          {
            appId: app.appId,
            environment: app.environment,
            ...names,
            image: app.desired.image,
            chartVersion: app.desired.chart.version,
            replicas: app.desired.sizes.replicas,
            availabilityPath: app.catalog.availability.path,
            smokeImage: app.catalog.smokeTest.image,
            smokeEnv: app.catalog.smokeTest.env ?? {},
            smokeSecretKeys: Object.keys(app.catalog.smokeTest.secrets ?? {}),
            placeholder: /@sha256:0{64}$/.test(app.desired.image),
          },
          null,
          2,
        ),
      );
      return 0;
    }
    case "set-desired": {
      const file = path.join(repositoryLayout(repoRoot).environmentsDir, need("env"), `${need("app")}.yaml`);
      if (!fs.existsSync(file)) throw new Error(`${path.relative(repoRoot, file)} fehlt; neue Apps per manuellem Pull Request anlegen`);
      const doc = parseDocument(fs.readFileSync(file, "utf8"));
      doc.set("image", need("image"));
      doc.setIn(["chart", "version"], need("chart-version"));
      if (values["upstream-release"]) doc.set("upstreamRelease", values["upstream-release"]);
      fs.writeFileSync(file, doc.toString());
      // Ergebnis sofort gegen Schema und Katalog prüfen
      loadApp(need("app"), need("env"), contractsFrom(values, repoRoot), repoRoot);
      console.log(path.relative(repoRoot, file));
      return 0;
    }
    case "affected": {
      const lines = fs.readFileSync(need("changed"), "utf8");
      const mode = (values.mode ?? "plan") as "plan" | "deploy";
      console.log(JSON.stringify(affectedStacks(lines, mode, repositoryLayout(repoRoot), repoRoot)));
      return 0;
    }
    case "verification-result": {
      const result = buildVerificationResult(
        {
          smokeStdout: fs.readFileSync(need("smoke-output"), "utf8"),
          exitCode: Number(need("exit-code")),
          appId: need("app"),
          environment: need("env"),
          checkRef: need("check-ref"),
          running: {
            gitSha: need("running-git-sha"),
            imageDigest: need("running-image-digest"),
            chartVersion: need("running-chart-version"),
          },
          desired: {
            gitSha: need("desired-git-sha"),
            imageDigest: need("desired-image-digest"),
            chartVersion: need("desired-chart-version"),
          },
          operationId: values["operation-id"],
          workflowRun: values["workflow-run"],
        },
        repoRoot,
      );
      console.log(JSON.stringify(result, null, 2));
      return result.success ? 0 : 1;
    }
    case "deployment-result": {
      const verificationFile = values.verification;
      const verification =
        verificationFile && fs.existsSync(verificationFile) && fs.statSync(verificationFile).size > 0
          ? JSON.parse(fs.readFileSync(verificationFile, "utf8"))
          : null;
      const result = buildDeploymentResult(
        {
          operationId: need("operation-id"),
          ...(values["action-id"] ? { actionId: values["action-id"] } : {}),
          workflowRun: need("workflow-run"),
          appId: need("app"),
          environment: need("env"),
          release: {
            gitSha: need("desired-git-sha"),
            imageDigest: need("desired-image-digest"),
            chartVersion: need("desired-chart-version"),
          },
          breakGlass: values["break-glass"] === true,
          verification,
          ...(values["failed-step"] ? { failedStep: values["failed-step"] } : {}),
        },
        repoRoot,
      );
      console.log(JSON.stringify(result, null, 2));
      return result.status === "succeeded" ? 0 : 1;
    }
    default:
      console.error(USAGE);
      return 2;
  }
}

try {
  process.exitCode = main(process.argv.slice(2));
} catch (e) {
  console.error((e as Error).message);
  process.exitCode = 1;
}
