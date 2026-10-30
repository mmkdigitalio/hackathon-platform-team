# platform

Shared Azure infrastructure (Pulumi TypeScript), app catalog, desired state per
environment, conventions, output contract and platform runbooks. The operations agent
lives next to it in [`../operations-agent/`](../operations-agent/). The implementation
follows the OpenSpec change
[`platform-foundation`](../openspec/changes/platform-foundation/); file ownership is
defined in [`openspec/PARALLEL-IMPLEMENTATION.md`](../openspec/PARALLEL-IMPLEMENTATION.md#dateizuständigkeit-im-monorepo).

## Ownership

Strands: **A** platform foundation, **B** app delivery and onboarding, **C** operations
intake and state, **D** operations agent and tools.

| Path | Owner | Content |
| --- | --- | --- |
| `infra/foundation/`, `infra/components/` | A | Foundation stack `organization/foundation/<env>`: every Azure resource, including the availability tests generated from the catalog |
| `scripts/bootstrap/` | A | Preflight, state bootstrap, foundation wrapper, access check |
| `schemas/conventions/`, `schemas/foundation-outputs/` | A | Naming conventions, foundation output contract |
| `operations/foundation/` | A | Foundation runbooks |
| `infra/app-runtime/`, `schemas/app-catalog/`, `catalog/`, `environments/`, `operations/apps/` | B | App stacks `organization/app-<app-id>/<env>`, catalog, desired state |
| `schemas/events/`, `operations/state/` | C | Event contracts, maintenance intake, database initialisation |
| `operations/agent/` | D | Platform-wide agent operating rules |
| `package.json`, `tsconfig.json`, `test/` | A, shared | Build and tests; each owner tests their area |

The same resource is never managed from two places. Azure resources belong to the
foundation stack; Kubernetes objects of an app (namespace, deployment, ingress, secrets)
belong to its app stack. A new app therefore needs a foundation run for its monitoring,
but none for its deployment.

## Naming example

Names derive from app ID and environment
([`schemas/conventions/conventions.json`](schemas/conventions/conventions.json),
`npm run conventions -- names <app-id> <env> [baseDomain]`):

```text
app-id: affine
stack: organization/app-affine/staging
namespace: affine-staging
host: affine.<baseDomain>
alert rule: alert-affine-staging-availability
foundation stack: organization/foundation/staging
foundation resource group: rg-mmk-platform-staging
```

`baseDomain` is `<ingress-ip-with-dashes>.sslip.io`, e.g. `20-79-1-2.sslip.io`; it is
known only after the first foundation run and is read from the output contract.

## Working locally

```bash
cd platform
npm ci
npm test                                   # compile, unit tests, Pulumi mock tests, script tests
npm run conventions -- app-id Demo_App     # exits 1: invalid app ID
npm run conventions -- names affine staging
shellcheck -x scripts/bootstrap/*.sh
```

The script tests run the bootstrap scripts against stub commands and need bash >= 4.4
(macOS: `brew install bash`); without it they are skipped. Nothing here touches Azure or
GitHub. Consumers can develop against the placeholder
outputs in [`schemas/foundation-outputs/example.staging.json`](schemas/foundation-outputs/example.staging.json)
or with `source: placeholder` in the composite action.

## Documentation

- [Platform status UI](operations/state/status-ui.md): read-only overview in the operations API, local preview, live checks and deployment
- [Bootstrap](operations/foundation/bootstrap.md): prerequisites, preflight, state backend,
  first foundation run and access check
- [Operations](operations/foundation/operations.md): upgrades, stop and start, recovery of
  cluster, state and database, accepted staging risks
- [Monitoring](operations/foundation/monitoring.md): alert routing, agent health signal,
  budget, Container Insights, test notifications
- [Handover](operations/foundation/handover.md): output contract and how B, C and D read it
- [Decisions](operations/foundation/decisions.md): implementation decisions of strand A
  (proposed, pending owner decision)
