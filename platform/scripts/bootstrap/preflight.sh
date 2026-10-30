#!/usr/bin/env bash
# Read-only check that the person running the bootstrap has every required right:
# Contributor on the subscription, admin on the repository (repository secrets, issue
# webhook) and, given its private key, the agent's GitHub App installed on every repository
# in AGENT_REPOS with write access to issues, pull requests, contents and actions. Creates
# nothing. Exits non-zero and names each gap.
#
#   [GITHUB_APP_PRIVATE_KEY_FILE=...] platform/scripts/bootstrap/preflight.sh platform/scripts/bootstrap/staging.env
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source-path=SCRIPTDIR source=lib.sh
source "$here/lib.sh"
load_bootstrap_env "${1:-}"
require_tools az gh jq curl openssl

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

# GitHub App of the agent (replaces the PAT since 2026-10-30). The installation of each
# repository carries the permissions the organization granted to the app.
if [[ -z "${GITHUB_APP_PRIVATE_KEY_FILE:-}" ]]; then
    info "GitHub App checks skipped: set GITHUB_APP_PRIVATE_KEY_FILE to the app's private key (bootstrap.md, step 1)."
elif [[ -z "${GITHUB_APP_ID:-}" || -z "${GITHUB_APP_INSTALLATION_ID:-}" ]]; then
    fail "GitHub App: GITHUB_APP_ID and GITHUB_APP_INSTALLATION_ID must be set in the bootstrap config"
elif ! jwt="$(github_app_jwt "$GITHUB_APP_ID" "$(cat "$GITHUB_APP_PRIVATE_KEY_FILE" 2>/dev/null)")"; then
    fail "GitHub App private key $GITHUB_APP_PRIVATE_KEY_FILE (no readable PEM private key)"
else
    for repo in $AGENT_REPOS; do
        if ! installation="$(github_json "$jwt" GET "repos/$repo/installation")"; then
            fail "GitHub App $GITHUB_APP_ID installed on $repo (or the key belongs to another app)"
            continue
        fi
        # Workflows and the agent request tokens for this ID; a wrong one fails only later.
        id="$(jq -r '.id // empty' <<<"$installation")"
        if [[ "$id" != "$GITHUB_APP_INSTALLATION_ID" ]]; then
            fail "GitHub App installation ID $GITHUB_APP_INSTALLATION_ID on $repo (GitHub reports ${id:-none})"
        fi
        for permission in issues pull_requests contents actions; do
            access="$(jq -r --arg p "$permission" '.permissions[$p] // "none"' <<<"$installation")"
            if [[ "$access" == write ]]; then
                pass "GitHub App $permission (write) on $repo"
            else
                fail "GitHub App permission $permission (write) on $repo (granted: $access)"
            fi
        done
    done
fi

# Region information for choosing the stack configuration.
if versions="$(az aks get-versions --location "$LOCATION" --query 'values[?!isPreview].version' -o tsv 2>/dev/null)"; then
    info "Supported AKS versions in $LOCATION: $(tr '\n' ' ' <<<"$versions")"
else
    fail "AKS availability in region $LOCATION (az aks get-versions failed)"
fi
# Free vCPUs of one quota (a VM family, or "cores" for the region) against the node pool.
check_quota() {
    local quota="$1" usage="$2" need="$3" free
    free="$(jq -r --arg q "${quota,,}" \
        'map(select((.name.value | ascii_downcase) == $q)) | first | if . then (.limit | tonumber) - (.currentValue | tonumber) else empty end' \
        <<<"$usage")"
    if [[ -z "$free" ]]; then
        fail "vCPU quota $quota in $LOCATION (not listed by az vm list-usage)"
    elif (( free >= need )); then
        pass "vCPU quota $quota in $LOCATION: $need needed, $free free"
    else
        fail "vCPU quota $quota in $LOCATION: $NODE_COUNT x $NODE_VM_SIZE need $need, $free free"
    fi
}

# Subscriptions can be barred from PostgreSQL Flexible Server in single regions; the reason
# is empty where provisioning is allowed.
if pg_reason="$(az postgres flexible-server list-skus --location "$LOCATION" --query '[0].reason' -o tsv 2>/dev/null)"; then
    if [[ -z "$pg_reason" || "$pg_reason" == "None" ]]; then
        pass "PostgreSQL Flexible Server can be provisioned in $LOCATION"
    else
        fail "PostgreSQL Flexible Server in $LOCATION ($pg_reason)"
    fi
else
    fail "PostgreSQL Flexible Server in $LOCATION (az postgres flexible-server list-skus failed)"
fi

if [[ -n "${NODE_VM_SIZE:-}" ]]; then
    NODE_COUNT="${NODE_COUNT:-1}"
    [[ "$NODE_COUNT" =~ ^[1-9][0-9]*$ ]] || die "NODE_COUNT must be a positive integer."
    # --size matches prefixes, and an unknown size has no restrictions either; count exact names first.
    skus="$(az vm list-skus --location "$LOCATION" --size "$NODE_VM_SIZE" --resource-type virtualMachines \
        --query "[?name=='$NODE_VM_SIZE']" -o json 2>/dev/null || echo error)"
    if [[ "$skus" == "error" ]]; then
        restrictions="az vm list-skus failed"
    elif [[ "$(jq length <<<"$skus")" == "0" ]]; then
        restrictions="unknown size in this region"
    else
        # The node pool uses no availability zones, so only a regional restriction blocks it.
        restrictions="$(jq -r '[.[].restrictions[]? | select(.type == "Location") | .reasonCode] | join(", ")' <<<"$skus")"
        zones="$(jq -r '[.[].restrictions[]? | select(.type == "Zone") | .restrictionInfo.zones[]?] | unique | join(" ")' <<<"$skus")"
        [[ -z "$zones" ]] || info "$NODE_VM_SIZE is restricted in zones $zones; the regional node pool does not use zones."
    fi
    if [[ -z "$restrictions" ]]; then
        pass "VM size $NODE_VM_SIZE is available in $LOCATION"
    else
        fail "VM size $NODE_VM_SIZE in $LOCATION (restriction: $restrictions)"
    fi
    if [[ "$skus" != "error" && "$(jq length <<<"$skus")" != "0" ]]; then
        family="$(jq -r '.[0].family' <<<"$skus")"
        vcpus="$(jq -r '.[0].capabilities[] | select(.name == "vCPUs") | .value' <<<"$skus")"
        if usage="$(az vm list-usage --location "$LOCATION" -o json 2>/dev/null)"; then
            check_quota "$family" "$usage" $((vcpus * NODE_COUNT))
            check_quota cores "$usage" $((vcpus * NODE_COUNT))
        else
            fail "vCPU quota in $LOCATION (az vm list-usage failed)"
        fi
    fi
fi
info "MANUAL: issue webhook to <publicScheme>://<operationsApiHost>/events/github once the event intake runs (bootstrap.md, step 8)."

if [[ ${#missing[@]} -gt 0 ]]; then
    log "Preflight failed; nothing was created. Missing:"
    printf '  - %s\n' "${missing[@]}" >&2
    exit 1
fi
log "Preflight passed."
