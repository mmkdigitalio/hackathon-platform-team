# Foundation monitoring

What the foundation stack `organization/foundation/<env>` monitors, where alerts go, how
to test the alert paths and how to trace an alert. Every monitoring resource is an Azure
resource and therefore lives in the foundation stack (E5 in
[PARALLEL-IMPLEMENTATION.md](../../../openspec/PARALLEL-IMPLEMENTATION.md)).

Sources: [`monitoring.ts`](../../infra/foundation/monitoring.ts),
[`catalog.ts`](../../infra/foundation/catalog.ts),
[`runtime.ts`](../../infra/foundation/runtime.ts),
[`config.ts`](../../infra/foundation/config.ts); behaviour pinned by
[`foundation-program.test.mjs`](../../test/foundation-program.test.mjs),
[`foundation-program-fallback.test.mjs`](../../test/foundation-program-fallback.test.mjs)
and [`catalog.test.mjs`](../../test/catalog.test.mjs). "Verify live" marks what cannot
be proven from the repository and still needs a check against the real environment.

## Building blocks

| Resource | Name | Purpose |
| --- | --- | --- |
| Log Analytics workspace | `log-mmk-platform-<env>` | `PerGB2018`, 30 days retention, no daily cap |
| Application Insights (workspace-based) | `appi-mmk-platform-<env>` | Hosts every availability test; apps may send telemetry through `appInsightsConnectionString` |
| Action group | `ops-intake` | Webhook to the event intake of the operations agent (C) |
| Action group | `ops-humans` | E-mail to the team distribution list `humansEmail`; no webhook |
| Availability test and alert rule per catalog app | `webtest-<app-id>-<env>-availability`, `alert-<app-id>-<env>-availability` | App outage to `ops-intake` |
| Availability test and alert rule of the agent | `webtest-operations-agent-<env>-healthz`, `alert-operations-agent-<env>-healthz` | Agent outage to `ops-humans` |
| Budget | `budget-mmk-platform-<env>` | Monthly cost to `ops-humans` |
| Container Insights (optional) | add-on `omsagent`, data collection rule `MSCI-<location>-aks-mmk-platform-<env>` | Container logs for humans |

## Alert routing

| Signal | Rule | Target |
| --- | --- | --- |
| A catalog app does not answer | `alert-<app-id>-<env>-availability` | `ops-intake` with custom properties `app-id` and `environment` |
| The agent API `/healthz` does not answer 200 | `alert-operations-agent-<env>-healthz` | `ops-humans` only |
| Cost above 80 % (actual) or 100 % (forecast) of the budget | budget notifications | `ops-humans` and `humansEmail` |

### Availability tests from the catalog

The foundation reads every `platform/catalog/<app-id>/app.yaml` and creates one test and
one alert rule per entry, except for `operations-agent`, which must not alert itself
through `ops-intake`. It reads only `appId` and `availability.path` (field names from B's
draft, see [decisions](decisions.md)). On this branch `platform/catalog/` does not exist
yet, so until B adds entries the stack creates only the agent's `/healthz` test.

