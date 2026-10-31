# Implementation status: platform-foundation (strand A)

As of 2026-10-31 (geändert 31.10.2026; was: 2026-10-30). A task is checked in [tasks.md](tasks.md) only when its completion check
was actually performed. Code that is written and tested locally, but whose completion
check needs Azure, GitHub secrets or another strand, stays unchecked and is listed here.

Live staging (2026-10-30): state backend `stmmkstateel7bmr` (germanywestcentral) and the
repository secrets exist; the foundation stack `organization/foundation/staging` is
deployed in westeurope (`baseDomain` `57-153-7-126.sslip.io`, Kubernetes 1.35.8, one
`Standard_D2as_v6` node), because this subscription cannot provision PostgreSQL Flexible
Server in germanywestcentral (region restricted) or Standard_B1ms in swedencentral
(SkuNotAvailable). The deployment predates the output `githubAppPrivateKey`; it appears
with the next `up` after `foundation.sh secrets` stored the app key.

Live staging (geändert 31.10.2026; checked read-only on 2026-10-31 06:46 UTC): the
platform status page reports `healthy` for AFFiNE, the operations agent (`/healthz`
HTTP 200, worker heartbeat healthy, database healthy) and the status UI; the agent's
host serves a Let's Encrypt certificate. The last foundation-outputs read in CI (run
[41158387580](https://github.com/mmkdigitalio/hackathon-platform-team/actions/runs/41158387580),
2026-10-31 02:06 UTC) still returned 31 outputs, so `githubAppPrivateKey` is not yet an
output and `Pulumi.staging.yaml` has no `githubAppPrivateKey` entry. Commit `545ba6f`
did not store the key in the foundation; it lets B's workflows fall back to the
repository secret `OPS_GITHUB_APP_PRIVATE_KEY` (created 2026-10-30 18:47 UTC) for the
agent's Kubernetes secret. The access check reads only the foundation output, so its
`github` category stays red. The read-only Azure identity of the agent workspace
(client ID `83e90ad7-5780-418f-9a27-cefdc9b80fc7`) exists since 2026-10-30, created
with the Azure CLI; the stack code behind `agentWorkspaceIdentity` (D14) is merged
(`9114114`), but the identity is not imported into the Pulumi state and the switch is
off in `Pulumi.staging.yaml`. Not verifiable from here: the Azure side (the checking
account has no read access on the subscription), so action groups, availability tests,
budget and the identity itself rest on the 2026-10-30 evidence below.

