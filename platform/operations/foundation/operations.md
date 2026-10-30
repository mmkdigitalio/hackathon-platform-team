# Foundation operations

Runbook for the foundation stack `organization/foundation/staging`. The stack is deployed
only locally, by a person with Contributor via `az login` and
[`foundation.sh`](../../scripts/bootstrap/foundation.sh). There is no CI deployment, and
recovery does not run from GitHub Actions because workflows have no Azure identity.
Every procedure works without a running operations agent; the foundation never takes the
operations lock.

Conventions on this page:

- Commands run from the repository root with `PULUMI_CONFIG_PASSPHRASE` exported
  ([bootstrap step 1](bootstrap.md#step-1-record-the-environment-values)).
- Direct `pulumi` commands need the
  [backend environment](bootstrap.md#backend-environment).
- Local `kubectl` uses the admin kubeconfig (cluster-admin; delete the file when done):

  ```bash
  az aks get-credentials --admin --resource-group rg-mmk-platform-staging \
    --name aks-mmk-platform-staging --file ~/.kube/mmk-staging-admin
  export KUBECONFIG=~/.kube/mmk-staging-admin
  ```

- Names from the outputs, read without secret values:

  ```bash
  outputs="$(platform/scripts/bootstrap/foundation.sh platform/scripts/bootstrap/staging.env outputs)"
  pg_server="$(jq -r .postgresHost <<<"$outputs" | cut -d. -f1)"
  acr_name="$(jq -r .acrLoginServer <<<"$outputs" | cut -d. -f1)"
  diag_account="$(jq -r .diagnosticsStorageAccount <<<"$outputs")"
  ```

## Protected resources

The program marks these resources with Pulumi `protect`. A change that would delete or
replace one of them fails the run instead:

- resource group `rg-mmk-platform-staging`
- Log Analytics workspace `log-mmk-platform-staging`
- container registry
- PostgreSQL server and database `operations`
- diagnostics storage account

Not protected: the AKS cluster and its node resource group, Application Insights,
Traefik, cert-manager and all other Kubernetes objects, action groups, budget,
availability tests, the firewall rule and the generated values. There is no Azure lock
(Contributor cannot create one), so anyone with Contributor can still delete any of these
resources directly in Azure.

Settings with irreversible or wide effects; read the preview before `up`:

| Setting | Effect |
| --- | --- |
| `kubernetesVersion` | AKS does not downgrade (verify live) |
| `location` | Would replace protected resources, so the run fails; a move is a new environment |
| `postgresVersion` | A major upgrade cannot be reverted; if the preview shows a replacement, `protect` stops it (verify live) |
| `postgresStorageGb` | Flexible Server storage grows only (verify live) |
| `nodeVmSize`, `nodeCount` | Changes the node pool; if the preview shows `aks` replaced, that is a new cluster (verify live) |

Rule: if a preview shows a replacement of `aks` or of the `traefik` release, stop. It
means a new ingress IP, a new `baseDomain` and the follow-up work of a
[deleted cluster](#deleted-cluster).

## Foundation release

Run a release after merging changes to `platform/infra/foundation/`,
`platform/infra/components/`, `platform/schemas/conventions/`, `Pulumi.staging.yaml`, or
a new or changed `platform/catalog/<app-id>/app.yaml` (its availability test). An app
release needs no foundation run.

1. Change through a pull request; `foundation-ci` builds and tests without Azure. Running
   `preview` on the branch and pasting the diff into the pull request helps the review.
2. After the merge:

   ```bash
   git switch main && git pull --ff-only && git status --porcelain   # must print nothing
   platform/scripts/bootstrap/foundation.sh platform/scripts/bootstrap/staging.env preview
   platform/scripts/bootstrap/foundation.sh platform/scripts/bootstrap/staging.env up
   ```

3. Keep the summary that `up` prints (Git SHA, versions, outputs, no secret values). A
   Git SHA ending in `-dirty` means the run included uncommitted changes.
4. Run the [access check](bootstrap.md#step-7-access-check) with `break-category=none`.
5. If `baseDomain` or `ingressIp` changed, inform B and update the
   [issue webhook](bootstrap.md#step-8-manual-steps).

Concurrent runs: the lock of the Pulumi backend in the state container stops a second
`preview` or `up` of the same stack with a "currently locked" error, without changing the
state. If a killed run left its lock behind, make sure nobody else is running, then
remove it with `pulumi -C platform/infra/foundation cancel` (verify live).

Rollback: revert the commit through a pull request and run `up` from `main`. When it is
urgent, check out the last good commit (Git SHA from its summary), run `up`, then revert
on `main`. A rollback never deletes or replaces protected resources.

### No mutual changes check

Task 5.2: neither stack may change the other's resources. After an app release (B's
workflow succeeded):

```bash
platform/scripts/bootstrap/foundation.sh platform/scripts/bootstrap/staging.env preview --expect-no-changes
```

Exit code 0 means no changes. A non-zero exit shows the resource in the diff: an app
release changed something the foundation owns; report it to B. Run the check on the
deployed commit: a merged but not yet deployed catalog entry shows up as new availability
tests, so deploy those first. The reverse direction, an app stack preview after a
foundation release, is run by B. Record both results for task 5.2.

## AKS upgrade

Automatic Kubernetes upgrades are off (`upgradeChannel: none`). The setting
`kubernetesVersion` sets the control plane and the system node pool
(`orchestratorVersion`).

1. List the targets:

   ```bash
   az aks get-upgrades --resource-group rg-mmk-platform-staging --name aks-mmk-platform-staging -o table
   az aks get-versions --location <location> -o table
   ```

2. Pick a version offered by `get-upgrades`, one minor version per step (verify live).
3. With the backend environment, set it and commit `Pulumi.staging.yaml` through a pull
   request:

   ```bash
   pulumi -C platform/infra/foundation config set kubernetesVersion <x.y.z>
   ```

4. After the merge, `preview`: expect an update of `aks` only, no replacement. Then `up`.
5. Check `kubectl get nodes` (new version), then run the access check. The
   [ingress check](bootstrap.md#step-6-ingress-check) is optional (it uses a certificate).

With few nodes, apps can be briefly unavailable while AKS replaces nodes; duration and
disruption: verify live. There is no downgrade: the way back from a failed upgrade is a
fix forward or the [deleted cluster](#deleted-cluster) recovery.

## Chart upgrades

Traefik (namespace `platform-ingress`, setting `traefikChartVersion`, default `42.1.3`)
and cert-manager (namespace `platform-certs`, setting `certManagerChartVersion`, default
`v1.21.3`) are Helm releases inside the foundation stack. Upgrade one chart at a time.

1. Read the upgrade notes: [Traefik chart](https://github.com/traefik/traefik-helm-chart),
   [cert-manager](https://cert-manager.io/docs/releases/).
2. With the backend environment, set the version (cert-manager versions keep the leading
   `v`) and commit `Pulumi.staging.yaml` through a pull request:

   ```bash
   pulumi -C platform/infra/foundation config set traefikChartVersion <version>
   pulumi -C platform/infra/foundation config set certManagerChartVersion v<x.y.z>
   ```

3. After the merge, note the current IP
   (`foundation.sh … outputs | jq -r .ingressIp`), run `preview`: expect an update of the
   `traefik` or `cert-manager` release only. A replacement of `traefik` means a new
   load balancer IP: stop.
4. `up`, then compare `ingressIp` with the noted value. A Helm upgrade normally keeps the
   LoadBalancer service and its IP (verify live).
5. Run the ingress check and the access check.

## Stop and start

To save cost, stop the cluster first and the database second; start in reverse order.
Apps, agent and data come back without a foundation run.

```bash
az aks stop --resource-group rg-mmk-platform-staging --name aks-mmk-platform-staging
az postgres flexible-server stop --resource-group rg-mmk-platform-staging --name "$pg_server"
```

```bash
az postgres flexible-server start --resource-group rg-mmk-platform-staging --name "$pg_server"
az aks start --resource-group rg-mmk-platform-staging --name aks-mmk-platform-staging
```

State: `az aks show … --query powerState.code -o tsv` (`Running`, `Stopped`) and
`az postgres flexible-server show … --query state -o tsv` (`Ready`, `Stopped`).

After the start, check that the ingress IP is unchanged. Whether it survives a cluster
stop: verify live.

```bash
kubectl -n platform-ingress get service traefik -o jsonpath='{.status.loadBalancer.ingress[0].ip}'; echo
platform/scripts/bootstrap/foundation.sh platform/scripts/bootstrap/staging.env outputs | jq -r .ingressIp
```

If they differ, run `up` (new `baseDomain`) and continue with steps 4 and 5 of the
[deleted cluster](#deleted-cluster) recovery.

Notes:

- Azure starts a stopped PostgreSQL Flexible Server automatically after 7 days; stop it
  again if staging stays paused longer.
- While stopped, availability tests fail: `ops-humans` receives the `/healthz` alert
  mails and `ops-intake` deliveries fail. This is expected.
- Do not run `preview` or `up` while the cluster is stopped: the Kubernetes provider
  cannot reach the API server and the run fails; Azure changes planned before that point
  may already be applied. A stopped PostgreSQL server may also fail the run (verify
  live). Start both first.
- Stopped resources still cost (disks, public IP, storage; verify live).

## Recovery

All recovery runs locally; GitHub Actions have no Azure identity.

### Deleted cluster

PostgreSQL, diagnostics storage, Log Analytics, ACR and the state live outside the
cluster; their outputs and `contractVersion` stay. Cluster, node resource group, Traefik,
cert-manager, ingress IP and kubeconfig are new.

1. Make sure the cluster is deleted, not stopped:

   ```bash
   az aks show --resource-group rg-mmk-platform-staging --name aks-mmk-platform-staging \
     --query powerState.code -o tsv
   ```

   `ResourceNotFound` means deleted; `Stopped` means [start it](#stop-and-start) instead.
2. With the backend environment, remove the dead cluster and its Kubernetes objects from
   the state:

   ```bash
   PULUMI_K8S_DELETE_UNREACHABLE=true pulumi -C platform/infra/foundation refresh
   ```

   Review before confirming: expected are removals of `aks`, the Container Insights
   association and the Kubernetes objects (namespaces, `traefik`, `cert-manager`,
   `letsencrypt`, `traefik-service`), nothing else. Without the variable the refresh
   fails on the unreachable API server (verify live).
3. Recreate:

   ```bash
   platform/scripts/bootstrap/foundation.sh platform/scripts/bootstrap/staging.env up
   ```

   The new Traefik service gets a new IP, so `ingressIp`, `baseDomain`,
   `operationsApiHost` and `kubeconfig` change; availability tests and the `ops-intake`
   webhook follow in the same run.
4. Run the [ingress check](bootstrap.md#step-6-ingress-check) and the
   [access check](bootstrap.md#step-7-access-check).
5. Consumers follow through the contract: B releases every app again, the operations
   agent included (the app stacks still hold objects of the old cluster; B's procedure).
   Workflows get the new values on their next run of the composite action. Update the
   [issue webhook](bootstrap.md#step-8-manual-steps) URL. Every host requests a new
   certificate from the shared `sslip.io` rate limit; if they are refused, use the
   [HTTP fallback](#http-fallback).

Rehearsal by a second person (task 5.3): agree on it with the team first, because it
deletes the cluster
(`az aks delete --resource-group rg-mmk-platform-staging --name aks-mmk-platform-staging`).
Setting `acmeServer` to `https://acme-staging-v02.api.letsencrypt.org/directory` for the
rehearsal spares the production rate limit; the ingress check then fails on HTTPS by
design (untrusted certificate). Set it back afterwards.

### Lost or overwritten state

The state lives in container `pulumi-state` of `STATE_STORAGE_ACCOUNT` with blob
versioning and 30-day blob and container soft delete.

1. Stop all writers: no local foundation runs and no app release workflows (B) until the
   restore is finished.
2. With the backend environment (the key comes from `AZURE_STORAGE_KEY`), list the
   versions of the foundation state blob. The path below follows Pulumi's project layout
   (verify live; list the `.pulumi/` prefix if it is not found):

   ```bash
   az storage blob list --account-name "$STATE_STORAGE_ACCOUNT" --container-name pulumi-state \
     --auth-mode key --prefix .pulumi/stacks/foundation/ --include v \
     --query "[].{name:name, version:versionId, current:isCurrentVersion, modified:properties.lastModified}" -o table
   ```

3. Download the last good version and check it:

   ```bash
   az storage blob download --account-name "$STATE_STORAGE_ACCOUNT" --container-name pulumi-state \
     --auth-mode key --name .pulumi/stacks/foundation/staging.json --version-id <versionId> --file staging.json
   jq '.checkpoint.latest.resources | length' staging.json
   ```

4. Upload it as the current version; the bad version stays in the version history:

   ```bash
   az storage blob upload --account-name "$STATE_STORAGE_ACCOUNT" --container-name pulumi-state \
     --auth-mode key --name .pulumi/stacks/foundation/staging.json --file staging.json --overwrite
   rm staging.json
   ```

   A deleted blob is restored the same way from its previous version (verify live). A
   deleted container:
   `az storage container list --account-name "$STATE_STORAGE_ACCOUNT" --auth-mode key --include-deleted -o table`,
   then
   `az storage container restore --account-name "$STATE_STORAGE_ACCOUNT" --auth-mode key --name pulumi-state --deleted-version <version>`.
5. Reconcile: `pulumi -C platform/infra/foundation refresh` (review the drift), then
   `foundation.sh … preview`, which should show little or nothing. Run the access check.

Limits of a restored version:

- Resources created after that version exist in Azure but not in the state. The next
  `up` fails on "already exists"; bring them in with `pulumi import` first.
- The generated values (global name suffix, passwords, webhook keys) must be in the
  restored version. Otherwise new values are generated: a new suffix would rename
  protected resources (the run stops), new passwords change logins.

### Damaged database

Azure keeps 7 days of PostgreSQL backups; a point-in-time restore creates a **new**
server. Strand C owns schema and migrations: coordinate every step with C.

1. Stop the writers: scale the agent API and worker to zero (B's release of D's chart)
   and hold app deployments that need the operations lock.
2. Restore to a new server:

   ```bash
   az postgres flexible-server restore --resource-group rg-mmk-platform-staging \
     --name <new-server-name> --source-server "$pg_server" --restore-time <UTC time before the damage>
   ```

   The restored server keeps the admin login and password of the source at that time;
   whether firewall rule and `require_secure_transport` are copied: verify live
   (`az postgres flexible-server firewall-rule list`,
   `az postgres flexible-server parameter show --name require_secure_transport`).
3. Choose with C:
   - **Copy back (default, outputs unchanged).** Needs `pg_dump` and `pg_restore`
     locally and the backend environment:

     ```bash
     export PGSSLMODE=require
     export PGPASSWORD="$(pulumi -C platform/infra/foundation stack output postgresAdminPassword --show-secrets)"
     pg_dump -Fc -h <new-server-name>.postgres.database.azure.com -U opsadmin -d operations -f operations.dump
     pg_restore --clean --if-exists --no-owner -h "$pg_server.postgres.database.azure.com" \
       -U opsadmin -d operations operations.dump
     unset PGPASSWORD; rm operations.dump
     ```

   - **Switch to the restored server.** Changes the contract value `postgresHost` (same
     `contractVersion`; consumers pick it up on their next release). Not prepared: the
     server name is derived from the global name suffix in
     [`data.ts`](../../infra/foundation/data.ts), so adopting the restored server needs
     a code change plus `pulumi state unprotect` and `pulumi import`. Plan it as a
     foundation change with B and C.
4. Start the agent again, run the access check (`postgres`), then delete the temporary
   server (`az postgres flexible-server delete`) once C confirms the data.

If the server itself was deleted: `pulumi refresh` drops it from the state (verify live)
and `up` creates an empty server with the same name and password, both kept in the
state. How to restore a deleted server's backup: verify live in the Azure documentation.

## HTTP fallback

Use it when Let's Encrypt refuses certificates. `sslip.io` is not on the Public Suffix
List, so all of `sslip.io` shares one rate limit.

With `httpFallback: true`: output `publicScheme` becomes `http`; availability tests use
`http://` without TLS checks; the `ops-intake` webhook uses `http://`, so
`alertWebhookKey` travels unencrypted. Traefik never redirects HTTP, so every host stays
reachable over HTTP.

1. With the backend environment:

   ```bash
   pulumi -C platform/infra/foundation config set httpFallback true
   ```

2. Release it like any change (pull request, merge, `preview`, `up`). Expect updates of
   the availability tests, `ops-intake` and the output `publicScheme`.
3. Tell B (consumers build URLs from `publicScheme`) and change the issue webhook URL to
   `http://`.

Back to HTTPS: set `httpFallback false`, release, run the ingress check, change the
webhook URL back. The ingress check always tests HTTPS, so it fails while certificates
are still refused.

## Rotating keys

| Credential | Rotate | Then |
| --- | --- | --- |
| State storage key (`PULUMI_BACKEND_KEY`) | `az storage account keys renew --account-name "$STATE_STORAGE_ACCOUNT" --resource-group rg-mmk-platform-state --key primary` | Re-run `bootstrap.sh` ([repeating](bootstrap.md#repeating-the-bootstrap)); it re-syncs the secret. Workflows that read the state fail in between; `foundation.sh` reads the key fresh on every run |
| GitHub App private key (`githubAppPrivateKey`) | New key in the app settings under *Private keys* ([GitHub App](bootstrap.md#github-app)) | Export `GITHUB_APP_PRIVATE_KEY_FILE`, `GITHUB_WEBHOOK_SECRET` and the model keys and run `foundation.sh … secrets` (it requires all of them). Commit `Pulumi.staging.yaml`, run `up`, B releases the agent again. Then delete the old key in the app settings and the downloaded `.pem` file ([GitHub App key](bootstrap.md#github-app-key)) |
| Optional PAT (`opsGithubToken`, `OPS_GITHUB_TOKEN`) | New fine-grained token, stored in the password manager with its expiry | Set it with the backend environment: `printf '%s' "$OPS_GITHUB_TOKEN" \| pulumi -C platform/infra/foundation config set --secret opsGithubToken`, or export it for `foundation.sh … secrets`. Commit `Pulumi.staging.yaml`, run `up`, re-run `bootstrap.sh` with `OPS_GITHUB_TOKEN` exported (repository secret), B releases the agent again. Revoke the old token |
| ACR admin password | `az acr credential renew --name "$acr_name" --password-name password` | `up` (output `acrPassword` uses the first password), then B releases the apps again (image pull secrets). Workflows get it through the composite action |
| Diagnostics storage key | `az storage account keys renew --account-name "$diag_account" --resource-group rg-mmk-platform-staging --key primary` | `up` (output uses key 1), then B releases the agent again |
| Model keys, webhook secret | New value from the provider or `openssl rand -hex 32` | `foundation.sh … secrets` as for the app key (it also needs `GITHUB_APP_PRIVATE_KEY_FILE`; without the `.pem` file, set only the changed value with the backend environment as for the PAT, with `--path` for `modelApiKeys["…"]`), commit, `up`, agent release; a new webhook secret also goes into the issue webhook |

The generated PostgreSQL password, `alertWebhookKey` and `maintenanceApiKey` have no
prepared rotation.

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| `bash >= 4.4 is required` | macOS bash 3.2 comes first in `PATH` | `brew install bash`; Homebrew's `bin` before `/bin` |
| `Required tool '<tool>' is not installed` | Tool missing | [Tools](bootstrap.md#tools) |
| `<NAME> is empty in <file>` or `Bootstrap config … not found` | Env file incomplete or wrong path | [Step 1](bootstrap.md#step-1-record-the-environment-values); run from the repository root |
| `PULUMI_CONFIG_PASSPHRASE is not set` | Not exported | `read -rs PULUMI_CONFIG_PASSPHRASE && export PULUMI_CONFIG_PASSPHRASE` |
| Pulumi reports an incorrect passphrase | Wrong passphrase | Password manager; [lost passphrase](bootstrap.md#lost-passphrase) |
| Preflight `MISSING …` | Right or GitHub App permission missing | Fix the named item; nothing was created |
| Stack is "currently locked" | Another run is active, or a killed run left its lock | Wait; otherwise see [foundation release](#foundation-release) |
| `Missing required configuration variable` | Required setting missing | [Step 4](bootstrap.md#step-4-configure-the-foundation-stack) |
| `budgetStartDate must be the first day of a month` | Wrong format | `YYYY-MM-01` |
| `modelApiKeys needs at least one provider key` | No model key stored | `foundation.sh … secrets` |
| `Invalid catalog entries:` | A `platform/catalog/<app-id>/app.yaml` has a bad `appId` or `availability.path` | Fix with B; the run stops so no app silently loses its monitoring |
| Pulumi refuses because a resource is protected | The change would delete or replace a [protected resource](#protected-resources) | Revert the setting |
| `The ingress controller service has no public IP yet` | Load balancer IP not assigned yet | Run `up` again |
| Run fails at the first Kubernetes resource | Cluster stopped or deleted | [Stop and start](#stop-and-start) or [deleted cluster](#deleted-cluster) |
| Ingress check: `HTTPS failed but http://… answers` | Certificate not issued (rate limit, HTTP-01) | `kubectl -n platform-certs logs deploy/cert-manager`; [HTTP fallback](#http-fallback) |
| Ingress check: `Neither https nor http://… answers` | Traefik or load balancer | `kubectl -n platform-ingress get pods,svc`, `kubectl -n platform-ingress logs deploy/traefik` |
| Access check: `foundation-state` fails, the rest `skipped` | Repository secrets outdated (rotated key, passphrase), stack missing, or contract version mismatch | Read the step log; re-run `bootstrap.sh` |
| Access check: `kubernetes`, `acr`, `postgres` or `diagnostics-storage` fails | Resource stopped, or a key rotated without `up` | Start it; run `up` |
| Access check: `github` fails | App key deleted in the app settings or never stored, app not installed on a repository, or wrong `GITHUB_APP_ID`/`GITHUB_APP_INSTALLATION_ID` | [Rotating keys](#rotating-keys) |
| Access check: `github-workflow-token` fails | PAT expired, revoked or missing a repository | [Rotating keys](#rotating-keys) |
| `Foundation contract version mismatch` in a consumer workflow | Consumer written for another `contractVersion` | Update the consumer ([handover](handover.md)) |
