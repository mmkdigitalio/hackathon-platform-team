// Convention check for workflows and catalog validation. Exits non-zero before any
// change is made when an identifier violates the conventions.
//
//   npm run conventions -- app-id <appId>
//   npm run conventions -- names <appId> <env> [baseDomain]
//   npm run conventions -- release <gitSha> <imageDigest> <chartVersion>
import {
    alertRuleName,
    appIdProblems,
    appNames,
    ConventionError,
    releaseReferenceProblems,
} from "../../infra/components/conventions";

function main(args: string[]): number {
    const [command, ...rest] = args;
    switch (command) {
        case "app-id": {
            const problems = appIdProblems(rest[0] ?? "");
            problems.forEach((p) => console.error(p));
            return problems.length === 0 ? 0 : 1;
        }
        case "names": {
            const [appId, env, baseDomain] = rest;
            const names = appNames(appId ?? "", env ?? "", baseDomain ?? "<baseDomain>");
            const availabilityAlert = alertRuleName(appId ?? "", env ?? "", "availability");
            console.log(JSON.stringify({ ...names, availabilityAlert }, null, 2));
            return 0;
        }
        case "release": {
            const [gitSha, imageDigest, chartVersion] = rest;
            const problems = releaseReferenceProblems({ gitSha, imageDigest, chartVersion });
            problems.forEach((p) => console.error(p));
            return problems.length === 0 ? 0 : 1;
        }
        default:
            console.error("Usage: check <app-id|names|release> ...");
            return 2;
    }
}

try {
    process.exitCode = main(process.argv.slice(2));
} catch (error) {
    if (!(error instanceof ConventionError)) {
        throw error;
    }
    console.error(error.message);
    process.exitCode = 1;
}
