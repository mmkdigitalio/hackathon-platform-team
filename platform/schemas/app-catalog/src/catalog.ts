import * as fs from "node:fs";
import * as path from "node:path";
import Ajv2020, { type ErrorObject, type ValidateFunction } from "ajv/dist/2020";
import addFormats from "ajv-formats";
import { parse as parseYaml } from "yaml";

export const SUPPORTED_SCHEMA_VERSION = "0.1";

/** Foundation-Ausgaben, die nie in ein App-Secret gelangen dürfen. */
const FORBIDDEN_FOUNDATION_REFS = new Set(["kubeconfig"]);

const PLACEHOLDER_DIGEST = /@sha256:0{64}$/;

/** Chart-Werte, die das App-Programm setzt (zusätzlich image.repository und image.digest). */
const RESERVED_VALUES = ["secretName", "imagePullSecretName", "rolloutMarker", "platform"];

export interface Issue {
  file: string;
  path: string;
  message: string;
}

export interface ValidationResult {
  errors: Issue[];
  warnings: Issue[];
}

/** Verträge aus Strang A, gegen die der Katalog geprüft wird. */
export interface Contracts {
  appIdPattern: RegExp;
  environments: string[];
  foundationOutputs: Set<string>;
}

export interface CatalogEntry {
  schemaVersion: string;
  appId: string;
  description?: string;
  source: { repository: string; upstream?: string };
  chart: { acr?: { repository: string }; oci?: { url: string }; repository?: { url: string; name: string } };
  ingress: { service: string; port: number };
  dataBlocks: DataBlockName[];
  generatedSecrets?: string[];
  secrets: Record<string, string>;
  availability: { path: string };
  smokeTest: { image: string; env?: Record<string, string>; secrets?: Record<string, string> };
  runbook: { repository: string; path: string; commit: string };
  allowedActions: string[];
  dataProcedures: { migration: string; backup: string; restore: string };
}

export interface DesiredState {
  schemaVersion: string;
  upstreamRelease?: string;
  image: string;
  chart: { version: string };
  sizes: {
    replicas: number;
    resources?: Record<string, Record<string, string>>;
    volumeSize?: string;
  };
  values?: Record<string, unknown>;
}

export type DataBlockName = "postgres-database" | "redis" | "persistent-volume";

export interface DataBlockSpec {
  secrets: string[];
  requiresVolumeSize?: boolean;
}

/** Sucht von `start` aufwärts das Verzeichnis, das `platform/schemas/app-catalog` enthält. */
export function findRepoRoot(start: string = __dirname): string {
  let dir = path.resolve(start);
  for (;;) {
    if (fs.existsSync(path.join(dir, "platform", "schemas", "app-catalog", "v0.1"))) return dir;
    const parent = path.dirname(dir);
    if (parent === dir) throw new Error(`Repository-Wurzel ab ${start} nicht gefunden`);
    dir = parent;
  }
}

export function schemaDir(repoRoot: string = findRepoRoot()): string {
  return path.join(repoRoot, "platform", "schemas", "app-catalog", `v${SUPPORTED_SCHEMA_VERSION}`);
}

function readJson<T>(file: string): T {
  return JSON.parse(fs.readFileSync(file, "utf8")) as T;
}

export function loadDataBlocks(repoRoot: string = findRepoRoot()): Record<DataBlockName, DataBlockSpec> {
  const raw = readJson<Record<string, unknown>>(path.join(schemaDir(repoRoot), "data-blocks.json"));
  delete raw.$comment;
  return raw as Record<DataBlockName, DataBlockSpec>;
}

export interface ContractPaths {
  conventions?: string;
  foundationOutputs?: string;
}

/**
 * Lädt die Verträge aus A's Bereich. Erwartete Form:
 * - conventions: JSON mit `appId.pattern` und `environments` (Liste)
 * - foundationOutputs: JSON-Schema mit einer Eigenschaft je Ausgabe unter `properties`
 * Fehlen die Dateien, ist das ein Lieferpunkt von A und die Validierung bricht ab.
 */
