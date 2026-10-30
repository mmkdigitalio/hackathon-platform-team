# Implementation status: platform-foundation (strand A)

As of 2026-10-30. A task is checked in [tasks.md](tasks.md) only when its completion check
was actually performed. Code that is written and tested locally, but whose completion
check needs Azure, GitHub secrets or another strand, stays unchecked and is listed here.

Nothing has been deployed to Azure, and no GitHub secrets, webhooks or repositories were
created. All local checks run with `cd platform && npm ci && npm test` (TypeScript build,
schema tests, Pulumi mock tests, script tests against stub commands) and
`shellcheck -x platform/scripts/bootstrap/*.sh`; CI runs both in
[foundation-ci.yml](../../../.github/workflows/foundation-ci.yml).

## Status per task

| Task | State | Evidence | Open completion check |
| --- | --- | --- | --- |
| 1.1 Monorepo, CODEOWNERS | Done | [.github/CODEOWNERS](../../../.github/CODEOWNERS), `platform/test/codeowners.test.mjs` maps one path per row of the ownership table to its strand | Owners confirm their handle |
| 1.2 Conventions | Done | `platform/schemas/conventions/`, `npm run conventions`, `platform/test/conventions.test.mjs` (`Demo_App` rejected, names for `affine`) | none |
| 1.3 Output schema v0 | Implemented | `platform/schemas/foundation-outputs/` with placeholder `example.staging.json`; B, C and D already read it through the action in their workflows | Confirmation by B, C and D |
| 2.1 Preflight | Implemented | `platform/scripts/bootstrap/preflight.sh`, `platform/test/bootstrap.test.mjs` (missing Contributor, admin, PAT rights each abort with a named right) | Live run with a real PAT; the write probes assume GitHub checks permissions before validating the body |
| 2.2 Idempotent bootstrap | Implemented | `bootstrap.sh`; tests for first run, repeated run without change, completion after an interrupted run | Two live runs and one interrupted run |
| 2.3 Repo and stack secrets | Implemented | `bootstrap.sh` sets the four repo secrets through stdin; `foundation.sh secrets` stores model keys, webhook secret and PAT as encrypted stack config | Secret list on GitHub; needs Erik's go for the live bootstrap |
| 2.4 Composite action | Done | `.github/actions/foundation-outputs/`; tests read the placeholder outputs end to end, abort on a wrong contract version, mask secrets (also ones unknown to the schema) and check every workflow's inputs and outputs; `foundation-ci` run 41059807172 on `main` (049529a) green: 96/96 tests, action read 31 placeholder outputs | none |
| 3.1 Base platform | Implemented | `platform/infra/foundation/` (resource group, Log Analytics, App Insights, ACR admin, AKS with local accounts), mock tests: no role assignments | Local `foundation.sh up` without role errors |
| 3.2 Container Insights | Implemented | DCR without role assignment behind `containerInsights`; procedure in `operations/foundation/monitoring.md` | Log query of a test line, or documented waiver |
| 3.3 Ingress, certificates | Implemented | Traefik, cert-manager, Let's Encrypt HTTP-01, `baseDomain` from the service IP; `foundation.sh ingress-check` | HTTPS call of `ingress-check.<baseDomain>` |
| 3.4 PostgreSQL | Implemented | Flexible Server, database `operations`, TLS required, open firewall, 7 days backup; `access-check.sh` postgres category | Connection from a GitHub-hosted runner |
| 3.5 Diagnostics storage | Implemented | Container `diagnostics`, 30-day lifecycle; access-check category writes and reads with the key | Live write and read |
| 3.6 Keys and contract | Implemented | `alertWebhookKey`, `maintenanceApiKey`, 30 contract outputs plus optional `platformVersions`; mock outputs validate against the schema | Schema validation of the real outputs |
| 4.1 Action groups | Implemented | `ops-intake` (webhook with key), `ops-humans` (team mail) | Test notification |
| 4.2 Catalog alerts | Implemented | Availability test and alert per `platform/catalog/*/app.yaml` with custom properties and auto-resolve | Fired and resolved event for a test entry |
| 4.3 Agent health test | Implemented | Test on `<publicScheme>://<operationsApiHost>/healthz` to `ops-humans` | Mail for a stopped worker, after B and C roll out the agent |
| 4.4 Budget | Implemented | Budget on the platform and AKS node resource groups | Visible budget with recipient |
| 5.1 Access check | Implemented | `.github/workflows/foundation-access-check.yml`, `access-check.sh`, `platform/test/access-check.test.mjs` (every category on its own, a broken credential fails exactly its category) | Green run and red run on GitHub |
| 5.2 Mutual previews | Prepared | `foundation.sh preview --expect-no-changes`; procedure in `operations/foundation/operations.md` | Empty previews after one release each |
| 5.3 Operations docs | Written | `platform/operations/foundation/` (bootstrap, operations, monitoring, handover, decisions) | Cluster recovery by a second person |
| 5.4 Acceptance scenario 1 | Prepared | `foundation.sh up` prints stack, Git SHA, versions and URLs (`summary.mjs`) | Deployment from bootstrap and Git state |

## Decisions and assumptions

Implementation decisions of strand A are recorded as **Proposed** in
[decisions.md](../../../platform/operations/foundation/decisions.md) until Erik decides on
them. Assumptions that only a live run can settle are marked "verify live" in the runbooks;
the most important ones:

- GitHub answers the preflight write probes (empty bodies) with 403 before 422 when a PAT
  lacks the permission.
- The ingress IP survives an AKS stop and start, so `baseDomain` stays stable overnight.
- Container Insights delivers logs without a role assignment.
- A subscription budget can be created with Contributor rights.

## Integration notes

- B's catalog entries (`platform/catalog/*/app.yaml`) use `appId` and `availability.path`,
  the fields the foundation reads; `operations-agent` is monitored only through `/healthz`.
- Every workflow on `main` that uses `./.github/actions/foundation-outputs` passes only
  declared inputs and reads only declared outputs; a contract test keeps it that way.
