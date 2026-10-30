# Platform bootstrap

How a person with Contributor on the Azure subscription initializes a platform
environment (first: `staging`). Everything runs locally under `az login`. GitHub Actions
have no Azure identity; they only read the state afterwards with the storage key.
Neither the bootstrap nor a foundation run needs a running operations agent.

| Created by | What |
| --- | --- |
| [`bootstrap.sh`](../../scripts/bootstrap/bootstrap.sh) | Resource provider registrations, state resource group `rg-mmk-platform-state`, state storage account (blob versioning, 30-day blob and container soft delete), container `pulumi-state`, repository secrets `PULUMI_BACKEND_ACCOUNT`, `PULUMI_BACKEND_KEY`, `PULUMI_CONFIG_PASSPHRASE` and, only when a PAT is exported, `OPS_GITHUB_TOKEN` |
| Foundation stack `organization/foundation/staging`, deployed with [`foundation.sh`](../../scripts/bootstrap/foundation.sh) | Resource group `rg-mmk-platform-staging`, Log Analytics, Application Insights, ACR with admin user, AKS `aks-mmk-platform-staging` (node resource group `rg-mmk-platform-staging-aks-nodes`), Container Insights, Traefik, cert-manager with ClusterIssuer `letsencrypt`, PostgreSQL Flexible Server with database `operations`, diagnostics storage, action groups `ops-intake` and `ops-humans`, budget, availability tests, generated keys |
| A person (this page) | Env file, passphrase, GitHub App private key, stack settings, model keys, webhook secret, GitHub issue webhook, test notifications |

No step creates role assignments, Entra objects or management locks: Contributor cannot
create them, and the platform does not need them. Consumers get every value through the
output contract ([handover](handover.md)).

## Prerequisites

### Tools

| Tool | Version / note |
| --- | --- |
| `bash` | 4.4 or newer; every script stops otherwise. macOS ships 3.2: `brew install bash` and put Homebrew's `bin` before `/bin` in `PATH` (the scripts use `#!/usr/bin/env bash`). |
| `az`, `gh`, `jq`, `curl`, `git`, `openssl` | Current versions |
| Pulumi CLI | 3.273.0 (same version as the composite action `foundation-outputs`) |
| Node.js and npm | Node 24 |
| `kubectl` | For the ingress check and local cluster access |

Install the program's dependencies once per checkout; `foundation.sh` compiles the
program with `npm run build` before every command:

```bash
cd <repository root>
(cd platform && npm ci)
```

### Rights

| Right | Needed for | Checked by the preflight |
| --- | --- | --- |
| Contributor (or higher) on the subscription | All Azure resources | yes |
| Admin on `mmkdigitalio/hackathon-platform-team` | Repository secrets, issue webhook | yes |
| GitHub App of the agent and its private key, see below | Agent on GitHub, access check | yes, per repository, when the key file is given |
| Team password manager | Passphrase, webhook secret, model keys, optional PAT | no |
| Mailbox behind `humansEmail` | Confirming test notifications | no |

At least two team members should have Contributor and access to the passphrase, so a
recovery does not depend on one person.

### GitHub App

