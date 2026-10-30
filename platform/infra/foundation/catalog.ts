// Reads the fields of platform/catalog/<app-id>/app.yaml that the foundation needs for
// availability tests. Strand B owns the catalog schema and its full validation; field
// names follow B's draft (appId, availability.path) until that schema is published.
import * as fs from "node:fs";
import * as path from "node:path";
import { parse } from "yaml";
import { appIdProblems } from "../components/conventions";

export interface CatalogEntry {
    appId: string;
    availabilityPath: string;
    file: string;
}

export class CatalogError extends Error {}

function entryProblems(dirName: string, document: unknown): string[] {
    if (document === null || typeof document !== "object" || Array.isArray(document)) {
        return ["is not a YAML mapping"];
    }
    const { appId, availability } = document as { appId?: unknown; availability?: { path?: unknown } };
    const problems: string[] = [];
    if (typeof appId !== "string") {
        problems.push("appId is missing");
    } else {
        problems.push(...appIdProblems(appId));
        if (appId !== dirName) {
            problems.push(`appId "${appId}" differs from its directory "${dirName}"`);
        }
    }
    const checkPath = availability?.path;
    if (typeof checkPath !== "string" || !/^\/[^\s#]*$/.test(checkPath)) {
        problems.push("availability.path must be an absolute URL path such as /healthz");
    }
    return problems;
}

/**
 * Returns all catalog entries sorted by app ID. A missing catalog directory yields no
 * entries; directories without app.yaml are skipped. Any invalid entry fails the run, so
 * an app never silently loses its monitoring.
 */
export function readCatalog(catalogDir: string): CatalogEntry[] {
    if (!fs.existsSync(catalogDir)) {
        return [];
    }
    const entries: CatalogEntry[] = [];
    const errors: string[] = [];
    for (const dirent of fs.readdirSync(catalogDir, { withFileTypes: true })) {
        const file = path.join(catalogDir, dirent.name, "app.yaml");
        if (!dirent.isDirectory() || !fs.existsSync(file)) {
            continue;
        }
        let document: unknown;
        try {
            document = parse(fs.readFileSync(file, "utf8"));
        } catch (error) {
            errors.push(`${file}: ${(error as Error).message}`);
            continue;
        }
        const problems = entryProblems(dirent.name, document);
        if (problems.length > 0) {
            errors.push(`${file}: ${problems.join("; ")}`);
            continue;
        }
        const { appId, availability } = document as { appId: string; availability: { path: string } };
        entries.push({ appId, availabilityPath: availability.path, file });
    }
    if (errors.length > 0) {
        throw new CatalogError(`Invalid catalog entries:\n${errors.join("\n")}`);
    }
    return entries.sort((a, b) => a.appId.localeCompare(b.appId));
}
