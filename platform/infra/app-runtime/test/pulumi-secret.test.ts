import { strict as assert } from "node:assert";
import { execFileSync } from "node:child_process";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { it } from "node:test";

it("übergibt den CI-App-Schlüssel nur beim Agenten, als Secret über stdin", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "pulumi-secret-"));
  try {
    const app = path.join(root, "app");
    fs.mkdirSync(path.join(app, "scripts"), { recursive: true });
    fs.mkdirSync(path.join(app, "bin/infra/app-runtime/src"), { recursive: true });
    fs.writeFileSync(path.join(app, "bin/infra/app-runtime/src/index.js"), "");
    const script = path.join(app, "scripts/pulumi-stack.sh");
    fs.copyFileSync(path.resolve(__dirname, "../scripts/pulumi-stack.sh"), script);
    const bin = path.join(root, "bin");
    fs.mkdirSync(bin);
    fs.writeFileSync(path.join(bin, "pulumi"), `#!/usr/bin/env python3
import json, os, sys
with open(os.environ["TEST_CALLS"], "a") as file:
    file.write(json.dumps({"args": sys.argv[1:], "stdin": sys.stdin.read() if "--secret" in sys.argv else ""}) + "\\n")
`, { mode: 0o755 });
    const key = "test-key-line-1\ntest-key-line-2\n";
    const callsFile = path.join(root, "calls.jsonl");
    const env = {
      ...process.env, PATH: `${bin}:${process.env.PATH}`, TEST_CALLS: callsFile,
      PULUMI_BACKEND_URL: "file:///unused", PULUMI_CONFIG_PASSPHRASE: "test",
      APP_RUNTIME_SKIP_BUILD: "1", APP_RUNTIME_GITHUB_APP_PRIVATE_KEY: key,
    };
    execFileSync("bash", [script, "operations-agent", "staging", "preview"], { env });
    const calls = fs.readFileSync(callsFile, "utf8").trim().split("\n").map((s) => JSON.parse(s));
    const secret = calls.filter((call) => call.args.includes("--secret"));
    assert.equal(secret.length, 1);
    assert.ok(secret[0].args.includes("app-runtime:githubAppPrivateKey"));
    assert.equal(secret[0].stdin, key);
    assert.ok(!JSON.stringify(calls.map((call) => call.args)).includes("test-key-line"));
    fs.writeFileSync(callsFile, "");
    execFileSync("bash", [script, "affine", "staging", "preview"], { env });
    const otherCalls = fs.readFileSync(callsFile, "utf8").trim().split("\n").map((s) => JSON.parse(s));
    assert.ok(!otherCalls.some((call) => call.args.includes("--secret")));
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});
