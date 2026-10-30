// Naming, tagging and release conventions shared by the foundation and app stacks.
// The machine-readable source is schemas/conventions/conventions.json; this module
// only renders and checks against it.
import conventions from "../../schemas/conventions/conventions.json";

export type Environment = "staging" | "production";

export interface ReleaseReference {
    gitSha: string;
    imageDigest: string;
    chartVersion: string;
}

export interface AppNames {
    stack: string;
    namespace: string;
    host: string;
}

export interface FoundationNames {
    stack: string;
    resourceGroup: string;
}

export class ConventionError extends Error {}

const appIdPattern = new RegExp(conventions.appId.pattern);
const appIdEndPattern = new RegExp(conventions.appId.endPattern);
const checkPattern = new RegExp(conventions.checkPattern);
const ipv4Pattern = /^(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(\.(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}$/;

export const conventionsVersion: number = conventions.conventionsVersion;
export const platformAppId = "mmk-platform";
export const operationsAgentAppId: string = conventions.operationsAgentAppId;
export const managedBy: string = conventions.managedBy;
export const platformNamespaces: Readonly<Record<string, string>> = conventions.platformNamespaces;
export const stateBackend: Readonly<{ resourceGroup: string; container: string }> = conventions.stateBackend;

function render(template: string, values: Record<string, string>): string {
    return template.replace(/\{(\w+)\}/g, (_, key: string) => {
        const value = values[key];
        if (value === undefined) {
            throw new ConventionError(`Template "${template}" needs a value for "${key}".`);
        }
        return value;
    });
}

/** Returns a list of problems; an empty list means the app ID is valid. */
export function appIdProblems(appId: string): string[] {
    const problems: string[] = [];
    if (!appIdPattern.test(appId)) {
        problems.push(`App ID "${appId}" does not match ${conventions.appId.pattern}.`);
    }
    if (!appIdEndPattern.test(appId)) {
        problems.push(`App ID "${appId}" must end with a letter or digit; it is the first label of its host name.`);
    }
    if (conventions.appId.reserved.includes(appId)) {
        problems.push(`App ID "${appId}" is reserved for the platform.`);
    }
    return problems;
}

export function assertAppId(appId: string): void {
    const problems = appIdProblems(appId);
    if (problems.length > 0) {
        throw new ConventionError(problems.join(" "));
    }
}

export function assertEnvironment(env: string): asserts env is Environment {
    if (!conventions.environments.includes(env)) {
        throw new ConventionError(
            `Environment "${env}" is not one of ${conventions.environments.join(", ")}.`,
        );
    }
}

export function appNames(appId: string, env: string, baseDomain: string): AppNames {
    assertAppId(appId);
    assertEnvironment(env);
    const values = { appId, env, baseDomain };
    return {
        stack: render(conventions.names.appStack, values),
        namespace: render(conventions.names.appNamespace, values),
        host: render(conventions.names.appHost, values),
    };
}

function assertCheck(check: string): void {
    if (!checkPattern.test(check)) {
        throw new ConventionError(`Check name "${check}" does not match ${conventions.checkPattern}.`);
    }
}

/** Alert rule name alert-<app-id>-<env>-<check>; the event intake reads app and environment from it. */
export function alertRuleName(appId: string, env: string, check: string): string {
    assertAppId(appId);
    assertEnvironment(env);
    assertCheck(check);
    return render(conventions.names.alertRule, { appId, env, check });
}

export function availabilityTestName(appId: string, env: string, check: string): string {
    assertAppId(appId);
    assertEnvironment(env);
    assertCheck(check);
    return render(conventions.names.availabilityTest, { appId, env, check });
}

/** <ingress-ip-with-dashes>.sslip.io, e.g. 20.79.1.2 -> 20-79-1-2.sslip.io. */
export function baseDomainFor(ingressIp: string): string {
    if (!ipv4Pattern.test(ingressIp)) {
        throw new ConventionError(`Ingress IP "${ingressIp}" is not an IPv4 address.`);
    }
    return render(conventions.names.baseDomain, { ingressIpWithDashes: ingressIp.replaceAll(".", "-") });
}

export function foundationNames(env: string): FoundationNames {
    assertEnvironment(env);
    return {
        stack: render(conventions.names.foundationStack, { env }),
        resourceGroup: render(conventions.names.foundationResourceGroup, { env }),
    };
}

/** The four tags every Azure resource of the platform must carry. */
export function requiredTags(appId: string, env: string, stack: string): Record<string, string> {
    assertEnvironment(env);
    return {
        "app-id": appId,
        environment: env,
        stack,
        "managed-by": managedBy,
    };
}

export function releaseReferenceProblems(ref: Partial<ReleaseReference>): string[] {
    const problems: string[] = [];
    for (const [field, pattern] of Object.entries(conventions.releaseReference)) {
        const value = ref[field as keyof ReleaseReference];
        if (typeof value !== "string" || !new RegExp(pattern).test(value)) {
            problems.push(`Release reference field "${field}" must match ${pattern}.`);
        }
    }
    return problems;
}