The agent accesses GitHub through the GitHub App `hackathonplatformagent` (App ID
`5542094`, installation `176901698` on `mmkdigitalio/hackathon-platform-team` and
`mmkdigitalio/hackathon-platform-affine`; `GITHUB_APP_ID` and
`GITHUB_APP_INSTALLATION_ID` in the env file). Changed 2026-10-30 (Erik); was:
fine-grained PAT of a team member
([D's handover](../agent/uebergaben.md#github-app-entscheidung-vom-30102026)).

- The installation needs the repository permissions Issues, Pull requests, Contents and
  Actions, each *Read and write*. The preflight checks them.
- The private key (PEM) is generated in the app settings under *Private keys*; GitHub
  keeps only the public part. Pass the path of the downloaded `.pem` file as
  `GITHUB_APP_PRIVATE_KEY_FILE` and delete the file once the key is stored in the stack
  ([GitHub App key](#github-app-key)).
- A fine-grained PAT is optional. If exported as `OPS_GITHUB_TOKEN`, it becomes the
  repository secret `OPS_GITHUB_TOKEN` and the output `opsGithubToken` (fallback). B's
  `app-update-desired-state.yml` still pushes and opens pull requests with that
  repository secret until it uses an app installation token (open item for B). Store a
  PAT in the password manager with its expiry date.

## Step 1: Record the environment values

```bash
cp platform/scripts/bootstrap/staging.env.example platform/scripts/bootstrap/staging.env
```

Fill in `platform/scripts/bootstrap/staging.env` (it contains no secrets):

- `SUBSCRIPTION_ID`
- `STATE_STORAGE_ACCOUNT`: globally unique, 3 to 24 lowercase letters and digits, for
  example `stmmkstate<random>`
- optional `NODE_VM_SIZE`: only used by the preflight to check availability
- `LOCATION`, `ENVIRONMENT`, `GITHUB_REPO`, `AGENT_REPOS`, `GITHUB_APP_ID` and
  `GITHUB_APP_INSTALLATION_ID` are prefilled

Commit the file, so every later run uses the same values. State resource group and
container names come from
[`conventions.json`](../../schemas/conventions/conventions.json) (`stateBackend`).

Only before the very first bootstrap (the repository secret `PULUMI_CONFIG_PASSPHRASE`
does not exist yet), generate the passphrase:

```bash
openssl rand -base64 32
```

Store it in the team password manager **before** continuing (for example entry
`mmk-platform / Pulumi passphrase`). All stacks share it; without it no stack secret can
be decrypted ([Lost passphrase](#lost-passphrase)).

Log in and export the secrets without echo, so they appear neither on screen nor in the
shell history. `GITHUB_APP_PRIVATE_KEY_FILE` is the path to the app's `.pem` file, not
the key itself:

```bash
az login
gh auth login
read -rs PULUMI_CONFIG_PASSPHRASE && export PULUMI_CONFIG_PASSPHRASE
export GITHUB_APP_PRIVATE_KEY_FILE=<path to the downloaded .pem file>
# optional PAT, see GitHub App above:
# read -rs OPS_GITHUB_TOKEN && export OPS_GITHUB_TOKEN
```

All commands on this page run from the repository root in this shell.

## Step 2: Preflight

```bash
platform/scripts/bootstrap/preflight.sh platform/scripts/bootstrap/staging.env
```

Read-only. Every line is `OK`, `MISSING` or `INFO`. On any gap it lists all missing
items, exits non-zero and has created nothing. It checks:

- Azure login, access to `SUBSCRIPTION_ID`, and Contributor (the `*` action on the
  subscription).
- `gh` login and admin on `GITHUB_REPO`.
- The GitHub App, if `GITHUB_APP_PRIVATE_KEY_FILE` is set: it signs an app JWT (RS256,
  `openssl`) with `GITHUB_APP_ID` and calls `GET /repos/{repo}/installation` for each
  repository in `AGENT_REPOS`. The installation must exist, have the ID
  `GITHUB_APP_INSTALLATION_ID` and grant `issues`, `pull_requests`, `contents` and
  `actions` with `write`; each gap is one line, for
  example `GitHub App permission actions (write) on <repo> (granted: read)`. Without the
  key file these checks are skipped with an `INFO` line.
- `INFO` with the supported non-preview AKS versions in `LOCATION` (you pick
  `kubernetesVersion` from it in step 4) and, if `NODE_VM_SIZE` is set, whether that size
  exists in the region without restrictions.

## Step 3: Bootstrap the state backend and repository secrets

```bash
platform/scripts/bootstrap/bootstrap.sh platform/scripts/bootstrap/staging.env
```

What it does, in this order:

1. Runs the preflight and stops on any gap.
2. Decides about the passphrase before anything is created: keep the existing
   repository secret; otherwise create it from `PULUMI_CONFIG_PASSPHRASE`; otherwise stop.
3. Registers missing resource providers (`Microsoft.ContainerService`,
   `ContainerRegistry`, `OperationalInsights`, `Insights`, `AlertsManagement`,
   `DBforPostgreSQL`, `Storage`, `Network`, `Consumption`) and waits for each.
4. Creates the resource group `rg-mmk-platform-state` and the storage account
   `STATE_STORAGE_ACCOUNT` if missing (Standard_LRS, TLS 1.2, no public blob access,
   shared key access on because workflows read the state with the key).
5. Re-applies blob versioning and 30-day blob and container soft delete.
6. Creates the private container `pulumi-state` (idempotent).
7. Sets repository secrets through stdin: `PULUMI_BACKEND_ACCOUNT` and
   `PULUMI_BACKEND_KEY` (key 1 of the state account) on every run; `OPS_GITHUB_TOKEN`
   only when `OPS_GITHUB_TOKEN` is exported, otherwise it skips it (the bootstrap needs no
   PAT); `PULUMI_CONFIG_PASSPHRASE` only if it does not exist yet.

The final output names backend and secrets (no values) and the next commands.

Each step only creates what is missing, and a failed lookup stops the run instead of
being read as "missing". An interrupted run is completed by running the same command
again, without clean-up. [`test/bootstrap.test.mjs`](../../test/bootstrap.test.mjs)
covers the repeated and the interrupted run against stubs. Provider registration can
take several minutes (verify live).

## Step 4: Configure the foundation stack

Region, AKS version, node size and count, recipients and budget have no defaults; they
are chosen for the actual subscription and recorded in
[`Pulumi.staging.yaml`](../../infra/foundation/Pulumi.staging.yaml). Node size, node
count and budget cost money: agree them with the budget owner before setting them.

### Backend environment

Direct `pulumi` commands need the environment that `foundation.sh` sets up. In the shell
from step 1, from the repository root:

```bash
source platform/scripts/bootstrap/staging.env
az account set --subscription "$SUBSCRIPTION_ID"
export ARM_SUBSCRIPTION_ID="$SUBSCRIPTION_ID"
export PULUMI_BACKEND_URL=azblob://pulumi-state
export AZURE_STORAGE_ACCOUNT="$STATE_STORAGE_ACCOUNT"
export AZURE_STORAGE_KEY="$(az storage account keys list --account-name "$STATE_STORAGE_ACCOUNT" \
  --resource-group rg-mmk-platform-state --query '[0].value' -o tsv)"
pulumi -C platform/infra/foundation stack select --create organization/foundation/staging \
  --secrets-provider passphrase
```

`PULUMI_CONFIG_PASSPHRASE` must be exported (step 1). The first `stack select --create`
creates the stack in the backend and writes `encryptionsalt` into `Pulumi.staging.yaml`.
The shell now holds the state key; close it when you are done.

### Required settings

```bash
pulumi -C platform/infra/foundation config set location <region>            # normally LOCATION from the env file
pulumi -C platform/infra/foundation config set kubernetesVersion <x.y.z>    # from the preflight INFO line
pulumi -C platform/infra/foundation config set nodeVmSize <vm-size>         # as checked by the preflight
pulumi -C platform/infra/foundation config set nodeCount <n>
pulumi -C platform/infra/foundation config set humansEmail "<a@x>, <b@x>"   # comma-separated; ops-humans, budget, Let's Encrypt (first address)
pulumi -C platform/infra/foundation config set budgetAmount <amount>        # monthly, in the billing currency (verify live)
pulumi -C platform/infra/foundation config set budgetStartDate <YYYY-MM-01>
```

The program rejects a `budgetStartDate` that is not the first day of a month. Use the
current month; Azure may refuse a start date in the past (verify live).

### Optional settings

Defaults from [`config.ts`](../../infra/foundation/config.ts):

| Key | Default | Purpose |
| --- | --- | --- |
| `acmeEmail` | first address in `humansEmail` | Let's Encrypt account e-mail |
| `acmeServer` | `https://acme-v02.api.letsencrypt.org/directory` | ACME endpoint (the staging endpoint for rehearsals) |
| `httpFallback` | `false` | [HTTP fallback](operations.md#http-fallback) |
| `containerInsights` | `true` | Container Insights addon and data collection rule |
| `postgresVersion` | `16` | PostgreSQL major version |
| `postgresSkuName`, `postgresSkuTier` | `Standard_B1ms`, `Burstable` | PostgreSQL size |
| `postgresStorageGb` | `32` | PostgreSQL storage |
| `traefikChartVersion` | `42.1.3` | [Chart upgrades](operations.md#chart-upgrades) |
| `certManagerChartVersion` | `v1.21.3` | [Chart upgrades](operations.md#chart-upgrades) |
| `catalogDir` | `platform/catalog` | Source of the app availability tests |

### GitHub App key

The foundation itself needs no GitHub access; it passes the app's private key to the
agent as the secret output `githubAppPrivateKey`. To store the key, or to rotate it after
generating a new one in the app settings:

```bash
GITHUB_APP_PRIVATE_KEY_FILE=<path to the .pem file> \
  platform/scripts/bootstrap/foundation.sh platform/scripts/bootstrap/staging.env secrets
platform/scripts/bootstrap/foundation.sh platform/scripts/bootstrap/staging.env up
```

To store or rotate only the key, without the other values, use `app-key` instead of
`secrets`:

```bash
GITHUB_APP_PRIVATE_KEY_FILE=<path to the .pem file> \
  platform/scripts/bootstrap/foundation.sh platform/scripts/bootstrap/staging.env app-key
```

`secrets` needs the other values as well ([stack secrets](#stack-secrets)); commit
`Pulumi.staging.yaml` through a pull request before `up`
([step 5](#step-5-preview-and-deploy)). After `up`, delete the downloaded `.pem` file.
On a rotation, B releases the agent again; only then delete the old key in the app
settings.

### Stack secrets

Generate the GitHub webhook secret (for example `openssl rand -hex 32`) and store it in
the password manager. Then, with `GITHUB_APP_PRIVATE_KEY_FILE` still exported from
step 1:

```bash
read -rs GITHUB_WEBHOOK_SECRET && export GITHUB_WEBHOOK_SECRET
read -rs MODEL_KEY_AZURE_OPENAI && export MODEL_KEY_AZURE_OPENAI   # and/or MODEL_KEY_ANTHROPIC
platform/scripts/bootstrap/foundation.sh platform/scripts/bootstrap/staging.env secrets
```

`secrets` needs `GITHUB_WEBHOOK_SECRET`, `GITHUB_APP_PRIVATE_KEY_FILE` and at least one
of `MODEL_KEY_ANTHROPIC` and `MODEL_KEY_AZURE_OPENAI`. It stores them through stdin as
encrypted settings `githubWebhookSecret`, `githubAppPrivateKey` (the content of the key
file, which `openssl` must read as an unencrypted private key), `modelApiKeys["anthropic"]` and `modelApiKeys["azure-openai"]`. `OPS_GITHUB_TOKEN`
is optional: only if it is exported, it is stored as `opsGithubToken`; the output
`opsGithubToken` is empty when no PAT is stored. Only exported model keys are written; a
key stored earlier for the other provider stays.

Commit `platform/infra/foundation/Pulumi.staging.yaml` through a pull request. It holds
the settings, the `encryptionsalt` and the secrets as ciphertext only.

## Step 5: Preview and deploy

Run from an up-to-date `main` with a clean working tree (`git status --porcelain` prints
nothing). The summary records the Git SHA and appends `-dirty` when tracked files have
uncommitted changes; such a run cannot be reproduced from Git.

```bash
platform/scripts/bootstrap/foundation.sh platform/scripts/bootstrap/staging.env preview
platform/scripts/bootstrap/foundation.sh platform/scripts/bootstrap/staging.env up
```

- `preview` runs `pulumi preview --diff`. The first preview shows only creations.
- `up` runs `pulumi up`, which shows the plan again and asks for confirmation. Afterwards
  [`summary.mjs`](../../scripts/bootstrap/summary.mjs) prints stack, Git SHA,
  `contractVersion`, the component versions from the output `platformVersions`
  (Kubernetes, Traefik chart, cert-manager chart, PostgreSQL), the URL patterns `<publicScheme>://<app-id>.<baseDomain>` and
  `<publicScheme>://<operationsApiHost>/healthz`, the resource group and every output;
  secret outputs show as `[secret]`. Keep this summary as evidence.
- The first run creates AKS and PostgreSQL and takes a while (duration: verify live).
- If `up` stops with `The ingress controller service has no public IP yet`, run `up`
  again.
- A second run of the same stack at the same time stops on the backend lock without
  changing the state.

Expected after the first run:

- The `/healthz` availability test of `operations-agent` fails until B deploys the agent,
  so `ops-humans` receives alert mails ([monitoring](monitoring.md)).
- App availability tests exist only for entries in `platform/catalog/<app-id>/app.yaml`
  that were merged before the run (the directory does not exist yet: no app tests). A new
  catalog entry needs another foundation run.
- Container Insights (task 3.2): check with a log query that container logs arrive
  ([monitoring](monitoring.md)); whether it works without a role assignment: verify live.

Read the outputs later without secret values:
`platform/scripts/bootstrap/foundation.sh platform/scripts/bootstrap/staging.env outputs`.

## Step 6: Ingress check

Proof for task 3.3: HTTPS call of a temporary test ingress under `<test>.<baseDomain>`.

```bash
platform/scripts/bootstrap/foundation.sh platform/scripts/bootstrap/staging.env ingress-check
```

It writes the admin kubeconfig from the stack output to a private temporary file,
deploys namespace `ingress-check` with `traefik/whoami:v1.12.0`, a Service and an Ingress
for `ingress-check.<baseDomain>` (class `traefik`, annotation
`cert-manager.io/cluster-issuer: letsencrypt`), waits up to 180 s for the rollout and up
to 300 s for certificate `ingress-check-tls`, then calls
`https://ingress-check.<baseDomain>/`. On exit it always deletes the namespace and the
temporary kubeconfig.

| Last line | Meaning |
| --- | --- |
| `OK: https://… answers with a valid Let's Encrypt certificate.` | Done |
| `HTTPS failed but http://… answers: certificate problem.` | Traefik works; read the printed `describe certificate`. If Let's Encrypt refuses (rate limit): [HTTP fallback](operations.md#http-fallback) |
| `Neither https nor http://… answers` | Traefik or the Azure load balancer; see [troubleshooting](operations.md#troubleshooting) |

Every run requests a new certificate from the rate limit that all of `sslip.io` shares.
Run it once per bootstrap or recovery, not in a loop.

## Step 7: Access check

[`foundation-access-check.yml`](../../../.github/workflows/foundation-access-check.yml)
exercises every access path from a GitHub-hosted runner with keys only (task 5.1).
GitHub offers `workflow_dispatch` only for workflows on the default branch (verify live),
so it must be merged to `main` first.

Green run:

```bash
gh workflow run foundation-access-check.yml --repo mmkdigitalio/hackathon-platform-team \
  -f environment=staging -f break-category=none
gh run list --repo mmkdigitalio/hackathon-platform-team --workflow foundation-access-check.yml --limit 1
gh run watch <run-id> --repo mmkdigitalio/hackathon-platform-team
```

| Category | Proof |
| --- | --- |
| `foundation-state` | The composite action reads the outputs with storage key and passphrase and accepts contract version 1 |
| `kubernetes` | `kubectl auth can-i '*' '*' --all-namespaces` answers `yes` with the admin kubeconfig |
| `acr` | `docker login` with the ACR admin user |
| `postgres` | `psql` login to `operations` with `sslmode=require`, session encrypted |
| `diagnostics-storage` | Write, read, compare and delete a blob with the storage key |
| `github` | An installation token of the GitHub App, created from the output `githubAppPrivateKey` with `GITHUB_APP_ID` and `GITHUB_APP_INSTALLATION_ID` (workflow env), reads repository and issues of every repository in `AGENT_REPOS` |
| `github-workflow-token` | The same reads with the repository secret `OPS_GITHUB_TOKEN` (optional PAT); only when that secret exists |

The job summary lists each category with result and detail; the job fails if any
category fails. If `foundation-state` fails, every other category is reported as
`skipped: no foundation outputs`. The script uses `kubectl`, `docker`, `psql`, `az`,
`curl`, `openssl` and `jq` from the runner image (verify live that `ubuntu-latest` has them; a missing tool
fails its category with `tool … is not installed`).

Red run, proving that a broken access is detected:

```bash
gh workflow run foundation-access-check.yml --repo mmkdigitalio/hackathon-platform-team \
  -f environment=staging -f break-category=postgres
```

Expected: the run fails, the summary shows only `postgres` as failed and names the
deliberately broken category. Any other category can be chosen (`github` gets a freshly
generated key that belongs to no app, so GitHub rejects the token request); with `foundation-state` all categories fail because the others are skipped. Keep the URLs of both runs as
evidence for task 5.1.

## Step 8: Manual steps

1. **Issue webhook** (repository admin), once C's event intake runs and B has published
   the agent under `operationsApiHost`: repository → Settings → Webhooks → Add webhook.
   - Payload URL: `<publicScheme>://<operationsApiHost>/events/github`, the GitHub
     receiver of C's API (`operations-agent/src/api/app.py`,
     [C's design](../../../openspec/changes/operations-intake-and-state/design.md)).
   - Content type `application/json`, events *Issues* and *Issue comments* (per C's
     design), secret = `githubWebhookSecret` from the password manager, or
     `pulumi -C platform/infra/foundation stack output githubWebhookSecret --show-secrets`
     with the [backend environment](#backend-environment).
   - Update the URL whenever `baseDomain` or `publicScheme` changes.
2. **Test notifications** before the staging acceptance: Azure portal → Monitor →
   Alerts → Action groups → `ops-humans` → *Test*: the `humansEmail` list receives a
   mail and nothing reaches the agent. `ops-intake` once C's intake runs: the request
   with key is accepted, the same request without key is rejected
   ([monitoring](monitoring.md)).
3. **GitHub App key file**: delete the downloaded `.pem` file after `up`
   ([GitHub App key](#github-app-key)). If an optional PAT is used, write its expiry date
   into the password manager entry and the team calendar; replacement in
   [operations](operations.md#rotating-keys).

The bootstrap is complete when steps 5 to 7 succeeded and the summary, the ingress check
result and both access check runs are recorded.

## Repeating the bootstrap

Run `bootstrap.sh` again after rotating the state storage key, after replacing the
optional PAT, after an interrupted run, or to check an environment. It needs
`PULUMI_CONFIG_PASSPHRASE` only if the repository secret is missing. With
`OPS_GITHUB_TOKEN` exported, the run overwrites the repository secret with it; without,
it leaves the secret as it is. Export `GITHUB_APP_PRIVATE_KEY_FILE` if the preflight
should check the GitHub App.

| On every run | Never |
| --- | --- |
| Preflight; registers missing providers; creates missing resource group, account or container; re-applies versioning and soft delete; re-syncs `PULUMI_BACKEND_ACCOUNT`, `PULUMI_BACKEND_KEY` and, if exported, `OPS_GITHUB_TOKEN` | Touches state blobs or the foundation stack; replaces an existing `PULUMI_CONFIG_PASSPHRASE`; generates a passphrase; reads a failed lookup as "missing"; creates role assignments, locks or Entra objects |

## Lost passphrase

The passphrase encrypts the secret settings in `Pulumi.staging.yaml` (model keys,
`githubWebhookSecret`, `githubAppPrivateKey`, optional `opsGithubToken`) and every
secret value in the state of every stack that shares it: the foundation (kubeconfig, ACR password, PostgreSQL admin
password, diagnostics key, `alertWebhookKey`, `maintenanceApiKey`) and B's app stacks.
Azure resources and data are not affected; what is lost is Pulumi's ability to read and
update these stacks.

1. Look for a copy: password manager history, the password managers of other team
   members. GitHub never shows a repository secret again.
2. While the repository secret still holds the old value, workflows keep working. A
   one-off workflow could use it to re-encrypt the stacks with a new passphrase
   (`pulumi stack change-secrets-provider`). Not prepared, not tested.
3. Without any copy, a new passphrase means rebuilding the Pulumi side of every stack.
   Not prepared, not tested; agree on it with the team first:
   - Store the new passphrase and replace the repository secret by hand
     (`gh secret set PULUMI_CONFIG_PASSPHRASE --repo mmkdigitalio/hackathon-platform-team`);
     `bootstrap.sh` never replaces it.
   - Foundation: remove `encryptionsalt` and all `secure:` values from
     `Pulumi.staging.yaml`, drop the old stack record (`pulumi stack rm --force` leaves
     the Azure resources alone; the old state blob stays in blob versioning), create the
     stack again and repeat step 4, then `pulumi import` the existing resources,
     including the global name suffix, before any `up`. Otherwise `up` fails on existing
     names or creates new resources with new names.
   - Generated values come back with new values: the PostgreSQL admin password, which
     also changes the server login (Contributor can reset it independently with
     `az postgres flexible-server update --admin-password`), `alertWebhookKey` and
     `maintenanceApiKey`. Every consumer must release again (B, C, D).
   - B's app stacks need the same treatment.

## Accepted risks

Deliberate simplifications for staging, required by the spec to be named; revisit before
production.

- **Keys instead of roles.** Contributor cannot assign roles, so every access path is a
  key. Whoever has the state storage key (repository secret or Contributor on the state
  account) and the passphrase can read every platform credential from the state. Every
  workflow of the repository can read these repository secrets.
- **Admin credentials in state and outputs.** The admin kubeconfig (cluster-admin, local
  accounts enabled, public API server without IP restriction) and the ACR admin user are
  secret outputs, passed masked to workflows and as Kubernetes Secrets to pods.
- **One PostgreSQL admin login** (`opsadmin`) for migrations, agent runtime and app
  databases.
- **Open PostgreSQL firewall** for 0.0.0.0 to 255.255.255.255; the protection is
  enforced TLS and a generated password.
- **No Key Vault, no rotation, no per-consumer secrets.** Rotation is manual
  ([operations](operations.md#rotating-keys)).
- **One GitHub App key.** Whoever can read the state, the stack config with the
  passphrase or the agent's Kubernetes Secret can act as the app on both repositories
  with Issues, Pull requests, Contents and Actions write. Changed 2026-10-30 (Erik); was:
  fine-grained PAT of one person. An optional PAT in `OPS_GITHUB_TOKEN` still belongs to
  one person; its expiry or revocation breaks the workflows that use it.
- **No management lock.** Contributor lacks `Microsoft.Authorization/locks/write`.
  Protection is Pulumi `protect` (only against Pulumi runs) plus versioning and soft
  delete of the state. Anyone with Contributor can still delete resources directly.
- **`sslip.io` rate limit.** `sslip.io` is not on the Public Suffix List, so every user
  of `sslip.io` shares one Let's Encrypt rate limit; certificates can be refused at any
  time. Under the HTTP fallback all traffic is unencrypted, including the
  `alertWebhookKey` in the `ops-intake` URL.
- **One operator path.** Foundation releases and recovery need a person with local
  Contributor access; at least two people should rehearse them.
- **State storage** is locally redundant (Standard_LRS) and has shared key access
  enabled, because workflows read the state with the key.