| Setting | Value |
| --- | --- |
| Test | Standard test `webtest-<app-id>-<env>-availability`, `GET <publicScheme>://<app-id>.<baseDomain><availability.path>`, dependent requests not parsed |
| Frequency, timeout | Every 300 seconds from each location, 30 seconds timeout |
| Retries | On; Azure reports a location as failed only after three failed attempts in a row ([Microsoft Learn](https://learn.microsoft.com/en-us/azure/azure-monitor/app/availability)) |
| Success | HTTP 200. With HTTPS also a valid TLS certificate that is valid for at least 7 more days; with the [HTTP fallback](#http-fallback) only HTTP 200 |
| Locations | `emea-nl-ams-azr` (West Europe), `emea-gb-db3-azr` (North Europe), `emea-fr-pra-edge` (France Central), `emea-ch-zrh-edge` (France South), `emea-se-sto-edge` (UK West); display names from Microsoft Learn, the city codes in the IDs do not match them |
| Alert rule | Metric alert `alert-<app-id>-<env>-availability`, criterion `WebtestLocationAvailabilityCriteria` with 2 failed locations, evaluated every minute over a 5-minute window (`PT5M`), severity 1 |
| Resolution | `autoMitigate: true`: one `Fired` event when the condition starts, one `Resolved` event when it clears; no repeated events while it stays fired |
| Action | `ops-intake` with custom properties `app-id: <app-id>` and `environment: <env>` |

Microsoft recommends a threshold of "number of locations minus 2", that is 3 of 5. The
program alerts at 2 of 5: earlier, with a higher chance of an alert caused by one
location's network. The time from outage to `Fired` follows from the values above (about
5 to 10 minutes) but is an inference; verify live with the first real alert.

### ops-intake

The webhook receiver `operations-agent-intake` posts the Common Alert Schema to

```text
<publicScheme>://<operationsApiHost>/events/azure-monitor?key=<alertWebhookKey>
```

`publicScheme` is `https` unless the HTTP fallback is on. `operationsApiHost` is
`operations-agent.<baseDomain>`; B publishes the agent API there, C's receiver checks the
key. The URL is a Pulumi secret in the state, but in Azure it is an ordinary property of
the action group: anyone who can read the platform resource group can read the key.

Fields C can use (Microsoft's
[common alert schema](https://learn.microsoft.com/en-us/azure/azure-monitor/alerts/alerts-common-schema)):

| Field | Content |
| --- | --- |
| `data.essentials.alertRule` | `alert-<app-id>-<env>-availability` |
| `data.essentials.monitorCondition` | `Fired` or `Resolved` |
| `data.essentials.alertId`, `firedDateTime`, `resolvedDateTime` | Alert instance and times |
| `data.customProperties` | `{"app-id": "<app-id>", "environment": "<env>"}` |
| `data.alertContext.condition.allOf[0].webTestName` | `webtest-<app-id>-<env>-availability` |

C reads the custom properties first and falls back to the rule name (event contract in
PARALLEL-IMPLEMENTATION.md). There is no queue between Azure and the intake: while the
intake is not running, deliveries fail, and the platform keeps no copy of an event Azure
could not deliver.

### Agent outage signal

The test `webtest-operations-agent-<env>-healthz` calls
`<publicScheme>://<operationsApiHost>/healthz` with the same settings as an app test. Its
rule `alert-operations-agent-<env>-healthz` sends only to `ops-humans` (e-mail), never to
the agent's intake. The test is created on every foundation run, independent of the
catalog, so it neither needs a model call nor a running agent.

- Per the spec (E10), `/healthz` answers 503 when the database is unreachable or the last
  worker heartbeat is older than 10 minutes. Strand C implements that; the foundation
  only checks for HTTP 200.
- If the agent release is deleted, Traefik answers 404 for the host, the test fails and
  the alert fires. The same happens before the first agent deployment: after the first
  foundation run `ops-humans` gets a `Fired` mail that resolves once `/healthz` answers 200.
- The mail carries the rule name and the description
  `Environment <env>, app operations-agent: <url> failed from at least two locations (test webtest-operations-agent-<env>-healthz).`
- Detection of a stopped worker takes the 10-minute heartbeat age plus the test and alert
  delays above. Task 4.3 (mail with a stopped worker) needs the agent deployed by B and C:
  verify live.

When every test fails at the same time, look at the shared path first: sslip.io DNS,
Traefik and its load balancer, the cluster, or (with HTTPS) the certificates. Each app
then sends its own `Fired` event to `ops-intake`.

## Budget

| Setting | Value |
| --- | --- |
| Scope | Subscription, filtered to `ResourceGroupName` in `rg-mmk-platform-<env>` and the AKS node resource group `rg-mmk-platform-<env>-aks-nodes` (VMs, disks, load balancer) |
| Amount, period | `budgetAmount` in the subscription's billing currency, monthly cost from `budgetStartDate` (`YYYY-MM-01`); both required, no defaults |
| Notifications | Actual cost above 80 %, forecast above 100 %; each to the action group `ops-humans` and directly to `humansEmail` |

Apps own no Azure resources (E5), so the two resource groups carry the environment's
cost; the state resource group `rg-mmk-platform-state` is not included. A budget only
notifies, it stops nothing, and Azure's cost data arrives with a delay. Task 4.4 (budget
visible with its recipients under Cost Management → Budgets): verify live.

## Catalog entries and foundation runs

- Tests and rules generated from the catalog live in the foundation stack (tag
  `stack: organization/foundation/<env>`) but carry the app's tag `app-id: <app-id>`; the
  agent's test carries `operations-agent`. All other foundation resources carry
  `app-id: mmk-platform`.
- A new, changed or removed catalog entry takes effect on the next foundation run, which
  is local only:

  ```bash
  platform/scripts/bootstrap/foundation.sh platform/scripts/bootstrap/staging.env preview
  platform/scripts/bootstrap/foundation.sh platform/scripts/bootstrap/staging.env up
  ```

  App releases never need a foundation run; only their monitoring does.
- Own recommendation: run the foundation after the app's first release. Otherwise the
  new test fails from the start and the first event the intake sees is a `Fired` alert
  for an app that is not deployed yet.
- An invalid entry fails the whole run before any resource is touched, with one line per
  broken file (`CatalogError`). Checked: the file is a YAML mapping; `appId` follows the
  conventions (pattern, ends with a letter or digit, not reserved) and equals its
  directory name; `availability.path` is an absolute path such as `/healthz`. Directories
  without `app.yaml` are skipped, and a missing catalog directory means no app tests.
  All other fields, including `schemaVersion`, belong to B's schema and are not checked
  here.

## Container Insights

Best effort, behind the stack setting `containerInsights` (default `true`):

- On: AKS add-on `omsagent` with managed identity authentication (`useAADAuth`) to the
  workspace, the data collection rule `MSCI-<location>-aks-mmk-platform-<env>`
  (`ContainerLogV2` and ten other Container Insights streams, all namespaces, 1-minute
  interval) and its association with the cluster. The foundation creates no role
  assignment for it.
- Off: neither add-on nor data collection rule; logs are then only available through
  `kubectl logs`.
- Only humans query `ContainerLogV2`. The agent needs no Log Analytics access (spec,
  E6), and the agent outage signal does not depend on Container Insights (E10).
- Whether logs arrive without a role assignment is not proven. Task 3.2 is complete only
  with a live log query of a test line, or with a documented switch to
  `containerInsights: false`: verify live.

Check for task 3.2 (Contributor may list the cluster admin credentials):

```bash
export KUBECONFIG="$(mktemp -d)/kubeconfig"
az aks get-credentials -g rg-mmk-platform-staging -n aks-mmk-platform-staging --admin --file "$KUBECONFIG"
marker="log-check-$(date -u +%s)"; echo "$marker"
kubectl create namespace log-check
kubectl -n log-check run log-check --image=busybox:1.36 --restart=Never -- sh -c "echo $marker"
# after the query below has found the line:
kubectl delete namespace log-check
```

Queries for humans (portal: Log Analytics workspace `log-mmk-platform-<env>` → Logs):

```kusto
// The marker line of the check above
ContainerLogV2
| where TimeGenerated > ago(1h)
| where PodNamespace == "log-check" and tostring(LogMessage) has "<marker>"

// Recent logs of one app namespace
ContainerLogV2
| where TimeGenerated > ago(30m)
| where PodNamespace == "affine-staging"
| project TimeGenerated, PodName, ContainerName, LogMessage
| order by TimeGenerated desc

// Availability test results per location (written by Application Insights, no Container Insights needed)
AppAvailabilityResults
| where TimeGenerated > ago(1h)
| where Name == "webtest-affine-staging-availability"
| project TimeGenerated, Location, Success, Message, DurationMs
| order by TimeGenerated desc
```

## HTTP fallback

The stack setting `httpFallback: true` is the documented reaction when Let's Encrypt does
not issue certificates (see [handover](handover.md#https-and-the-http-fallback)):

| | HTTPS (default) | HTTP fallback |
| --- | --- | --- |
| `publicScheme` output | `https` | `http` |
| Test URLs | `https://<app-id>.<baseDomain><path>`, `https://<operationsApiHost>/healthz` | the same with `http://` |
| Validation | HTTP 200, TLS check, certificate valid for at least 7 more days | HTTP 200 only |
| `ops-intake` webhook | `https://…?key=…` | `http://…?key=…`; the key crosses the network unencrypted |

Traefik never redirects HTTP to HTTPS, so the hosts answer over HTTP in both modes. In
HTTPS mode a certificate problem (not issued, expiring within 7 days) fails the tests:
app alerts go to `ops-intake`, the agent alert to `ops-humans`.

Switching in either direction: set `foundation:httpFallback` to `true` or `false` in
`platform/infra/foundation/Pulumi.<env>.yaml` (or `pulumi config set httpFallback true`
with the backend environment from [bootstrap](bootstrap.md)), commit, then run
`platform/scripts/bootstrap/foundation.sh <env-file> up`. The run updates every test and
the `ops-intake` URL; consumers that build URLs from `publicScheme` follow with their next
release. Before switching back, check that certificates are issued again:
`foundation.sh <env-file> ingress-check`.

## Test notifications

Azure portal → Monitor → Alerts → Action groups → select the group → *Test*, sample type
for example *Metric alert*.

- `ops-humans` (task 4.1, before the staging acceptance): the team distribution list
  receives an e-mail; nothing reaches the intake, because the group has no webhook.
- `ops-intake`, once C's intake runs: the test request carries the configured URL with
  key and must be accepted. The rejection half (same request without key) is C's test:
  send a sample payload without the `key` parameter and expect the rejection status C
  defines.

  ```bash
  curl -sS -o /dev/null -w '%{http_code}\n' -X POST -H 'Content-Type: application/json' \
    --data @sample-alert.json "<publicScheme>://<operationsApiHost>/events/azure-monitor"
  ```

- End to end with a real alert (task 4.2, joint acceptance P1-09): stop a catalog app,
  for example B's scenario that scales AFFiNE to 0, and expect `Fired` with `app-id`
  `affine` and `environment` `staging`, then `Resolved` after recovery: verify live.

## Tracing an alert

1. **Azure**: Monitor → Alerts, filter by the rule `alert-<app-id>-<env>-availability`.
   The alert shows monitor condition, fired and resolved times and its history; which
   delivery details of the action group Azure shows there: verify live.
2. **Test results**: Application Insights `appi-mmk-platform-<env>` → Availability, or the
   `AppAvailabilityResults` query above; `Message` explains the failure per location.
3. **Payload**: match `data.essentials.alertId`, `alertRule` and `monitorCondition` with
   what the intake stored. The custom properties name app and environment.
4. **Intake and job**: C's receiver deduplicates and stores the event and creates or
   updates a job. The job model and its log fields are C's; look them up in C's
   documentation once published.
5. **Agent logs**: namespace `operations-agent-<env>`, through `ContainerLogV2` (if
   Container Insights works) or `kubectl logs`.
6. **Artifacts**: larger diagnostics are in the storage account
   `diagnosticsStorageAccount`, container `diagnostics`, deleted after 30 days.
