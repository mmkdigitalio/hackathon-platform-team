#!/usr/bin/env bash
# Exercises every access path from CI and reports each category separately: foundation
# state, Kubernetes (admin kubeconfig), ACR (admin user), PostgreSQL (TLS), diagnostics
# storage (key: write, read, delete) and GitHub (agent PAT). Runs once without retries;
# with keys instead of role assignments there is nothing to wait for. Never prints
# secret values: the kubeconfig goes to a private file, passwords and tokens through
# stdin or the environment.
#
# Environment (from .github/workflows/foundation-access-check.yml):
#   FOUNDATION_STATE_OUTCOME   outcome of the foundation-outputs step (success|failure)
#   KUBECONFIG_CONTENT, ACR_LOGIN_SERVER, ACR_USERNAME, ACR_PASSWORD, POSTGRES_HOST,
#   POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DATABASE, DIAGNOSTICS_ACCOUNT,
#   DIAGNOSTICS_CONTAINER, DIAGNOSTICS_KEY, AGENT_GITHUB_TOKEN, PAT_REPOS
#   WORKFLOW_GITHUB_TOKEN      optional: repository secret OPS_GITHUB_TOKEN, checked as well
#   BREAK_CATEGORY             optional: replace this category's credential with an invalid one
set -uo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source-path=SCRIPTDIR source=lib.sh
source "$here/lib.sh"

categories=(foundation-state kubernetes acr postgres diagnostics-storage github)
[[ -n "${WORKFLOW_GITHUB_TOKEN:-}" ]] && categories+=(github-workflow-token)
break_category="${BREAK_CATEGORY:-none}"
if [[ "$break_category" != none && " ${categories[*]} " != *" $break_category "* ]]; then
    die "BREAK_CATEGORY must be none or one of: ${categories[*]}"
fi

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
chmod 700 "$work"
detail=""

# An invalid credential of the right shape, for the deliberately red run.
if [[ "$break_category" != none ]]; then
    log "BREAK_CATEGORY=$break_category: using an invalid credential for this category"
    case "$break_category" in
        kubernetes)
            # Without client certificate and token the API server sees an anonymous user.
            KUBECONFIG_CONTENT="$(grep -vE '^[[:space:]]*(client-certificate-data|client-key-data|token):' <<<"${KUBECONFIG_CONTENT:-}")" ;;
        acr) ACR_PASSWORD="invalid-access-check-password" ;;
        postgres) POSTGRES_PASSWORD="invalid-access-check-password" ;;
        diagnostics-storage) DIAGNOSTICS_KEY="aW52YWxpZC1hY2Nlc3MtY2hlY2sta2V5" ;;
        github) AGENT_GITHUB_TOKEN="github_pat_invalid_access_check" ;;
        github-workflow-token) WORKFLOW_GITHUB_TOKEN="github_pat_invalid_access_check" ;;
        foundation-state) ;; # broken by the workflow: the composite action gets an invalid backend key
    esac
fi

need() {
    local name
    for name in "$@"; do
        [[ -n "${!name:-}" ]] || { detail="$name is empty"; return 1; }
    done
}

tools() {
    local tool
    for tool in "$@"; do
        command -v "$tool" >/dev/null || { detail="tool $tool is not installed"; return 1; }
    done
}

check_kubernetes() {
    need KUBECONFIG_CONTENT && tools kubectl || return 1
    (umask 077 && printf '%s\n' "$KUBECONFIG_CONTENT" >"$work/kubeconfig")
    local answer
    answer="$(kubectl --kubeconfig "$work/kubeconfig" --request-timeout=30s auth can-i '*' '*' --all-namespaces 2>/dev/null)"
    [[ "$answer" == "yes" ]] || { detail="kubectl auth can-i '*' '*' --all-namespaces: ${answer:-error}"; return 1; }
}

check_acr() {
    need ACR_LOGIN_SERVER ACR_USERNAME ACR_PASSWORD && tools docker || return 1
    printf '%s' "$ACR_PASSWORD" | DOCKER_CONFIG="$work/docker" docker login "$ACR_LOGIN_SERVER" \
        --username "$ACR_USERNAME" --password-stdin >/dev/null 2>&1 || { detail="docker login $ACR_LOGIN_SERVER failed"; return 1; }
}