export function loadContracts(repoRoot: string = findRepoRoot(), paths: ContractPaths = {}): Contracts {
  const conventionsFile =
    paths.conventions ??
    process.env.APP_CATALOG_CONVENTIONS ??
    path.join(repoRoot, "platform", "schemas", "conventions", "conventions.json");
  const outputsFile =
    paths.foundationOutputs ??
    process.env.APP_CATALOG_FOUNDATION_OUTPUTS ??
    path.join(repoRoot, "platform", "schemas", "foundation-outputs", "foundation-outputs.schema.json");

  for (const [name, file] of [
    ["Konventionen", conventionsFile],
    ["Foundation-Ausgabeschema", outputsFile],
  ]) {
    if (!fs.existsSync(file)) {
      throw new Error(`${name} fehlt: ${file} (Lieferung von Strang A, siehe platform/schemas/app-catalog/README.md)`);
    }
  }

  const conventions = readJson<{ appId?: { pattern?: string }; environments?: string[] }>(conventionsFile);
  if (!conventions.appId?.pattern || !Array.isArray(conventions.environments)) {
    throw new Error(`${conventionsFile}: erwartet appId.pattern und environments`);
  }
  const outputs = readJson<{ properties?: Record<string, unknown> }>(outputsFile);
  if (!outputs.properties) throw new Error(`${outputsFile}: erwartet ein JSON-Schema mit properties`);

  return {
    appIdPattern: new RegExp(conventions.appId.pattern),
    environments: conventions.environments,
    foundationOutputs: new Set(Object.keys(outputs.properties)),
  };
}

interface Validators {
  app: ValidateFunction;
  desired: ValidateFunction;
}

const validatorCache = new Map<string, Validators>();

function validators(repoRoot: string): Validators {
  const cached = validatorCache.get(repoRoot);
  if (cached) return cached;
  const ajv = new Ajv2020({ allErrors: true, strict: true, verbose: true });
  addFormats(ajv);
  ajv.addKeyword({ keyword: "x-error", schemaType: "string" });
  const dir = schemaDir(repoRoot);
  const appSchema = readJson<object>(path.join(dir, "app.schema.json"));
  ajv.addSchema(appSchema);
  const result = {
    app: ajv.getSchema((appSchema as { $id: string }).$id)!,
    desired: ajv.compile(readJson<object>(path.join(dir, "desired-state.schema.json"))),
  };
  validatorCache.set(repoRoot, result);
  return result;
}

function formatAjvError(file: string, error: ErrorObject): Issue {
  const custom = (error.parentSchema as { "x-error"?: string } | undefined)?.["x-error"];
  const at = error.instancePath || "/";
  if (error.keyword === "additionalProperties") {
    const field = (error.params as { additionalProperty: string }).additionalProperty;
    return { file, path: `${error.instancePath}/${field}`, message: `unbekanntes Feld '${field}'` };
  }
  if (error.keyword === "required") {
    const field = (error.params as { missingProperty: string }).missingProperty;
    return { file, path: `${error.instancePath}/${field}`, message: `Pflichtfeld '${field}' fehlt` };
  }
  if (custom) {
    // Fehlerhafte Secret-Referenzen können Klartextwerte sein und werden nie ausgegeben
    const value = /\/secrets\//.test(at) ? "" : ` (Wert: ${JSON.stringify(error.data)})`;
    return { file, path: at, message: `${custom}${value}` };
  }
  return { file, path: at, message: error.message ?? error.keyword };
}

