// Task 1.1: CODEOWNERS assigns every area of openspec/PARALLEL-IMPLEMENTATION.md to its
// strand. Owners are resolved like GitHub does: the last matching pattern wins.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const text = readFileSync(new URL("../../.github/CODEOWNERS", import.meta.url), "utf8");

const strandOwners = Object.fromEntries(
    [...text.matchAll(/^# Strand ([A-D]): (@\S+)/gm)].map(([, strand, owner]) => [owner, strand]),
);

const rules = text
    .split("\n")
    .filter((line) => line.trim() !== "" && !line.startsWith("#"))
    .map((line) => {
        const [pattern, ...owners] = line.trim().split(/\s+/);
        const body = pattern
            .replace(/^\//, "")
            .replace(/[.+?^${}()|[\]\\]/g, "\\$&")
            .replaceAll("*", "[^/]*");
        const anchored = pattern.startsWith("/") ? "^" : "(^|/)";
        const regex = pattern === "*" ? /.*/ : new RegExp(`${anchored}${body}${pattern.endsWith("/") ? "" : "(/|$)"}`);
        return { pattern, owners, regex };
    });

function strandOf(path) {
    const rule = rules.findLast(({ regex }) => regex.test(path));
    assert.ok(rule, `${path} has no owner`);
    assert.equal(rule.owners.length, 1, `${rule.pattern} names one strand owner`);
    return strandOwners[rule.owners[0]];
}

// One sample path per row of "Dateizuständigkeit im Monorepo".
const expected = {
    ".gitignore": "A",
    "README.md": "A",
    ".github/CODEOWNERS": "A",
    "platform/infra/foundation/index.ts": "A",
    "platform/infra/components/conventions.ts": "A",
    "platform/scripts/bootstrap/bootstrap.sh": "A",
    "platform/operations/foundation/handover.md": "A",
    "platform/schemas/conventions/conventions.json": "A",
    "platform/schemas/foundation-outputs/foundation-outputs.schema.json": "A",
    ".github/actions/foundation-outputs/action.yml": "A",
    ".github/workflows/foundation-access-check.yml": "A",
    "platform/infra/app-runtime/src/index.ts": "B",
    "platform/schemas/app-catalog/v0.1/app.schema.json": "B",
    "platform/catalog/affine/app.yaml": "B",
    "platform/environments/staging/affine.yaml": "B",
    "platform/operations/apps/affine/README.md": "B",
    ".github/workflows/app-deploy.yml": "B",
    "platform/schemas/events/event.schema.json": "C",
    "platform/operations/state/README.md": "C",
    ".github/ISSUE_TEMPLATE/support.yml": "C",
    ".github/workflows/health-check.yml": "C",
    ".github/workflows/ops-migrate.yml": "C",
    "platform/operations/agent/paket-betrieb.md": "D",
    "operations-agent/src/contracts/job.py": "C",
    "operations-agent/src/api/app.py": "C",
    "operations-agent/src/persistence/migrations/001.sql": "C",
    "operations-agent/src/worker/main.py": "C",
    "operations-agent/src/agent/runner.py": "D",
    "operations-agent/src/tools/kubernetes.py": "D",
    "operations-agent/deploy/Chart.yaml": "D",
    "operations-agent/Dockerfile": "D",
    "operations-agent/pyproject.toml": "D",
    ".github/workflows/agent-build.yml": "D",
    "operations-agent/tests/state/test_leases.py": "C",
    "operations-agent/tests/agent/test_tools.py": "D",
};

test("spec scenario: every area of the parallel plan has its strand as code owner", () => {
    assert.deepEqual(Object.values(strandOwners).sort(), ["A", "B", "C", "D"]);
    for (const [path, strand] of Object.entries(expected)) {
        assert.equal(strandOf(path), strand, path);
    }
});

test("every owner in a rule is a declared strand owner", () => {
    for (const { pattern, owners } of rules) {
        for (const owner of owners) assert.ok(owner in strandOwners, `${pattern}: ${owner}`);
    }
});
