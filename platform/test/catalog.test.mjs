import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";

const require = createRequire(import.meta.url);
const { readCatalog, CatalogError } = require("../dist/infra/foundation/catalog.js");

function catalog(entries) {
    const dir = mkdtempSync(join(tmpdir(), "catalog-"));
    for (const [name, content] of Object.entries(entries)) {
        mkdirSync(join(dir, name));
        if (content !== null) writeFileSync(join(dir, name, "app.yaml"), content);
    }
    return dir;
}

test("reads app ID and availability path, sorted, skipping directories without app.yaml", () => {
    const dir = catalog({
        "operations-agent": "appId: operations-agent\navailability:\n  path: /healthz\n",
        affine: "appId: affine\navailability: { path: /info }\nchart: { oci: x }\n",
        notes: null,
    });
    assert.deepEqual(
        readCatalog(dir).map(({ appId, availabilityPath }) => ({ appId, availabilityPath })),
        [
            { appId: "affine", availabilityPath: "/info" },
            { appId: "operations-agent", availabilityPath: "/healthz" },
        ],
    );
});

test("a missing catalog directory yields no entries", () => {
    assert.deepEqual(readCatalog(join(tmpdir(), "does-not-exist-catalog")), []);
});

for (const [label, content, message] of [
    ["invalid app ID", "appId: Demo_App\navailability: { path: /info }\n", /does not match/],
    ["app ID differs from directory", "appId: other\navailability: { path: /info }\n", /differs from its directory/],
    ["missing availability path", "appId: demo\n", /availability\.path/],
    ["relative availability path", "appId: demo\navailability: { path: info }\n", /availability\.path/],
    ["broken YAML", "appId: [demo\n", /app\.yaml/],
]) {
    test(`spec: an invalid entry fails the run (${label})`, () => {
        const dir = catalog({ demo: content });
        assert.throws(() => readCatalog(dir), (error) => error instanceof CatalogError && message.test(error.message));
    });
}
