#!/usr/bin/env bash
# Read-only check that the person running the bootstrap has every required right:
# Contributor on the subscription, admin on the repository (repository secrets, issue
# webhook) and a fine-grained PAT with Issues, Contents, Pull requests and Actions on
# every repository in PAT_REPOS. Creates nothing. Exits non-zero and names each gap.
#
#   OPS_GITHUB_TOKEN=... platform/scripts/bootstrap/preflight.sh platform/scripts/bootstrap/staging.env
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source-path=SCRIPTDIR source=lib.sh
source "$here/lib.sh"
load_bootstrap_env "${1:-}"
require_tools az gh jq curl
[[ -n "${OPS_GITHUB_TOKEN:-}" ]] || die "OPS_GITHUB_TOKEN is not set: export the fine-grained PAT of the agent (bootstrap.md, step 1)."

missing=()
pass() { log "OK      $*"; }
fail() { log "MISSING $*"; missing+=("$*"); }
info() { log "INFO    $*"; }

# Azure session, subscription and Contributor (a "*" action; Contributor excludes only
# authorization writes, which the platform never needs).
az account show >/dev/null 2>&1 || die "Not logged in to Azure. Run: az login"
if [[ "$(az account show --subscription "$SUBSCRIPTION_ID" --query id -o tsv 2>/dev/null || true)" == "$SUBSCRIPTION_ID" ]]; then
    pass "Azure subscription $SUBSCRIPTION_ID is accessible"
    actions="$(az rest --method get \
        --url "https://management.azure.com/subscriptions/$SUBSCRIPTION_ID/providers/Microsoft.Authorization/permissions?api-version=2022-04-01" \
        --query 'value[].actions[]' -o tsv 2>/dev/null || true)"
    if grep -qx '\*' <<<"$actions"; then
        pass "Contributor (or higher) on subscription $SUBSCRIPTION_ID"
    else
        fail "Contributor on subscription $SUBSCRIPTION_ID (only these actions are allowed: $(tr '\n' ' ' <<<"${actions:-none}"))"
    fi
else
    fail "Access to subscription $SUBSCRIPTION_ID (az account show --subscription failed)"
fi

# Repository admin: repository secrets and the issue webhook.
if ! gh auth status >/dev/null 2>&1; then
    fail "GitHub CLI login (gh auth login)"
elif [[ "$(gh api "repos/$GITHUB_REPO" --jq .permissions.admin 2>/dev/null || echo false)" == "true" ]]; then
    pass "Admin on $GITHUB_REPO (repository secrets, issue webhook)"
else
    fail "Admin on $GITHUB_REPO (repository secrets, issue webhook)"
fi

# PAT permissions. Reads must return 200. Writes are probed with an invalid body: 422
# (validation failed) proves the permission without creating anything, 403 means it is
# missing. 409 is an empty repository.
probe() {
    local repo="$1" permission="$2" method="$3" path="$4" body="$5" accepted="$6" code
    code="$(github_status "$OPS_GITHUB_TOKEN" "$method" "repos/$repo$path" "$body")"
    if [[ " $accepted " == *" $code "* ]]; then
        pass "PAT $permission on $repo"
    else
        fail "PAT permission $permission on $repo (HTTP $code)"
    fi
}
for repo in $PAT_REPOS; do
    code="$(github_status "$OPS_GITHUB_TOKEN" GET "repos/$repo")"
    if [[ "$code" != "200" ]]; then
        fail "PAT access to repository $repo (HTTP $code)"
        continue
    fi
    probe "$repo" "Issues (read)" GET "/issues?per_page=1" "" "200"
    probe "$repo" "Pull requests (read)" GET "/pulls?per_page=1" "" "200"
    probe "$repo" "Contents (read)" GET "/commits?per_page=1" "" "200 409"
    probe "$repo" "Actions (read)" GET "/actions/runs?per_page=1" "" "200"
    probe "$repo" "Issues (write)" POST "/issues" "{}" "422"
    probe "$repo" "Pull requests (write)" POST "/pulls" "{}" "422"
    probe "$repo" "Contents (write)" POST "/git/refs" "{}" "422"
    # A dispatch to a workflow that does not exist: 404 or 422 with the permission.
    probe "$repo" "Actions (write)" POST "/actions/workflows/0/dispatches" "{}" "404 422"
done

# Region information for choosing the stack configuration.
if versions="$(az aks get-versions --location "$LOCATION" --query 'values[?!isPreview].version' -o tsv 2>/dev/null)"; then
    info "Supported AKS versions in $LOCATION: $(tr '\n' ' ' <<<"$versions")"
else
    fail "AKS availability in region $LOCATION (az aks get-versions failed)"
fi
if [[ -n "${NODE_VM_SIZE:-}" ]]; then
    # --size matches prefixes, and an unknown size has no restrictions either; count exact names first.
    skus="$(az vm list-skus --location "$LOCATION" --size "$NODE_VM_SIZE" --resource-type virtualMachines \
        --query "[?name=='$NODE_VM_SIZE']" -o json 2>/dev/null || echo error)"
    if [[ "$skus" == "error" ]]; then
        restrictions="az vm list-skus failed"
    elif [[ "$(jq length <<<"$skus")" == "0" ]]; then
        restrictions="unknown size in this region"
    else
        restrictions="$(jq -r '[.[].restrictions[]?.reasonCode] | join(", ")' <<<"$skus")"
    fi
    if [[ -z "$restrictions" ]]; then
        pass "VM size $NODE_VM_SIZE is available in $LOCATION"
    else
        fail "VM size $NODE_VM_SIZE in $LOCATION (restriction: $restrictions)"
    fi
fi
info "MANUAL: issue webhook to https://<operationsApiHost>/ once the event intake runs (bootstrap.md)."

if [[ ${#missing[@]} -gt 0 ]]; then
    log "Preflight failed; nothing was created. Missing:"
    printf '  - %s\n' "${missing[@]}" >&2
    exit 1
fi
log "Preflight passed."
