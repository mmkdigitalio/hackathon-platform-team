import { strict as assert } from "node:assert";
import * as path from "node:path";
import { it } from "node:test";
import * as pulumi from "@pulumi/pulumi";
import { FOUNDATION } from "./foundation-fixture";

const repoRoot = path.resolve(__dirname, "../../../..");
const catalogDir = path.join(repoRoot, "platform/schemas/app-catalog");

// Fehlgeschlagene Registrierungen erscheinen als unbehandelte Ablehnungen; hier sind sie das erwartete Ergebnis
const rejections: string[] = [];

it("bricht bei anderer contractVersion ab, bevor eine Kubernetes- oder Datenbankressource registriert wird", async () => {
  const types: string[] = [];
  process.removeAllListeners("unhandledRejection");
  process.on("unhandledRejection", (e) => rejections.push(String(e)));
  pulumi.runtime.setMocks(
    {
      newResource(args) {
        types.push(args.type);
        if (args.type === "pulumi:pulumi:StackReference") {
          return { id: args.name, state: { outputs: { ...FOUNDATION, contractVersion: 2 } } };
        }
        return { id: `${args.name}-id`, state: args.inputs };
      },
      call: (args) => args.inputs,
    },
    "app-operations-agent",
    "staging",
    false,
  );
  const { appStack } = await import("../src/program");
  appStack({
    appId: "operations-agent",
    environment: "staging",
    repoRoot,
    layout: {
      catalogDir: path.join(catalogDir, "examples/valid/catalog"),
      environmentsDir: path.join(catalogDir, "examples/valid/environments"),
    },
    foundationStack: "organization/foundation/staging",
    expectedContractVersion: 1,
    gitSha: "local",
    operationId: "op-test",
    storageClass: "managed-csi",
    postgresSslMode: "require",
  });
  await new Promise((r) => setTimeout(r, 500));
  assert.deepEqual([...new Set(types)], ["pulumi:pulumi:StackReference"]);
  assert.ok(rejections.some((r) => r.includes("erwartet contractVersion 1, vorhanden 2")), rejections.join("\n"));
});
