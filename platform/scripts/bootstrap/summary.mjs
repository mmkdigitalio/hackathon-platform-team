// Prints the foundation run summary: stack, Git SHA, versions, URLs and every output.
// Reads outputs without --show-secrets, so secrets stay "[secret]". Appends to
// $GITHUB_STEP_SUMMARY when set, otherwise writes to stdout.
//
//   GIT_SHA=$(git rev-parse HEAD) node platform/scripts/bootstrap/summary.mjs organization/foundation/staging
import { execFileSync } from "node:child_process";
import { appendFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const cell = (value) => (typeof value === "string" ? value : JSON.stringify(value)).replaceAll("|", "\\|");

export function renderSummary({ stack, gitSha, outputs }) {
    const scheme = outputs.publicScheme ?? "https";
    const versions = Object.entries(outputs.platformVersions ?? {}).map(([name, version]) => `| ${name} | \`${cell(version)}\` |`);
    const rows = Object.entries(outputs).map(([name, value]) => `| \`${name}\` | ${cell(value)} |`);
    return [
        `## Foundation ${stack}`,
        "",
        "| Version | Value |",
        "| --- | --- |",
        `| Git SHA | \`${gitSha}\` |`,
        `| contractVersion | \`${outputs.contractVersion}\` |`,
        ...versions,
        "",
        "URLs:",
        `- Apps: \`${scheme}://<app-id>.${outputs.baseDomain}\``,
        `- Operations API health: ${scheme}://${outputs.operationsApiHost}/healthz`,
        `- Resource group: \`${outputs.resourceGroupName}\` (${outputs.location})`,
        "",
        "| Output | Value |",
        "| --- | --- |",
        ...rows,
        "",
        "Consumers read these values only through the output contract (platform/operations/foundation/handover.md):",
        `- Pulumi app stacks: \`new pulumi.StackReference("${stack}")\``,
        "- Workflows: `uses: ./.github/actions/foundation-outputs`",
        "",
        "Next: run the workflow foundation-access-check and check that every category passes.",
        "",
    ].join("\n");
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
    const stack = process.argv[2];
    if (!stack) {
        console.error("Usage: summary.mjs <stack>");
        process.exit(2);
    }
    const outputs = JSON.parse(execFileSync("pulumi", ["stack", "output", "--json", "--stack", stack], { encoding: "utf8" }));
    const markdown = renderSummary({ stack, gitSha: process.env.GIT_SHA ?? process.env.GITHUB_SHA ?? "unknown", outputs });
    if (process.env.GITHUB_STEP_SUMMARY) {
        appendFileSync(process.env.GITHUB_STEP_SUMMARY, markdown);
    } else {
        console.log(markdown);
    }
}
