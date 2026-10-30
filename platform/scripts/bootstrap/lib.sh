#!/usr/bin/env bash
# Shared helpers for the bootstrap scripts. Never print secret values.

# Command substitutions must fail like plain commands, so a failed lookup can never be
# mistaken for "missing" and trigger a create or overwrite (needs bash >= 4.4).
if (( BASH_VERSINFO[0] < 4 || (BASH_VERSINFO[0] == 4 && BASH_VERSINFO[1] < 4) )); then
    echo "bash >= 4.4 is required (macOS: brew install bash)." >&2
    exit 1
fi
shopt -s inherit_errexit

bootstrap_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
platform_dir="$(cd "$bootstrap_dir/../.." && pwd)"
conventions_file="$platform_dir/schemas/conventions/conventions.json"

log() { printf '%s %s\n' "$(date -u +%H:%M:%S)" "$*" >&2; }
die() { log "ERROR: $*"; exit 1; }

require_tools() {
    local tool
    for tool in "$@"; do
        command -v "$tool" >/dev/null || die "Required tool '$tool' is not installed."
    done
}

# Loads platform/scripts/bootstrap/<env>.env (no secrets) and the state backend names
# from conventions.json, the single source for names shared with workflows.
load_bootstrap_env() {
    local file="${1:-}"
    [[ -n "$file" ]] || die "Usage: $0 <path/to/<env>.env>"
    [[ -f "$file" ]] || die "Bootstrap config $file not found (template: platform/scripts/bootstrap/staging.env.example)."
    # shellcheck disable=SC1090
    source "$file"
    local name
    for name in SUBSCRIPTION_ID LOCATION ENVIRONMENT GITHUB_REPO AGENT_REPOS STATE_STORAGE_ACCOUNT; do
        [[ -n "${!name:-}" ]] || die "$name is empty in $file."
    done
    [[ "$ENVIRONMENT" =~ ^(staging|production)$ ]] || die "ENVIRONMENT must be staging or production."
    [[ "$STATE_STORAGE_ACCOUNT" =~ ^[a-z0-9]{3,24}$ ]] || die "STATE_STORAGE_ACCOUNT must be 3-24 lowercase letters and digits."
    [[ "$GITHUB_REPO" =~ ^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$ ]] || die "GITHUB_REPO must be <owner>/<repo>."
    require_tools jq
    STATE_RESOURCE_GROUP="$(jq -er .stateBackend.resourceGroup "$conventions_file")"
    STATE_CONTAINER="$(jq -er .stateBackend.container "$conventions_file")"
    FOUNDATION_STACK="organization/foundation/$ENVIRONMENT"
    export SUBSCRIPTION_ID LOCATION ENVIRONMENT GITHUB_REPO AGENT_REPOS STATE_STORAGE_ACCOUNT \
        STATE_RESOURCE_GROUP STATE_CONTAINER FOUNDATION_STACK GITHUB_APP_ID GITHUB_APP_INSTALLATION_ID
}

# HTTP status of a GitHub REST call. The token goes to curl through a config on stdin,
# never through argv.
github_status() {
    local token="$1" method="$2" path="$3" body="${4:-}" code
    local args=(-sS -o /dev/null -w '%{http_code}' -X "$method"
        -H 'Accept: application/vnd.github+json' -H 'X-GitHub-Api-Version: 2022-11-28' -K -)
    [[ -z "$body" ]] || args+=(--data "$body")
    code="$(curl "${args[@]}" "https://api.github.com/$path" <<<"header = \"Authorization: Bearer $token\"" 2>/dev/null || true)"
    printf '%s' "${code:-000}"
}

# Response body of a GitHub REST call; fails unless the status is 2xx.
github_json() {
    local token="$1" method="$2" path="$3" out
    out="$(curl -sS -w '\n%{http_code}' -X "$method" -H 'Accept: application/vnd.github+json' \
        -H 'X-GitHub-Api-Version: 2022-11-28' -K - "https://api.github.com/$path" \
        <<<"header = \"Authorization: Bearer $token\"" 2>/dev/null)" || return 1
    [[ "${out##*$'\n'}" == 2?? ]] || return 1
    printf '%s' "${out%$'\n'*}"
}

base64url() { openssl base64 -A | tr '+/' '-_' | tr -d '='; }

# JWT of the agent's GitHub App (RS256, backdated 60 s against clock skew, valid 9 minutes).
# The PEM comes as a value, also with escaped line breaks, and reaches openssl through a
# file descriptor, never through argv.
github_app_jwt() {
    local app_id="$1" key="${2//\\n/$'\n'}" header payload signature now
    now="$(date +%s)"
    header="$(printf '%s' '{"alg":"RS256","typ":"JWT"}' | base64url)"
    payload="$(printf '{"iat":%d,"exp":%d,"iss":"%s"}' $((now - 60)) $((now + 540)) "$app_id" | base64url)"
    signature="$(printf '%s.%s' "$header" "$payload" |
        openssl dgst -sha256 -sign <(printf '%s\n' "$key") 2>/dev/null | base64url)" || return 1
    [[ -n "$signature" ]] || return 1
    printf '%s.%s.%s' "$header" "$payload" "$signature"
}

# Installation token of the GitHub App (valid one hour), the same access the agent uses.
github_app_token() {
    local app_id="$1" key="$2" installation_id="$3" jwt
    jwt="$(github_app_jwt "$app_id" "$key")" || return 1
    github_json "$jwt" POST "app/installations/$installation_id/access_tokens" | jq -er .token
}

# Storage key of the state backend; requires an Azure CLI login.
state_storage_key() {
    az storage account keys list --account-name "$STATE_STORAGE_ACCOUNT" \
        --resource-group "$STATE_RESOURCE_GROUP" --query '[0].value' -o tsv
}
