#!/usr/bin/env bash
# Local runs of the foundation stack under the Azure CLI login of a Contributor (the
# foundation has no CI deployment). Selects or creates organization/foundation/<env> in
# the state backend from the bootstrap and passes secrets only through stdin.
#
#   foundation.sh <env-file> secrets     store model keys, webhook secret and GitHub App key as stack secrets
#   foundation.sh <env-file> preview [pulumi args, e.g. --expect-no-changes]
#   foundation.sh <env-file> up          deploy after confirmation, then print the summary
#   foundation.sh <env-file> outputs     non-secret view of the output contract
#   foundation.sh <env-file> ingress-check   HTTPS call of a temporary test ingress (task 3.3)
#
# Environment: PULUMI_CONFIG_PASSPHRASE (always); for "secrets" GITHUB_WEBHOOK_SECRET,
# GITHUB_APP_PRIVATE_KEY_FILE (.pem of the agent's GitHub App), at least one of
# MODEL_KEY_ANTHROPIC, MODEL_KEY_AZURE_OPENAI and optionally the fallback PAT OPS_GITHUB_TOKEN.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source-path=SCRIPTDIR source=lib.sh
source "$here/lib.sh"
config_file="${1:-}"
command="${2:-}"
load_bootstrap_env "$config_file"
require_tools az pulumi npm node git
[[ "$command" =~ ^(secrets|preview|up|outputs|ingress-check)$ ]] ||
    die "Usage: $0 <env-file> secrets|preview|up|outputs|ingress-check"
[[ -n "${PULUMI_CONFIG_PASSPHRASE:-}" ]] || die "PULUMI_CONFIG_PASSPHRASE is not set (team password manager)."

az account set --subscription "$SUBSCRIPTION_ID"
export ARM_SUBSCRIPTION_ID="$SUBSCRIPTION_ID"
export PULUMI_BACKEND_URL="azblob://$STATE_CONTAINER"
export AZURE_STORAGE_ACCOUNT="$STATE_STORAGE_ACCOUNT"
AZURE_STORAGE_KEY="$(state_storage_key)"
export AZURE_STORAGE_KEY

(cd "$platform_dir" && npm run --silent build)
cd "$platform_dir/infra/foundation"
pulumi stack select --create "$FOUNDATION_STACK" --secrets-provider passphrase >/dev/null

# pulumi reads the value from stdin when none is given on the command line.
set_secret_config() { printf '%s' "$2" | pulumi config set --secret --stack "$FOUNDATION_STACK" --path "$1"; }

output() { pulumi stack output "$1" --stack "$FOUNDATION_STACK" "${@:2}"; }

# retry <attempts> <pause seconds> <command...>
retry() {
    local attempts="$1" pause="$2" attempt
    shift 2
    for ((attempt = 1; attempt <= attempts; attempt++)); do
        "$@" && return 0
        ((attempt == attempts)) || sleep "$pause"
    done
    return 1
}