All local checks run with `cd platform && npm ci && npm test` (TypeScript build,
schema tests, Pulumi mock tests, script tests against stub commands) and
`shellcheck -x platform/scripts/bootstrap/*.sh`; CI runs both in
[foundation-ci.yml](../../../.github/workflows/foundation-ci.yml). Latest run on `main`
(442496a): [41125878232](https://github.com/mmkdigitalio/hackathon-platform-team/actions/runs/41125878232)
green, 109 tests (geändert 31.10.2026; was: 96/96 in run 41059807172).

## Status per task

| Task | State | Evidence | Open completion check |
| --- | --- | --- | --- |
| 1.1 Monorepo, CODEOWNERS | Done | [.github/CODEOWNERS](../../../.github/CODEOWNERS), `platform/test/codeowners.test.mjs` maps one path per row of the ownership table to its strand | Owners confirm their handle |
| 1.2 Conventions | Done | `platform/schemas/conventions/`, `npm run conventions`, `platform/test/conventions.test.mjs` (`Demo_App` rejected, names for `affine`) | none |
| 1.3 Output schema v0 | Done (geändert 31.10.2026; was: Implemented) | `platform/schemas/foundation-outputs/` with placeholder `example.staging.json`; B, C and D read the real outputs through the action in green workflows: `app-deploy` [41125866426](https://github.com/mmkdigitalio/hackathon-platform-team/actions/runs/41125866426) (B), `health-check` [41158387580](https://github.com/mmkdigitalio/hackathon-platform-team/actions/runs/41158387580) (C), `agent-build` [41124648862](https://github.com/mmkdigitalio/hackathon-platform-team/actions/runs/41124648862) (D); each logs `Read 31 foundation outputs (stack)` | none; no written confirmation by the people, the check is met by use |
| 2.1 Preflight | Implemented | `platform/scripts/bootstrap/preflight.sh`, `platform/test/bootstrap.test.mjs`; live run on 2026-10-30 confirmed Contributor and repository admin and stopped with named rights, then still for the PAT (Issues, Pull requests and Actions write missing, HTTP 403). Since the switch to the GitHub App (2026-10-30) it reads the installation's ID and permissions; stub tests cover a missing installation, a wrong installation ID, a missing Actions write and a key of another app. It also checks regional VM availability, vCPU quota and PostgreSQL availability. Unchecked again because the task now names the app's permissions; no live run with the key file since | Live preflight with `GITHUB_APP_PRIVATE_KEY_FILE` |
| 2.2 Idempotent bootstrap | Implemented | `bootstrap.sh`; tests for first run, repeated run without change, completion after an interrupted run | Two live runs and one interrupted run |
| 2.3 Repo and stack secrets | Implemented | Repository secrets `PULUMI_BACKEND_ACCOUNT`, `PULUMI_BACKEND_KEY`, `PULUMI_CONFIG_PASSPHRASE` exist (`gh secret list`, 2026-10-30 13:35 UTC); `OPS_GITHUB_TOKEN` is optional since the GitHub App. Stack config holds the Azure OpenAI key and `githubWebhookSecret` as ciphertext; `foundation.sh secrets` stores the app key after `openssl` read it as an unencrypted private key | Store `githubAppPrivateKey` in the stack config (needs the `.pem` file from Erik; `Pulumi.staging.yaml` does not hold it on 2026-10-31). The repository secret `OPS_GITHUB_APP_PRIVATE_KEY` (2026-10-30) is a CI fallback of B and does not close this (geändert 31.10.2026) |
| 2.4 Composite action | Done | `.github/actions/foundation-outputs/`; tests read the placeholder outputs end to end, abort on a wrong contract version, mask secrets (also ones unknown to the schema) and check every workflow's inputs and outputs; `foundation-ci` run 41059807172 on `main` (049529a) green: 96/96 tests, action read 31 placeholder outputs | none |
| 3.1 Base platform | Implemented | `platform/infra/foundation/` (resource group, Log Analytics, App Insights, ACR admin, AKS with local accounts), mock tests: no role assignments; local `foundation.sh up` on 2026-10-30 created the stack without role errors, but under an Owner account | A run (for example `preview --expect-no-changes`) by someone with Contributor only. With `agentWorkspaceIdentity` on (D14) a run needs Owner or User Access Administrator, an exception to E3 (geändert 31.10.2026) |
| 3.2 Container Insights | Done | DCR without role assignment behind `containerInsights`; procedure in `operations/foundation/monitoring.md`. On 2026-10-30 a pod `default/logtest` wrote `mmk-logtest-20261030T142513Z` at 14:25:15 UTC; `ContainerLogV2 \| where LogMessage has '<marker>'` returned it at 14:28:50 UTC, so logs arrive without a role assignment | none |
| 3.3 Ingress, certificates | Done | Traefik, cert-manager, Let's Encrypt HTTP-01, `baseDomain` from the service IP; `foundation.sh ingress-check` passed on 2026-10-30 14:09 UTC: `https://ingress-check.57-153-7-126.sslip.io/` answered with a valid Let's Encrypt certificate (issued in about 23 s) | none |
| 3.4 PostgreSQL | Done | Flexible Server, database `operations`, TLS required, open firewall, 7 days backup; access check run [41070864441](https://github.com/mmkdigitalio/hackathon-platform-team/actions/runs/41070864441) on `ubuntu-latest`: `postgres` PASS (login with `sslmode=require`, session encrypted) | none |
| 3.5 Diagnostics storage | Done | Container `diagnostics`, 30-day lifecycle; access check run [41070864441](https://github.com/mmkdigitalio/hackathon-platform-team/actions/runs/41070864441): `diagnostics-storage` PASS (write, read, compare and delete a blob with the key) | none |
| 3.6 Keys and contract | Implemented | `alertWebhookKey`, `maintenanceApiKey`, the contract outputs plus optional `platformVersions`; mock outputs validate against the schema. The 31 real outputs (2026-10-30, still 31 in run 41158387580 on 2026-10-31) validate except the required `githubAppPrivateKey`, which the deployment predates. `agentWorkspaceAzure` (D14) is an additional output outside the contract and absent while the switch is off (geändert 31.10.2026) | Schema validation of the real outputs after the next `up` |
| 4.1 Action groups | Implemented | `ops-intake` (webhook with key), `ops-humans` (team mail) | Test notification |
| 4.2 Catalog alerts | Implemented | Availability test and alert per `platform/catalog/*/app.yaml` with custom properties and auto-resolve | Fired and resolved event for a test entry |
| 4.3 Agent health test | Implemented | Test on `<publicScheme>://<operationsApiHost>/healthz` to `ops-humans` | Mail for a stopped worker. Precondition met on 2026-10-31: the agent is deployed and `/healthz` answers 200; the check itself was not run (geändert 31.10.2026) |
| 4.4 Budget | Done | `budget-mmk-platform-staging` (`az consumption budget list`, 2026-10-30): 150 per month from 2026-10-01, filter `rg-mmk-platform-staging` and `rg-mmk-platform-staging-aks-nodes`, notifications at 80 % actual and 100 % forecast to the four `humansEmail` addresses and action group `ops-humans` | none |
| 5.1 Access check | Implemented | `.github/workflows/foundation-access-check.yml`, `access-check.sh`, `platform/test/access-check.test.mjs` (every category on its own, a broken credential fails exactly its category). Run [41070864441](https://github.com/mmkdigitalio/hackathon-platform-team/actions/runs/41070864441): every category PASS except `github` (`GITHUB_APP_PRIVATE_KEY is empty`). Red run [41070881648](https://github.com/mmkdigitalio/hackathon-platform-team/actions/runs/41070881648) with `break-category=postgres`: `postgres` FAIL, the others as in the first run | Green run once the app key is stored in the foundation (the workflow ignores the repository secret `OPS_GITHUB_APP_PRIVATE_KEY`); no run since 2026-10-30 14:19 UTC (geändert 31.10.2026) |
| 5.2 Mutual previews | Prepared | `foundation.sh preview --expect-no-changes`; procedure in `operations/foundation/operations.md` | Empty previews after one release each |
| 5.3 Operations docs | Written | `platform/operations/foundation/` (bootstrap, operations, monitoring, handover, decisions) | Cluster recovery by a second person. The runbook now also covers the agent workspace identity (adoption into the state) (geändert 31.10.2026) |
| 5.4 Acceptance scenario 1 | Prepared | `foundation.sh up` prints stack, Git SHA, versions and URLs (`summary.mjs`) | Deployment from bootstrap and Git state |

## Decisions and assumptions

Implementation decisions of strand A are recorded as **Proposed** in
[decisions.md](../../../platform/operations/foundation/decisions.md) until Erik decides on
them. Assumptions that only a live run can settle are marked "verify live" in the runbooks;
the most important ones:

- The ingress IP survives an AKS stop and start, so `baseDomain` stays stable overnight.
- A subscription budget can be created with Contributor rights.

Decision D14 (read-only Azure identity for the agent workspace, `agentWorkspaceIdentity`,
default off) is an opt-in exception to E3 and E6 and also **Proposed** (geändert
31.10.2026; first numbered D12, which was already taken by the access check).

## Integration notes

- B's catalog entries (`platform/catalog/*/app.yaml`) use `appId` and `availability.path`,
  the fields the foundation reads; `operations-agent` is monitored only through `/healthz`.
- Every workflow on `main` that uses `./.github/actions/foundation-outputs` passes only
  declared inputs and reads only declared outputs; a contract test keeps it that way.