check_postgres() {
    need POSTGRES_HOST POSTGRES_USER POSTGRES_PASSWORD POSTGRES_DATABASE && tools psql || return 1
    # Proves login and that the session is encrypted.
    local ssl
    ssl="$(PGPASSWORD="$POSTGRES_PASSWORD" PGSSLMODE=require PGCONNECT_TIMEOUT=15 psql \
        "host=$POSTGRES_HOST port=5432 dbname=$POSTGRES_DATABASE user=$POSTGRES_USER" -tAc \
        'select ssl from pg_stat_ssl where pid = pg_backend_pid()' 2>/dev/null)"
    [[ "$ssl" == "t" ]] || { detail="login with sslmode=require failed or session not encrypted"; return 1; }
}

storage() {
    AZURE_STORAGE_ACCOUNT="$DIAGNOSTICS_ACCOUNT" AZURE_STORAGE_KEY="$DIAGNOSTICS_KEY" \
        az storage blob "$@" --auth-mode key --container-name "$DIAGNOSTICS_CONTAINER" --only-show-errors >/dev/null 2>&1
}

check_diagnostics_storage() {
    need DIAGNOSTICS_ACCOUNT DIAGNOSTICS_CONTAINER DIAGNOSTICS_KEY && tools az || return 1
    local blob="access-check/${GITHUB_RUN_ID:-local}-$RANDOM.txt" expected
    expected="access-check $(date -u +%s)"
    printf '%s' "$expected" >"$work/upload.txt"
    storage upload --name "$blob" --file "$work/upload.txt" --overwrite || { detail="write failed"; return 1; }
    storage download --name "$blob" --file "$work/download.txt" --overwrite || { detail="read failed"; return 1; }
    [[ "$(cat "$work/download.txt")" == "$expected" ]] || { detail="read returned other content"; return 1; }
    storage delete --name "$blob" || { detail="delete failed"; return 1; }
}

github_reads() {
    local token="$1" repo code
    for repo in $PAT_REPOS; do
        code="$(github_status "$token" GET "repos/$repo")"
        [[ "$code" == "200" ]] || { detail="repository $repo: HTTP $code"; return 1; }
        code="$(github_status "$token" GET "repos/$repo/issues?per_page=1")"
        [[ "$code" == "200" ]] || { detail="issues of $repo: HTTP $code"; return 1; }
    done
}

check_github() {
    need AGENT_GITHUB_TOKEN PAT_REPOS && tools curl || return 1
    github_reads "$AGENT_GITHUB_TOKEN"
}

check_github_workflow_token() {
    need WORKFLOW_GITHUB_TOKEN PAT_REPOS && tools curl || return 1
    github_reads "$WORKFLOW_GITHUB_TOKEN"
}

declare -A result reason
failed=0
for category in "${categories[@]}"; do
    detail=""
    if [[ "$category" == foundation-state ]]; then
        if [[ "${FOUNDATION_STATE_OUTCOME:-}" == success ]]; then
            result[$category]=pass
        else
            result[$category]=fail
            reason[$category]="foundation-outputs step: ${FOUNDATION_STATE_OUTCOME:-not run}"
        fi
    elif [[ "${result[foundation-state]}" != pass ]]; then
        result[$category]=fail
        reason[$category]="skipped: no foundation outputs"
    elif "check_${category//-/_}"; then
        result[$category]=pass
    else
        result[$category]=fail
        reason[$category]="$detail"
    fi
    [[ "${result[$category]}" == pass ]] || failed=1
    log "$(printf '%-22s %s %s' "$category" "${result[$category]^^}" "${reason[$category]:-}")"
done

{
    echo "## Foundation access check"
    echo
    echo "| Category | Result | Detail |"
    echo "| --- | --- | --- |"
    for category in "${categories[@]}"; do
        echo "| \`$category\` | ${result[$category]} | ${reason[$category]:-} |"
    done
    [[ "$break_category" == none ]] || { echo; echo "Deliberately broken category: \`$break_category\`"; }
} >>"${GITHUB_STEP_SUMMARY:-/dev/stderr}"

if [[ "$failed" -ne 0 ]]; then
    log "Access check failed; the bootstrap is not fully functional."
    exit 1
fi
log "Access check passed: every category succeeded."
