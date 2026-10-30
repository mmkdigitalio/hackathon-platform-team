# Foundation implementation decisions

Choices made while implementing the OpenSpec change `platform-foundation` that the spec
leaves open. The code implements each proposal so it can be reviewed and changed before
the first Azure deployment.

**Status of every entry: Proposed (pending Erik's decision).** None of them is accepted.

Already decided and not repeated here: the hackathon decisions E1 to E10 in
[PARALLEL-IMPLEMENTATION.md](../../../openspec/PARALLEL-IMPLEMENTATION.md) (in particular
E3 Contributor only, E4 local foundation runs, E5 all Azure resources in the foundation
stack, E6 keys instead of roles, E8 sslip.io with HTTP fallback, E10 agent health through
`/healthz`) and the design decisions 1 to 7 in
[design.md](../../../openspec/changes/platform-foundation/design.md). Entries below refer
to them where they build on them.

| No. | Decision | Main code |
| --- | --- | --- |
| [D1](#d1-ingress-controller-traefik) | Traefik as ingress controller, no global HTTP-to-HTTPS redirect | [`ingress.ts`](../../infra/foundation/ingress.ts) |
| [D2](#d2-cert-manager-with-http-01-and-a-switchable-http-fallback) | cert-manager with HTTP-01; `httpFallback` switches `publicScheme` | [`ingress.ts`](../../infra/foundation/ingress.ts), [`config.ts`](../../infra/foundation/config.ts) |
| [D3](#d3-basedomain-from-the-traefik-service-ip) | `baseDomain` from the Traefik service IP | [`ingress.ts`](../../infra/foundation/ingress.ts) |
| [D4](#d4-budget-includes-the-aks-node-resource-group) | Budget includes the AKS node resource group | [`monitoring.ts`](../../infra/foundation/monitoring.ts) |
| [D5](#d5-no-management-locks-pulumi-protect-and-state-versioning-instead) | No management locks; Pulumi `protect` and state versioning | [`runtime.ts`](../../infra/foundation/runtime.ts), [`data.ts`](../../infra/foundation/data.ts), [`bootstrap.sh`](../../scripts/bootstrap/bootstrap.sh) |
| [D6](#d6-container-insights-best-effort-behind-a-flag) | Container Insights best effort behind a flag | [`runtime.ts`](../../infra/foundation/runtime.ts) |
| [D7](#d7-catalog-generated-monitoring-carries-the-apps-app-id-tag) | Catalog-generated monitoring carries the app's `app-id` tag | [`monitoring.ts`](../../infra/foundation/monitoring.ts) |
| [D8](#d8-stricter-app-ids-and-a-pattern-for-check-names) | Stricter app IDs, pattern for check names | [`conventions.json`](../../schemas/conventions/conventions.json) |
| [D9](#d9-catalog-field-names-follow-bs-draft) | Catalog field names follow B's draft | [`catalog.ts`](../../infra/foundation/catalog.ts) |
| [D10](#d10-preflight-probes-pat-write-permissions-with-invalid-requests) | Preflight probes PAT write permissions with invalid requests | [`preflight.sh`](../../scripts/bootstrap/preflight.sh) |
| [D11](#d11-git-sha-only-in-the-run-summary-versions-as-an-optional-output) | Git SHA only in the run summary, versions as optional output | [`summary.mjs`](../../scripts/bootstrap/summary.mjs), [`index.ts`](../../infra/foundation/index.ts) |
| [D12](#d12-access-check-runs-once-with-a-break-category-for-the-red-run) | Access check runs once, break category for the red run | [`access-check.sh`](../../scripts/bootstrap/access-check.sh), [`foundation-access-check.yml`](../../../.github/workflows/foundation-access-check.yml) |
| [D13](#d13-agent-health-test-independent-of-the-catalog) | Agent health test independent of the catalog | [`monitoring.ts`](../../infra/foundation/monitoring.ts) |

## D1. Ingress controller: Traefik

- Status: Proposed (pending Erik's decision)
- Context: the spec requires an actively maintained ingress controller and a documented
  choice (requirement "Gemeinsamer HTTP(S)-Zugang ohne eigene Domain"; open question in
  design.md). ingress-nginx, the most common choice, was retired in March 2026: "no
  further releases, no bugfixes, and no updates to resolve any security vulnerabilities"
  ([Kubernetes blog, 2025-11-11](https://kubernetes.io/blog/2025/11/11/ingress-nginx-retirement/)).
- Decision: Traefik from `https://traefik.github.io/charts`, chart version `42.1.3`
  (Traefik v3.7.16 per Artifact Hub; stack setting `traefikChartVersion`) in namespace
  `platform-ingress`, as the default IngressClass `traefik` (output `ingressClassName`).
  The load balancer probes ports 80 and 443 over TCP, because Traefik answers 404 on `/`
  without a matching route. No HTTP-to-HTTPS redirect: the program sets none, and the
  chart default (`ports.web.http.redirections.entryPoint: {}` in 42.1.3) has none, so
  HTTP stays available as the fallback of D2.
- Consequences: app stacks write standard `Ingress` objects with the class from the
  contract. Every host also answers over HTTP; apps that must be HTTPS-only have to
  enforce it themselves. A chart upgrade is an in-place Helm upgrade; replacing the
  release changes the ingress IP (D3).
- Alternatives: ingress-nginx (retired); a managed Azure ingress such as Application
  Gateway for Containers (extra Azure resources and cost, and its controller needs an
  identity with role assignments, which E3 rules out; not evaluated further); a Gateway
  API implementation (app stacks would write routes instead of Ingress objects; B's
  draft uses Ingress).

## D2. cert-manager with HTTP-01 and a switchable HTTP fallback

- Status: Proposed (pending Erik's decision)
- Context: E8 requires HTTPS through Let's Encrypt and HTTP over the same hosts as the
  documented fallback. There is no DNS zone, so DNS-01 is not possible. `sslip.io` is not
  on the Public Suffix List (checked against list version 2026-10-28), so Let's Encrypt
  treats all of `sslip.io` as one registered domain: 50 new certificates per 7 days,
  refilling one every 202 minutes, shared by every sslip.io user
  ([rate limits](https://letsencrypt.org/docs/rate-limits/)). Whether sslip.io has an
  override: unknown, verify live.
- Decision: cert-manager chart `v1.21.3` from `https://charts.jetstack.io` (stack setting
  `certManagerChartVersion`, CRDs installed by the chart) in namespace `platform-certs`,
  ClusterIssuer `letsencrypt` with the ACME HTTP-01 solver on the class `traefik`, account
  e-mail `acmeEmail` (default `humansEmail`), ACME server `acmeServer` (default Let's
  Encrypt production). The stack setting `httpFallback` (default `false`) switches the
  output `publicScheme` to `http`; availability tests and the `ops-intake` webhook then
  use HTTP without certificate checks ([monitoring](monitoring.md#http-fallback)).
  `publicScheme`, `ingressClassName` and `clusterIssuerName` are additions to the output
  contract; adding outputs is compatible, so `contractVersion` stays `1`.
  `foundation.sh <env-file> ingress-check` proves HTTPS on a temporary host (task 3.3).
- Consequences: every app host and every new certificate counts against the shared limit;
  renewals with ACME Renewal Info or the same identifiers are exempt per Let's Encrypt.
  The fallback is global, not per app. While it is on, app traffic and the
  `alertWebhookKey` travel unencrypted (accepted staging risk in design.md). Consumers
  must build URLs from `publicScheme`.
- Alternatives: DNS-01 with an own zone (no domain, E8); Let's Encrypt's staging CA
  (untrusted certificates, tests with TLS check fail); self-signed certificates (same);
  a rate-limit override through Let's Encrypt's form, which their docs allow per domain or
  account (not tried, outcome unknown).

## D3. baseDomain from the Traefik service IP

- Status: Proposed (pending Erik's decision)
- Context: design decision 3 takes the ingress IP from the LoadBalancer service instead of
  a pre-created IP in another resource group, which would need a role assignment. The
  spec scenario "Ingress-IP bleibt stabil" requires an unchanged `baseDomain` as long as
  the controller is not replaced.
- Decision: after the Traefik release the program reads its Service with
  `Service.get` (read only, not a managed resource), takes
  `status.loadBalancer.ingress[0].ip`, fails the run if there is no IP yet, and derives
  `baseDomain = <ip-with-dashes>.sslip.io` (`baseDomainFor` in
  [`conventions.ts`](../../infra/components/conventions.ts)). Outputs: `ingressIp`,
  `baseDomain`, `operationsApiHost`.
- Consequences: the IP is created by AKS in the node resource group and stays while the
  Service exists. It changes when the Service is replaced (Traefik release recreated,
  renamed or moved) or the cluster is recreated (lost-cluster recovery). Then every host,
  every test URL and the `ops-intake` URL change: the foundation run updates its own
  resources, app stacks need a release, the GitHub webhook a manual update, and every
  certificate is issued again (D2). Lost-cluster recovery therefore keeps
  `contractVersion` but not `baseDomain`. Whether the IP survives an AKS stop and start:
  verify live.
- Alternatives: a static IP in the platform resource group (needs `Network Contributor`
  for the cluster identity, a role assignment, E3); a public IP pre-created in the node
  resource group and pinned with a service annotation (would survive a replaced Service
  but not a lost cluster, and puts a foundation resource into the AKS-managed group; not
  tested).

## D4. Budget includes the AKS node resource group

- Status: Proposed (pending Erik's decision)
- Context: the spec asks for a budget alarm "für die Plattform-Resource-Group". The
  cluster's VMs, disks and load balancer, most of the cost, live in the node resource
  group `rg-mmk-platform-<env>-aks-nodes`.
- Decision: one monthly cost budget `budget-mmk-platform-<env>` on the subscription,
  filtered to `rg-mmk-platform-<env>` and `rg-mmk-platform-<env>-aks-nodes`; 80 % actual
  and 100 % forecast to `ops-humans` and `humansEmail`; `budgetAmount` and
  `budgetStartDate` are required stack settings without defaults.
- Consequences: covers the environment's cost, because apps own no Azure resources (E5).
  The state resource group is not included. Creating a subscription-scoped budget with
  Contributor rights: verify live on the first run.
- Alternatives: a budget on the platform resource group only (spec wording; misses the
  cluster cost); one budget per resource group (two to maintain); the whole subscription
  without filter (counts unrelated resources in a shared subscription).

## D5. No management locks; Pulumi protect and state versioning instead

- Status: Proposed (pending Erik's decision)
- Context: Contributor excludes `Microsoft.Authorization/*/Write` and `*/Delete`
  ([built-in roles](https://learn.microsoft.com/en-us/azure/role-based-access-control/built-in-roles/privileged#contributor)),
  so the team cannot create or remove locks.
- Decision: no locks. Pulumi `protect` on the platform resource group, the Log Analytics
  workspace and the registry (`runtime.ts`) and on the PostgreSQL server, the
  `operations` database and the diagnostics storage account (`data.ts`); the AKS cluster
  is not protected so that it can be replaced or recovered. The bootstrap re-applies blob
  versioning and 30-day soft delete for blobs and containers of the state account on every
  run.
- Consequences: `protect` stops only Pulumi: a change that would delete or replace a
  protected resource fails. A deletion in the portal or CLI by any Contributor is not
  blocked; data then depends on PostgreSQL's 7-day backups and the state account's blob
  versions. Deleting the whole state storage account is not covered by blob soft delete;
  whether and how Azure can recover a deleted account: verify live. Removing the
  environment requires `pulumi state unprotect` first ([operations](operations.md)).
- Alternatives: locks set by a subscription owner outside the team (not available in the
  hackathon); Azure Policy deny rules (need the same authorization rights).

## D6. Container Insights best effort behind a flag

- Status: Proposed (pending Erik's decision)
- Context: the spec wants container logs in Log Analytics "soweit sich das ohne
  Rollenzuweisung einrichten lässt"; design.md lists the risk that it needs a role
  assignment; task 3.2 accepts a log query or a documented waiver.
- Decision: stack setting `containerInsights` (default `true`): AKS add-on `omsagent` with
  managed identity authentication, data collection rule `MSCI-<location>-<cluster>` with
  `ContainerLogV2` and ten other streams for all namespaces, and its association with the
  cluster; no role assignment. `false` creates neither add-on nor rule.
- Consequences: Microsoft's own onboarding may add role assignments this program cannot
  create; whether logs arrive without them is unproven, verify live (task 3.2, check in
  [monitoring](monitoring.md#container-insights)). Log Analytics has no daily cap, so a
  noisy namespace raises the bill; the budget (D4) is the only guard. Neither the agent
  nor its outage signal depends on it (E6, E10).
- Alternatives: no Container Insights (`kubectl logs` only, the spec's waiver); an
  in-cluster log stack (more to operate, not in the spec); namespace filtering (lower
  cost, less coverage).

## D7. Catalog-generated monitoring carries the app's app-id tag

- Status: Proposed (pending Erik's decision)
- Context: every Azure resource carries `app-id`, `environment`, `stack` and `managed-by`
  (spec "Ableitbare Ressourcennamen"); E5 puts app tests and alert rules into the
  foundation stack, and the spec does not say which `app-id` they carry.
- Decision: generated tests and rules carry the app's ID (`operations-agent` for the
  health test), `stack: organization/foundation/<env>` and `managed-by: pulumi`. Web tests
  also carry the `hidden-link:<Application Insights ID>` tag that links them to
  Application Insights. All other foundation resources carry `app-id: mmk-platform`.
- Consequences: filtering or cost analysis by `app-id` includes an app's monitoring; the
  `stack` tag still shows that the foundation manages it, so nobody should treat
  "`app-id: affine`" as "owned by the AFFiNE app stack".
- Alternatives: `app-id: mmk-platform` on every foundation resource (loses the per-app
  view).

## D8. Stricter app IDs and a pattern for check names

- Status: Proposed (pending Erik's decision); stricter than the spec, which would need a
  spec change.
- Context: the spec pattern `^[a-z][a-z0-9-]{1,30}$` allows a trailing hyphen (`demo-`),
  but the app ID is the first DNS label of `<app-id>.<baseDomain>`, and `demo-` is not a
  valid label. The spec names `alert-<app-id>-<env>-<check>` without a pattern for
  `<check>`.
- Decision: `conventions.json` adds `appId.endPattern` `[a-z0-9]$` and reserves
  `mmk-platform` and `foundation` (they would collide with `rg-mmk-platform-<env>` and
  `organization/foundation/<env>`); [`app-id.schema.json`](../../schemas/conventions/app-id.schema.json)
  mirrors both. `checkPattern` `^[a-z][a-z0-9-]{0,30}[a-z0-9]$` applies to the check part
  of rule and test names. `conventions.ts` enforces all three, and the catalog reader uses
  it.
- Consequences: an app ID valid under the spec can be rejected here; B's catalog
  validation should read the same rules from `conventions.json`.
- Alternatives: the spec pattern alone (invalid host names possible); a full DNS-label
  rule in the spec.

## D9. Catalog field names follow B's draft

- Status: Proposed (pending Erik's decision); final schema belongs to strand B.
- Context: B owns the catalog schema under `platform/schemas/app-catalog/`, which is not
  published yet. The foundation needs only the app ID and the availability path.
- Decision: read `appId` and `availability.path` as in B's draft
  ([B's design.md](../../../openspec/changes/app-delivery-and-onboarding/design.md));
  validate only these two fields and that `appId` equals the directory name; ignore all
  other fields, including `schemaVersion`.
- Consequences: if B renames a field, the next foundation run fails with one error per
  entry instead of silently dropping monitoring; `catalog.ts` and the fixtures in
  `platform/test/fixtures/catalog/` then follow B's schema. Whether the foundation should
  validate against B's schema once it exists is open.
- Alternatives: wait for B's schema (blocks the monitoring work); validate against a
  local copy of B's draft (two sources of truth).

## D10. Preflight probes PAT write permissions with invalid requests

- Status: Proposed (pending Erik's decision); based on an unverified assumption.
- Context: the preflight must check the PAT's Issues, Contents, Pull requests and Actions
  rights, name every missing one, and create nothing (spec "Dokumentierte manuelle
  Voraussetzungen", task 2.1).
- Decision: read probes must return 200 (Contents also 409 for an empty repository).
  Write probes send an invalid body `{}`: `POST /issues`, `POST /pulls` and
  `POST /git/refs` must return 422; `POST /actions/workflows/0/dispatches` must return
  404 or 422. Any other status, typically 403, counts as missing.
- Consequences: the probes assume that GitHub checks the permission before it validates
  the body or looks up the workflow. If it validates first, a PAT without the write right
  passes. The Actions probe accepts 404, which is weaker evidence. Verify live: run the
  preflight with a PAT lacking Actions write (the spec scenario) and one lacking Issues
  write; both must fail with the named permission.
- Alternatives: create and delete real objects (visible issues, branches, notifications);
  read the token's permissions from the API (no such endpoint for fine-grained PATs
  found; not checked exhaustively).

## D11. Git SHA only in the run summary, versions as an optional output

- Status: Proposed (pending Erik's decision)
- Context: a successful foundation run must print stack name, resource references, Git
  SHA and follow-up commands without secrets (spec "Nachvollziehbare
  Bootstrap-Ergebnisse"); task 5.4 wants versions in the output; task 5.2 wants empty
  previews after each release.
- Decision: `foundation.sh <env-file> up` runs `summary.mjs` after `pulumi up` with the
  SHA of `git rev-parse HEAD`. The summary shows stack, Git SHA, `contractVersion`,
  component versions, URLs, every output (secrets as `[secret]`), how consumers read the
  contract and the next step (access check). The SHA is not a stack output. Component
  versions are the optional output `platformVersions` with `kubernetes`, `traefikChart`,
  `certManagerChart` and `postgres`, taken from the stack config; a compatible addition,
  `contractVersion` stays `1`.
- Consequences: `platformVersions` changes only when a version in the config changes, so
  a preview after an unrelated commit stays empty; a SHA output would change on every
  commit. The SHA is not kept in the state, and the summary of a local run exists only in
  the operator's terminal unless someone saves it. `git rev-parse HEAD` ignores
  uncommitted changes, and `foundation.sh` does not check for a clean working tree, so the
  printed SHA can differ from the deployed code. `platformVersions` reports configured,
  not observed versions.
- Alternatives: SHA as a stack output or resource tag (previews never empty, task 5.2
  fails); Pulumi stack tags (metadata outside the outputs; not evaluated).

## D12. Access check runs once with a break category for the red run

- Status: Proposed (pending Erik's decision)
- Context: the access check must exercise every access path from CI, report each category
  and fail with the affected one; task 5.1 needs a green run and a red run with a
  deliberately wrong key.
- Decision: `workflow_dispatch` with the inputs `environment` (`staging`) and
  `break-category`. Categories: `foundation-state`, `kubernetes`, `acr`, `postgres`,
  `diagnostics-storage`, `github`, and `github-workflow-token` when the repository secret
  `OPS_GITHUB_TOKEN` is set. One attempt per category without retries: keys work at once,
  there is no role propagation to wait for. `break-category` replaces that category's
  credential with an invalid one; `foundation-state` is broken by passing an invalid
  backend key to the composite action. Results go to the job summary.
- Consequences: a transient network error turns the run red; re-run it. When
  `foundation-state` fails, every other category reports "skipped: no foundation
  outputs", so that red run fails all categories, not only the broken one. The check runs
  only on demand; nothing detects drift in between.
- Alternatives: retries with backoff (longer runs, can hide flaky access); one job per
  category (parallel, but every job reads the outputs again).

## D13. Agent health test independent of the catalog

- Status: Proposed (pending Erik's decision)
- Context: where the test lives is already fixed: the spec's ownership table assigns
  "Verfügbarkeitstest des Betriebsagenten" to the foundation stack, and E10 with the
  requirement "Unabhängiges Signal bei Agentenausfall" defines its behaviour. Not fixed
  is how it is created.
- Decision: the foundation creates `webtest-operations-agent-<env>-healthz` on
  `<publicScheme>://<operationsApiHost>/healthz` and `alert-operations-agent-<env>-healthz`
  to `ops-humans` on every run, whether or not the catalog has an `operations-agent`
  entry. That entry, if present, produces nothing; its `availability.path` is not used.
  Check name `healthz`; all other settings equal the app tests.
- Consequences: the signal survives a broken or deleted agent release and a missing or
  broken catalog entry for the agent. It fires from the first foundation run until the
  agent answers 200, so `ops-humans` gets mails before the first agent deployment.
- Alternatives: derive the test from the agent's catalog entry (agent outage monitoring
  would depend on B's entry); put it in the agent's app stack (app stacks hold no Azure
  resources, E5, and it would disappear with the release).