/** Ajv meldet bei Fehlern in Unterschemata oft Folgefehler; pro Pfad und Meldung nur einmal ausgeben. */
function dedupe(issues: Issue[]): Issue[] {
  const seen = new Set<string>();
  return issues.filter((i) => {
    const key = `${i.file}|${i.path}|${i.message}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function readYaml(file: string, errors: Issue[]): unknown {
  try {
    return parseYaml(fs.readFileSync(file, "utf8"));
  } catch (e) {
    errors.push({ file, path: "/", message: `kein gültiges YAML: ${(e as Error).message}` });
    return undefined;
  }
}

function checkSchemaVersion(file: string, doc: unknown, errors: Issue[]): boolean {
  const version = (doc as { schemaVersion?: unknown } | null)?.schemaVersion;
  if (version !== SUPPORTED_SCHEMA_VERSION) {
    errors.push({
      file,
      path: "/schemaVersion",
      message: `schemaVersion ${JSON.stringify(version)} wird nicht unterstützt; unterstützte Version: ${SUPPORTED_SCHEMA_VERSION}`,
    });
    return false;
  }
  return true;
}

export function validateCatalogEntry(
  file: string,
  doc: unknown,
  expectedAppId: string,
  contracts: Contracts,
  repoRoot: string,
): ValidationResult {
  const errors: Issue[] = [];
  const warnings: Issue[] = [];
  if (!checkSchemaVersion(file, doc, errors)) return { errors, warnings };

  const v = validators(repoRoot).app;
  if (!v(doc)) errors.push(...(v.errors ?? []).map((e) => formatAjvError(file, e)));
  const entry = doc as CatalogEntry;

  if (!contracts.appIdPattern.test(expectedAppId)) {
    errors.push({ file, path: "/", message: `App-ID '${expectedAppId}' verletzt die Konvention ${contracts.appIdPattern}` });
  }
  if (typeof entry.appId === "string" && entry.appId !== expectedAppId) {
    errors.push({ file, path: "/appId", message: `appId '${entry.appId}' passt nicht zum Verzeichnis '${expectedAppId}'` });
  }

  const blocks = loadDataBlocks(repoRoot);
  const provided = new Map<string, string>();
  for (const block of entry.dataBlocks ?? []) {
    for (const s of blocks[block]?.secrets ?? []) provided.set(s, `Datenbaustein ${block}`);
  }
  for (const [i, name] of (entry.generatedSecrets ?? []).entries()) {
    const owner = provided.get(name);
    if (owner) errors.push({ file, path: `/generatedSecrets/${i}`, message: `'${name}' kollidiert mit ${owner}` });
    else provided.set(name, "generatedSecrets");
  }

  const checkRefs = (map: Record<string, string> | undefined, base: string) => {
    for (const [key, ref] of Object.entries(map ?? {})) {
      if (typeof ref !== "string") continue;
      const m = /^(foundation|app):(.+)$/.exec(ref);
      if (!m) continue; // Formfehler meldet das Schema
      const [, kind, name] = m;
      const at = `${base}/${key}`;
      if (kind === "foundation") {
        if (FORBIDDEN_FOUNDATION_REFS.has(name)) {
          errors.push({ file, path: at, message: `'${ref}' darf nicht an Apps weitergegeben werden` });
        } else if (!contracts.foundationOutputs.has(name)) {
          errors.push({ file, path: at, message: `'${ref}' ist keine Ausgabe des Foundation-Ausgabeschemas` });
        }
      } else if (!provided.has(name)) {
        errors.push({
          file,
          path: at,
          message: `'${ref}' wird weder von einem deklarierten Datenbaustein geliefert noch in generatedSecrets generiert`,
        });
      }
    }
  };
  checkRefs(entry.secrets, "/secrets");
  checkRefs(entry.smokeTest?.secrets, "/smokeTest/secrets");
  // App- und Test-Secrets landen im selben Secret <app-id>-platform
  for (const [key, ref] of Object.entries(entry.smokeTest?.secrets ?? {})) {
    const appRef = entry.secrets?.[key];
    if (appRef !== undefined && appRef !== ref) {
      errors.push({ file, path: `/smokeTest/secrets/${key}`, message: `Schlüssel ${key} ist in secrets bereits mit anderer Referenz belegt` });
    }
  }

  if (typeof entry.smokeTest?.image === "string" && PLACEHOLDER_DIGEST.test(entry.smokeTest.image)) {
    warnings.push({ file, path: "/smokeTest/image", message: "Platzhalter-Digest; vor dem Deployment ersetzen" });
  }
  return { errors: dedupe(errors), warnings };
}

export function validateDesiredState(
  file: string,
  doc: unknown,
  entry: CatalogEntry | undefined,
  repoRoot: string,
): ValidationResult {
  const errors: Issue[] = [];
  const warnings: Issue[] = [];
  if (!checkSchemaVersion(file, doc, errors)) return { errors, warnings };
  const v = validators(repoRoot).desired;
  if (!v(doc)) errors.push(...(v.errors ?? []).map((e) => formatAjvError(file, e)));
  const desired = doc as DesiredState;

  if (entry && Array.isArray(entry.dataBlocks)) {
    const blocks = loadDataBlocks(repoRoot);
    const needsVolume = entry.dataBlocks.some((b) => blocks[b]?.requiresVolumeSize);
    const hasVolume = desired.sizes?.volumeSize !== undefined;
    if (needsVolume && !hasVolume) {
      errors.push({ file, path: "/sizes/volumeSize", message: "Pflicht, weil der Katalogeintrag persistent-volume deklariert" });
    }
    if (!needsVolume && hasVolume) {
      errors.push({ file, path: "/sizes/volumeSize", message: "nicht zulässig ohne Datenbaustein persistent-volume" });
    }
  }
  if (desired.values && typeof desired.values === "object") {
    const values = desired.values as Record<string, unknown>;
    const image = (values.image ?? {}) as Record<string, unknown>;
    const reserved = [
      ...RESERVED_VALUES.filter((k) => k in values),
      ...["repository", "digest"].filter((k) => typeof image === "object" && k in image).map((k) => `image/${k}`),
    ];
    for (const key of reserved) {
      errors.push({ file, path: `/values/${key}`, message: "reserviert; diesen Wert setzt das App-Programm" });
    }
  }
  if (typeof desired.image === "string" && PLACEHOLDER_DIGEST.test(desired.image)) {
    warnings.push({ file, path: "/image", message: "Platzhalter-Digest; app-deploy.yml rollt diesen Sollzustand nicht aus" });
  }
  return { errors: dedupe(errors), warnings };
}

export interface TreeLayout {
  /** Verzeichnis mit `<app-id>/app.yaml` */
  catalogDir: string;
  /** Verzeichnis mit `<env>/<app-id>.yaml` */
  environmentsDir: string;
}

export function repositoryLayout(repoRoot: string): TreeLayout {
  return {
    catalogDir: path.join(repoRoot, "platform", "catalog"),
    environmentsDir: path.join(repoRoot, "platform", "environments"),
  };
}

function listDirs(dir: string): string[] {
  if (!fs.existsSync(dir)) return [];
  return fs
    .readdirSync(dir, { withFileTypes: true })
    .filter((d) => d.isDirectory())
    .map((d) => d.name)
    .sort();
}

/** Validiert alle Katalogeinträge und Sollzustände eines Baums. */
export function validateTree(layout: TreeLayout, contracts: Contracts, repoRoot: string): ValidationResult {
  const result: ValidationResult = { errors: [], warnings: [] };
  const merge = (r: ValidationResult) => {
    result.errors.push(...r.errors);
    result.warnings.push(...r.warnings);
  };
  const rel = (f: string) => path.relative(repoRoot, f);

  const entries = new Map<string, CatalogEntry | undefined>();
  for (const appId of listDirs(layout.catalogDir)) {
    const file = path.join(layout.catalogDir, appId, "app.yaml");
    if (!fs.existsSync(file)) {
      result.errors.push({ file: rel(path.dirname(file)), path: "/", message: "Verzeichnis ohne app.yaml" });
      continue;
    }
    const doc = readYaml(file, result.errors);
    if (doc === undefined) continue;
    const r = validateCatalogEntry(rel(file), doc, appId, contracts, repoRoot);
    merge(r);
    entries.set(appId, r.errors.length === 0 ? (doc as CatalogEntry) : undefined);
  }

  for (const env of listDirs(layout.environmentsDir)) {
    const envDir = path.join(layout.environmentsDir, env);
    if (!contracts.environments.includes(env)) {
      result.errors.push({
        file: rel(envDir),
        path: "/",
        message: `Umgebung '${env}' ist laut Konvention nicht zulässig (${contracts.environments.join(", ")})`,
      });
      continue;
    }
    for (const name of fs.readdirSync(envDir).sort()) {
      const file = path.join(envDir, name);
      if (!/\.ya?ml$/.test(name)) continue;
      const appId = name.replace(/\.ya?ml$/, "");
      if (!entries.has(appId)) {
        result.errors.push({
          file: rel(file),
          path: "/",
          message: `Sollzustand ohne Katalogeintrag ${rel(path.join(layout.catalogDir, appId, "app.yaml"))}`,
        });
        continue;
      }
      const doc = readYaml(file, result.errors);
      if (doc === undefined) continue;
      merge(validateDesiredState(rel(file), doc, entries.get(appId), repoRoot));
    }
  }
  return result;
}

export interface LoadedApp {
  appId: string;
  environment: string;
  catalog: CatalogEntry;
  desired: DesiredState;
}

/** Lädt und validiert genau eine App in einer Umgebung; wirft bei Fehlern. */
export function loadApp(
  appId: string,
  environment: string,
  contracts: Contracts,
  repoRoot: string = findRepoRoot(),
  layout: TreeLayout = repositoryLayout(repoRoot),
): LoadedApp {
  const errors: Issue[] = [];
  if (!contracts.environments.includes(environment)) {
    throw new Error(`Umgebung '${environment}' ist laut Konvention nicht zulässig`);
  }
  const catalogFile = path.join(layout.catalogDir, appId, "app.yaml");
  const desiredFile = path.join(layout.environmentsDir, environment, `${appId}.yaml`);
  for (const f of [catalogFile, desiredFile]) {
    if (!fs.existsSync(f)) throw new Error(`${path.relative(repoRoot, f)} fehlt`);
  }
  const catalog = readYaml(catalogFile, errors);
  const desired = readYaml(desiredFile, errors);
  if (errors.length === 0) {
    const c = validateCatalogEntry(path.relative(repoRoot, catalogFile), catalog, appId, contracts, repoRoot);
    errors.push(...c.errors);
    if (c.errors.length === 0) {
      errors.push(
        ...validateDesiredState(path.relative(repoRoot, desiredFile), desired, catalog as CatalogEntry, repoRoot).errors,
      );
    }
  }
  if (errors.length > 0) throw new Error(formatIssues(errors));
  return { appId, environment, catalog: catalog as CatalogEntry, desired: desired as DesiredState };
}

export function formatIssues(issues: Issue[]): string {
  return issues.map((i) => `${i.file}: ${i.path}: ${i.message}`).join("\n");
}

/** Konventionsnamen aus platform-bootstrap. */
export function derivedNames(appId: string, environment: string) {
  return {
    stack: `organization/app-${appId}/${environment}`,
    namespace: `${appId}-${environment}`,
    hostPrefix: appId,
    platformSecret: `${appId}-platform`,
    imagePullSecret: "acr-pull",
    alertRule: `alert-${appId}-${environment}-availability`,
  };
}

/** Löst `acr:<repo>@sha256:...` gegen den ACR-Login-Server auf; andere Referenzen bleiben unverändert. */
export function resolveImage(ref: string, acrLoginServer: string): string {
  return ref.startsWith("acr:") ? `${acrLoginServer}/${ref.slice(4)}` : ref;
}

export function imageDigest(ref: string): string {
  const i = ref.indexOf("@");
  return i < 0 ? "" : ref.slice(i + 1);
}