# Task 3.3: a temporary whoami app behind ingress-check.<baseDomain> with a Let's Encrypt
# certificate, called over HTTPS, then removed again (namespace ingress-check).
ingress_check() {
    require_tools kubectl curl
    local work host issuer class
    work="$(mktemp -d)"
    (umask 077 && output kubeconfig --show-secrets >"$work/kubeconfig")
    export KUBECONFIG="$work/kubeconfig"
    host="ingress-check.$(output baseDomain)"
    issuer="$(output clusterIssuerName)"
    class="$(output ingressClassName)"
    # shellcheck disable=SC2064 # expand now: work is local
    trap "kubectl delete namespace ingress-check --ignore-not-found --wait=false >/dev/null; rm -rf '$work'" EXIT
    kubectl apply -f - >/dev/null <<MANIFEST
apiVersion: v1
kind: Namespace
metadata: {name: ingress-check}
---
apiVersion: apps/v1
kind: Deployment
metadata: {name: whoami, namespace: ingress-check}
spec:
  selector: {matchLabels: {app: whoami}}
  template:
    metadata: {labels: {app: whoami}}
    spec:
      containers: [{name: whoami, image: traefik/whoami:v1.12.0, ports: [{containerPort: 80}]}]
---
apiVersion: v1
kind: Service
metadata: {name: whoami, namespace: ingress-check}
spec: {selector: {app: whoami}, ports: [{port: 80, targetPort: 80}]}
---
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: whoami
  namespace: ingress-check
  annotations: {cert-manager.io/cluster-issuer: $issuer}
spec:
  ingressClassName: $class
  tls: [{hosts: [$host], secretName: ingress-check-tls}]
  rules:
    - host: $host
      http: {paths: [{path: /, pathType: Prefix, backend: {service: {name: whoami, port: {number: 80}}}}]}
MANIFEST
    kubectl -n ingress-check rollout status deployment/whoami --timeout=180s >/dev/null
    log "Waiting up to 5 minutes for the certificate of $host"
    # Traefik keeps serving its default certificate until it has loaded the new TLS secret.
    if kubectl -n ingress-check wait --for=condition=Ready certificate/ingress-check-tls --timeout=300s >/dev/null &&
        retry 12 5 curl -fsS --max-time 20 "https://$host/" >/dev/null 2>&1; then
        log "OK: https://$host/ answers with a valid Let's Encrypt certificate."
        return 0
    fi
    kubectl -n ingress-check describe certificate ingress-check-tls >&2 || true
    if curl -fsS --max-time 20 "http://$host/" >/dev/null; then
        die "HTTPS failed but http://$host/ answers: certificate problem. Consider httpFallback (operations/foundation/operations.md)."
    fi
    die "Neither https nor http://$host/ answers: check Traefik and the load balancer."
}

case "$command" in
    secrets)
        [[ -n "${GITHUB_WEBHOOK_SECRET:-}" ]] || die "GITHUB_WEBHOOK_SECRET is not set."
        [[ -n "${GITHUB_APP_PRIVATE_KEY_FILE:-}" ]] ||
            die "GITHUB_APP_PRIVATE_KEY_FILE is not set: path to the private key (.pem) of the agent's GitHub App."
        require_tools openssl
        # The agent signs without a passphrase; an encrypted or broken key would fail only there.
        openssl pkey -in "$GITHUB_APP_PRIVATE_KEY_FILE" -noout -passin pass: >/dev/null 2>&1 ||
            die "$GITHUB_APP_PRIVATE_KEY_FILE is not an unencrypted PEM private key."
        [[ -n "${MODEL_KEY_ANTHROPIC:-}${MODEL_KEY_AZURE_OPENAI:-}" ]] ||
            die "Set MODEL_KEY_ANTHROPIC and/or MODEL_KEY_AZURE_OPENAI; the agent needs at least one model key."
        set_secret_config githubWebhookSecret "$GITHUB_WEBHOOK_SECRET"
        set_secret_config githubAppPrivateKey "$(<"$GITHUB_APP_PRIVATE_KEY_FILE")"
        # Fallback only; the agent prefers its GitHub App.
        if [[ -n "${OPS_GITHUB_TOKEN:-}" ]]; then
            set_secret_config opsGithubToken "$OPS_GITHUB_TOKEN"
        fi
        if [[ -n "${MODEL_KEY_ANTHROPIC:-}" ]]; then
            set_secret_config 'modelApiKeys["anthropic"]' "$MODEL_KEY_ANTHROPIC"
        fi
        if [[ -n "${MODEL_KEY_AZURE_OPENAI:-}" ]]; then
            set_secret_config 'modelApiKeys["azure-openai"]' "$MODEL_KEY_AZURE_OPENAI"
        fi
        log "Stored encrypted in Pulumi.$ENVIRONMENT.yaml; commit that file. Run 'up' to pass them on as outputs, then delete the downloaded .pem."
        ;;
    preview)
        pulumi preview --diff --stack "$FOUNDATION_STACK" "${@:3}"
        ;;
    up)
        pulumi up --stack "$FOUNDATION_STACK"
        # "-dirty" marks a run from uncommitted changes, which the Git state cannot reproduce.
        GIT_SHA="$(git -C "$platform_dir" describe --always --dirty --abbrev=40 --exclude='*')" node "$here/summary.mjs" "$FOUNDATION_STACK"
        ;;
    outputs)
        pulumi stack output --json --stack "$FOUNDATION_STACK"
        ;;
    ingress-check)
        ingress_check
        ;;
esac
