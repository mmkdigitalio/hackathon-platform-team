// Reads the foundation stack outputs, checks the contract version, masks secret values
// and writes every output as a step output. Secret output names come from the x-secret
// markers in platform/schemas/foundation-outputs/foundation-outputs.schema.json and from
// the stack itself, so a secret output added after this action version was pinned is
// masked too.
import { execFileSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import { appendFileSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const schemaDir = join(here, "../../../platform/schemas/foundation-outputs");
export const defaultSchemaPath = join(schemaDir, "foundation-outputs.schema.json");
export const placeholderPath = join(schemaDir, "example.staging.json");

// Lines shorter than this inside a multi-line secret (YAML keys, "apiVersion: v1") are
// not worth masking and would black out unrelated log text.
const minEmbeddedValueLength = 16;

// Embedded YAML values are masked on their own only under keys that carry credentials,
// so that plain values such as the cluster name in a kubeconfig stay visible.
const credentialKey = /(-data|token|password|secret|key)$/i;

export class ContractError extends Error {}

export function secretOutputNames(schema) {
    return Object.entries(schema.properties ?? {})
        .filter(([, property]) => property["x-secret"] === true)
        .map(([name]) => name);
}

export function checkContract(outputs, expectedVersion) {
    const actual = outputs.contractVersion;
    if (actual !== expectedVersion) {
        throw new ContractError(
            `Foundation contract version mismatch: expected ${expectedVersion}, stack provides ${actual ?? "none"}. ` +
                "Update the consumer before using these outputs.",
        );
    }
}

function asText(value) {
    return typeof value === "string" ? value : JSON.stringify(value);
}

/** Every string worth masking in one secret value: the value, long lines, and credential YAML values (kubeconfig). */
function maskValues(text) {
    const values = new Set([text]);
    for (const line of text.split(/\r?\n/)) {
        const trimmed = line.trim();
        if (trimmed.length < minEmbeddedValueLength) continue;
        values.add(trimmed);
        const [, key, value] = trimmed.match(/^-?\s*([\w.-]+):\s+(.+)$/) ?? [];
        if (key && credentialKey.test(key) && value.trim().length >= minEmbeddedValueLength) values.add(value.trim());
    }
    return [...values].filter((value) => value.trim() !== "" && !value.includes("\n"));
}

/** Workflow-command escaping as in @actions/core, so the runner registers the exact value. */
const escapeCommandData = (value) => value.replaceAll("%", "%25").replaceAll("\r", "%0D").replaceAll("\n", "%0A");

/** Workflow commands that mask every secret value, line by line, including object members. */
export function maskCommands(outputs, secretNames) {
    const values = new Set();
    for (const name of secretNames) {
        if (!(name in outputs)) continue;
        const raw = outputs[name];
        const isObject = raw !== null && typeof raw === "object";
        if (isObject && Object.keys(raw).length === 0) continue;
        const texts = isObject ? [asText(raw), ...Object.values(raw).map(asText)] : [asText(raw)];
        for (const text of texts) maskValues(text).forEach((value) => values.add(value));
    }
    return [...values].map((value) => `::add-mask::${escapeCommandData(value)}`);
}

/** Step-output file content; multi-line values use the heredoc form. */
export function stepOutputLines(outputs, delimiter = `EOF_${randomUUID()}`) {
    const lines = [];
    for (const [name, raw] of Object.entries(outputs)) {
        const value = asText(raw);
        if (value.includes("\n")) {
            lines.push(`${name}<<${delimiter}`, value, delimiter);
        } else {
            lines.push(`${name}=${value}`);
        }
    }
    return lines;
}

function pulumiStackOutput(stack, extraArgs) {
    const json = execFileSync("pulumi", ["stack", "output", "--json", ...extraArgs, "--stack", stack], {
        encoding: "utf8",
        stdio: ["ignore", "pipe", "inherit"],
    });
    return JSON.parse(json);
}

const readStackOutputs = (stack) => pulumiStackOutput(stack, ["--show-secrets"]);

/** Names of outputs that Pulumi itself holds as secrets (shown as "[secret]" without --show-secrets). */
function readStackSecretNames(stack) {
    return Object.entries(pulumiStackOutput(stack, []))
        .filter(([, value]) => value === "[secret]")
        .map(([name]) => name);
}

/** Backend settings the stack source needs; checked up front for a clear message instead of a Pulumi login error. */
export function missingBackendSettings(env) {
    return [
        ["AZURE_STORAGE_ACCOUNT", "backend-account"],
        ["AZURE_STORAGE_KEY", "backend-key"],
        ["PULUMI_CONFIG_PASSPHRASE", "config-passphrase"],
    ]
        .filter(([variable]) => !env[variable])
        .map(([, input]) => input);
}

export function run({
    stack,
    expectedVersion,
    schemaPath = defaultSchemaPath,
    outputFile,
    readOutputs = readStackOutputs,
    readSecretNames = readStackSecretNames,
    write = console.log,
}) {
    if (!Number.isInteger(expectedVersion)) {
        throw new ContractError(`expected-contract-version must be an integer, got "${expectedVersion}".`);
    }
    const secretNames = new Set([
        ...secretOutputNames(JSON.parse(readFileSync(schemaPath, "utf8"))),
        ...readSecretNames(stack),
    ]);
    const outputs = readOutputs(stack);
    // Mask first, so that no later failure can print a secret unmasked.
    maskCommands(outputs, secretNames).forEach((command) => write(command));
    checkContract(outputs, expectedVersion);
    appendFileSync(outputFile, stepOutputLines(outputs).join("\n") + "\n");
    return Object.keys(outputs).length;
}

export function sourceReaders(source, env) {
    switch (source) {
        case "stack": {
            const missing = missingBackendSettings(env);
            if (missing.length > 0) {
                throw new ContractError(`Missing action inputs for source "stack": ${missing.join(", ")}.`);
            }
            return {};
        }
        case "placeholder":
            return {
                readOutputs: () => JSON.parse(readFileSync(placeholderPath, "utf8")),
                readSecretNames: () => [],
            };
        default:
            throw new ContractError(`source must be "stack" or "placeholder", got "${source}".`);
    }
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
    try {
        const source = process.env.OUTPUTS_SOURCE || "stack";
        const count = run({
            stack: process.env.FOUNDATION_STACK,
            expectedVersion: Number(process.env.EXPECTED_CONTRACT_VERSION),
            outputFile: process.env.GITHUB_OUTPUT,
            ...sourceReaders(source, process.env),
        });
        console.log(`Read ${count} foundation outputs (${source}) for ${process.env.FOUNDATION_STACK}.`);
    } catch (error) {
        console.log(`::error::${error.message}`);
        process.exitCode = 1;
    }
}
