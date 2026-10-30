import * as path from "node:path";
import * as pulumi from "@pulumi/pulumi";
import { findRepoRoot } from "../../../schemas/app-catalog/src/catalog";
import { appStack } from "./program";

// Stack organization/app-<app-id>/<env>: App-ID aus dem Projekt-, Umgebung aus dem Stack-Namen
const project = pulumi.getProject();
if (!project.startsWith("app-")) throw new Error(`Projektname ${project} folgt nicht app-<app-id>`);
const environment = pulumi.getStack();
const config = new pulumi.Config("app-runtime");
const repoRoot = findRepoRoot(__dirname);
// Nur lokal: Baum mit catalog/ und environments/ statt platform/ (z. B. dev/test-tree)
const treeDir = config.get("treeDir");

export = appStack({
  appId: project.slice("app-".length),
  environment,
  repoRoot,
  layout: treeDir
    ? {
        catalogDir: path.resolve(repoRoot, treeDir, "catalog"),
        environmentsDir: path.resolve(repoRoot, treeDir, "environments"),
      }
    : undefined,
  foundationStack: config.get("foundationStack") ?? `organization/foundation/${environment}`,
  expectedContractVersion: config.getNumber("expectedContractVersion") ?? 1,
  gitSha: config.get("gitSha") ?? "local",
  operationId: config.get("operationId") ?? `local-${Date.now()}`,
  storageClass: config.get("storageClass") ?? "managed-csi",
  ingressClassName: config.get("ingressClassName"),
  clusterIssuer: config.get("clusterIssuer"),
  publicScheme: config.get("publicScheme") as "https" | "http" | undefined,
  postgresSslMode: config.get("postgresSslMode") ?? "require",
});
