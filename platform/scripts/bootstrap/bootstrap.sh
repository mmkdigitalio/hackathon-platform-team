#!/usr/bin/env bash
# Idempotent bootstrap of the Pulumi state backend and the repository secrets. Each step
# only creates what is missing, so a repeated or interrupted run completes without manual
# clean-up and never replaces state data or the Pulumi passphrase. Every existence check
# fails the run when the lookup itself fails, instead of treating the object as missing.
# Needs Contributor only: no role assignments, no locks, no Entra objects.
#
#   OPS_GITHUB_TOKEN=... [PULUMI_CONFIG_PASSPHRASE=...] \
#       platform/scripts/bootstrap/bootstrap.sh platform/scripts/bootstrap/staging.env
#
# PULUMI_CONFIG_PASSPHRASE is only needed on the very first run (when the repository
# secret does not exist yet). Store it in the team password manager first.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source-path=SCRIPTDIR source=lib.sh
source "$here/lib.sh"
config_file="${1:-}"
load_bootstrap_env "$config_file"
require_tools az gh jq curl

log "Preflight"
"$here/preflight.sh" "$config_file"

# Decide about the passphrase before anything is created. A failed listing aborts the
# run (inherit_errexit); it is never read as "secret missing".
repo_secrets="$(gh secret list --repo "$GITHUB_REPO" --json name --jq '.[].name')"
if grep -qx PULUMI_CONFIG_PASSPHRASE <<<"$repo_secrets"; then
    passphrase_action=keep
elif [[ -n "${PULUMI_CONFIG_PASSPHRASE:-}" ]]; then
    passphrase_action=create
else
    die "PULUMI_CONFIG_PASSPHRASE is missing. Generate one (openssl rand -base64 32), store it in the team password manager, then re-run with PULUMI_CONFIG_PASSPHRASE set."
fi

az account set --subscription "$SUBSCRIPTION_ID"
bootstrap_tags=(app-id=mmk-platform environment=shared stack=bootstrap managed-by=bootstrap-script)

log "Resource providers"
for namespace in Microsoft.ContainerService Microsoft.ContainerRegistry Microsoft.OperationalInsights \
    Microsoft.Insights Microsoft.AlertsManagement Microsoft.DBforPostgreSQL Microsoft.Storage \
    Microsoft.Network Microsoft.Consumption; do
    if [[ "$(az provider show --namespace "$namespace" --query registrationState -o tsv)" != "Registered" ]]; then
        az provider register --namespace "$namespace" --wait >/dev/null
        log "  registered $namespace"
    fi
done

log "State resource group $STATE_RESOURCE_GROUP"
if [[ "$(az group exists --name "$STATE_RESOURCE_GROUP")" != "true" ]]; then
    az group create --name "$STATE_RESOURCE_GROUP" --location "$LOCATION" --tags "${bootstrap_tags[@]}" >/dev/null
    log "  created"
fi

log "State storage account $STATE_STORAGE_ACCOUNT"
account_count="$(az storage account list --resource-group "$STATE_RESOURCE_GROUP" \
    --query "[?name=='$STATE_STORAGE_ACCOUNT'] | length(@)" -o tsv)"
if [[ "$account_count" == "0" ]]; then
    # Workflows read the state with the account key, so shared key access stays enabled.
    az storage account create --name "$STATE_STORAGE_ACCOUNT" --resource-group "$STATE_RESOURCE_GROUP" \
        --location "$LOCATION" --sku Standard_LRS --kind StorageV2 --min-tls-version TLS1_2 \
        --allow-blob-public-access false --allow-shared-key-access true --tags "${bootstrap_tags[@]}" >/dev/null
    log "  created"
fi
# Versioning and soft delete (30 days) are re-applied on every run; this never touches blobs.
az storage account blob-service-properties update --account-name "$STATE_STORAGE_ACCOUNT" \
    --resource-group "$STATE_RESOURCE_GROUP" --enable-versioning true \
    --enable-delete-retention true --delete-retention-days 30 \
    --enable-container-delete-retention true --container-delete-retention-days 30 >/dev/null

state_key="$(state_storage_key)"
[[ -n "$state_key" ]] || die "Azure returned no key for $STATE_STORAGE_ACCOUNT."
log "State container $STATE_CONTAINER"
# The key goes through the environment, not the command line; create is idempotent.
AZURE_STORAGE_KEY="$state_key" az storage container create --name "$STATE_CONTAINER" \
    --account-name "$STATE_STORAGE_ACCOUNT" --public-access off --only-show-errors >/dev/null

log "Repository secrets in $GITHUB_REPO"
# Values go through stdin without a trailing newline (printf is a builtin, so they never
# appear in a process list). Account, key and PAT are re-synchronized on every run
# (repairs a rotated key or token); the passphrase is never replaced.
set_secret() { printf '%s' "$2" | gh secret set "$1" --repo "$GITHUB_REPO" >/dev/null; }
set_secret PULUMI_BACKEND_ACCOUNT "$STATE_STORAGE_ACCOUNT"
set_secret PULUMI_BACKEND_KEY "$state_key"
unset state_key
set_secret OPS_GITHUB_TOKEN "$OPS_GITHUB_TOKEN"
if [[ "$passphrase_action" == create ]]; then
    set_secret PULUMI_CONFIG_PASSPHRASE "$PULUMI_CONFIG_PASSPHRASE"
    log "  PULUMI_CONFIG_PASSPHRASE created from the environment"
else
    log "  PULUMI_CONFIG_PASSPHRASE exists; value left unchanged"
fi

cat <<SUMMARY

Bootstrap complete for $ENVIRONMENT (no secret values shown).
  Pulumi backend:     azblob://$STATE_CONTAINER (account $STATE_STORAGE_ACCOUNT, resource group $STATE_RESOURCE_GROUP)
  Repository secrets: PULUMI_BACKEND_ACCOUNT, PULUMI_BACKEND_KEY, PULUMI_CONFIG_PASSPHRASE, OPS_GITHUB_TOKEN in $GITHUB_REPO

Next (operations/foundation/bootstrap.md, steps 3 to 5):
  platform/scripts/bootstrap/foundation.sh $config_file secrets
  platform/scripts/bootstrap/foundation.sh $config_file preview
  platform/scripts/bootstrap/foundation.sh $config_file up
SUMMARY
