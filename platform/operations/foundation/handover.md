# Foundation handover to B, C and D

How consumers get infrastructure values and access. The only source is the foundation
output contract; nobody derives Azure names from logs or the portal.

## Output contract

- Stack outputs of `organization/foundation/<env>`.
- Schema: [`platform/schemas/foundation-outputs/foundation-outputs.schema.json`](../../schemas/foundation-outputs/foundation-outputs.schema.json).
  Outputs marked `x-secret` are Pulumi secrets. Placeholder example:
  [`example.staging.json`](../../schemas/foundation-outputs/example.staging.json).
- `contractVersion` is an integer, currently `1`. Adding an output is compatible and
  keeps the version; renaming or removing one increases it. Consumers state the version
  they were written against and stop on a mismatch.
- Outputs beyond the spec table: `ingressClassName` (`traefik`), `clusterIssuerName`
  (`letsencrypt`) and `publicScheme` (`https`, or `http` while the
  [HTTP fallback](#https-and-the-http-fallback) is active).

| Consumer | Typical outputs |
| --- | --- |
| B, app stacks and release workflows | `kubeconfig`, `acrLoginServer`, `acrUsername`, `acrPassword`, `baseDomain`, `ingressClassName`, `clusterIssuerName`, `publicScheme`, `appInsightsConnectionString`, `postgresHost`, `postgresAdminUser`, `postgresAdminPassword` (app databases) |
| C, event intake, maintenance intake and persistence | `postgresHost`, `postgresAdminUser`, `postgresAdminPassword`, `postgresOperationsDatabase`, `alertWebhookKey`, `maintenanceApiKey`, `githubWebhookSecret`, `operationsApiHost` |
| D, agent runtime and tools | `kubeconfig` (release only; the agent itself uses its ServiceAccount), `modelApiKeys`, `opsGithubToken`, `diagnosticsStorageAccount`, `diagnosticsContainer`, `diagnosticsStorageKey`, `acrLoginServer`, `acrUsername`, `acrPassword` |

`modelApiKeys` is a JSON object keyed by provider: `anthropic` and/or `azure-openai`.
At least one key is present.

## Three ways to read it

### 1. Pulumi app stacks: StackReference

App stacks use the same backend and passphrase (`PULUMI_BACKEND_URL=azblob://pulumi-state`,
`AZURE_STORAGE_ACCOUNT`, `AZURE_STORAGE_KEY`, `PULUMI_CONFIG_PASSPHRASE`), so secret
outputs are decrypted without extra key configuration and stay secret in the app stack.

```typescript
const expectedContractVersion = 1;
const foundation = new pulumi.StackReference(`organization/foundation/${env}`);
foundation.requireOutput("contractVersion").apply((version) => {
    if (version !== expectedContractVersion) {
        throw new Error(`Foundation contract: expected ${expectedContractVersion}, stack provides ${version}`);
    }
});
const provider = new k8s.Provider("aks", { kubeconfig: foundation.requireOutput("kubeconfig") });
const host = foundation.requireOutput("baseDomain").apply((domain) => appNames(appId, env, domain).host);
```

Names inside the app stack come from `appNames(appId, env, baseDomain)` in
[`platform/infra/components/conventions.ts`](../../infra/components/conventions.ts).
App stacks need no Azure login: everything they touch is in Kubernetes or reached with
keys from the contract.

### 2. GitHub workflows: composite action

```yaml
permissions:
  contents: read
jobs:
  example:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - id: foundation
        uses: ./.github/actions/foundation-outputs
        with:
          backend-account: ${{ secrets.PULUMI_BACKEND_ACCOUNT }}
          backend-key: ${{ secrets.PULUMI_BACKEND_KEY }}
          config-passphrase: ${{ secrets.PULUMI_CONFIG_PASSPHRASE }}
          # expected-contract-version: "1" (default)
          # environment: staging (default)
          # source: placeholder  -> reads example.staging.json, no secrets needed
      - run: echo "Registry ${{ steps.foundation.outputs.acrLoginServer }}"
```

The action masks every secret output first (whole value, every line, and every longer
YAML value inside the kubeconfig), then checks the version and stops before any change
with `expected X, stack provides Y`. Every schema property is a step output; objects such
as `modelApiKeys` are JSON strings. The workflow needs no Azure identity.

### 3. Processes in the cluster: Kubernetes Secret and ConfigMap

A process never reads the Pulumi state. Its app stack creates a ConfigMap (non-secret
values) and a Secret (secret values) in the app namespace from the StackReference and
mounts them as environment variables. Keys and names are chosen by the owner of the app
stack (B) together with the app owner.

For the operations agent (D's chart, B's app stack):

| Need | Values |
| --- | --- |
| Kubernetes rights | ServiceAccount and RBAC shipped with D's chart; no Azure identity |
| Database | `postgresHost`, `postgresAdminUser`, `postgresAdminPassword`, `postgresOperationsDatabase`; TLS is enforced (`sslmode=require`) |
| Intake keys | `alertWebhookKey`, `maintenanceApiKey`, `githubWebhookSecret` |
| Model and GitHub | `modelApiKeys`, `opsGithubToken` |
| Diagnostics | `diagnosticsStorageAccount`, `diagnosticsContainer`, `diagnosticsStorageKey` |
| Public API | Ingress rule for `operationsApiHost` with class `ingressClassName` and annotation `cert-manager.io/cluster-issuer: <clusterIssuerName>` (B); `/healthz` (C) |

## HTTPS and the HTTP fallback

Traefik is the ingress controller (class `traefik`), cert-manager issues Let's Encrypt
certificates through HTTP-01 (ClusterIssuer `letsencrypt`). An app ingress requests a
certificate with the annotation `cert-manager.io/cluster-issuer: letsencrypt` and a
`tls` section for its host. HTTP is never redirected, so a host stays reachable over
HTTP while a certificate is pending.

`sslip.io` is not on the Public Suffix List, so all of `*.sslip.io` shares one Let's
Encrypt rate limit. If certificates are refused, the stack config `httpFallback: true`
switches `publicScheme` to `http`; the availability tests and the `ops-intake` webhook
then use HTTP. Consumers build URLs with `publicScheme`, never with a hard-coded scheme.

## Boundary: infrastructure and C's migrations

| A, foundation stack | C, operations persistence |
| --- | --- |
| PostgreSQL Flexible Server, database `operations`, admin login, TLS enforcement, open firewall, 7 days backup, deletion protection (Pulumi `protect`) | Tables, checkpoint structures, job and lock logic, migrations and their tooling |
| Network path: public endpoint reachable from GitHub-hosted runners and cluster pods | The client that runs migrations from a GitHub runner without a running agent API |

The foundation stack creates no tables and never runs migrations. Phase 1 uses one
admin login for migrations, runtime and app databases.

## GitHub access

There is no GitHub App. A fine-grained PAT (`opsGithubToken`, also repository secret
`OPS_GITHUB_TOKEN`) covers both repositories (`hackathon-platform-team`,
`hackathon-platform-affine`) with the repository permissions Issues, Contents, Pull
requests and Actions, each read and write. Comments the
agent writes carry the marker `<!-- mmk-ops-agent -->`; C ignores events that carry it.
The GitHub webhook to `https://<operationsApiHost>/` with `githubWebhookSecret` is
created by a team member once C's receiver runs.

## Consumer checks (completion of task 1.3)

- B: a release workflow calls the composite action and fails on a deliberate version
  mismatch.
- C: the migration client connects to `operations` from a GitHub-hosted runner with the
  contract values, without a running agent API.
- D: the agent package is built and pushed with `acrLoginServer`, `acrUsername` and
  `acrPassword` from the composite action (`.github/workflows/agent-build.yml`).
