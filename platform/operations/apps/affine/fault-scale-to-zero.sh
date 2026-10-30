#!/usr/bin/env bash
# Reparierbares Fehlerszenario: skaliert das AFFiNE-Deployment in staging auf 0 Replicas.
#
#   fault-scale-to-zero.sh trigger --environment staging   Fehler auslösen
#   fault-scale-to-zero.sh reset   --environment staging   bekannte Reparatur: app-deploy.yml starten
#
# Berührt ausschließlich spec.replicas des Deployments affine; Datenbank, PVC und Secrets bleiben unverändert.
# Voraussetzungen: trigger braucht KUBECONFIG auf den Staging-Cluster, reset die GitHub-CLI (gh).
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$here/../lib/cluster-guard.sh"

usage() { sed -n '2,8p' "$0" >&2; exit 2; }

command="${1:-}"; shift || true
environment=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --environment) environment="${2:-}"; shift 2 ;;
    *) usage ;;
  esac
done
[[ "$command" == "trigger" || "$command" == "reset" ]] || usage

if [[ "$environment" != "staging" ]]; then
  echo "Abbruch ohne Änderung: Fehlerszenarien sind nur für staging erlaubt (angegeben: '${environment:-leer}')." >&2
  exit 1
fi

namespace="affine-$environment"
deployment="affine"

case "$command" in
  trigger)
    : "${KUBECONFIG:?KUBECONFIG auf den Staging-Cluster fehlt}"
    require_platform_cluster "$environment"
    replicas="$(kubectl -n "$namespace" get deployment "$deployment" -o jsonpath='{.spec.replicas}')"
    echo "Vorher: $namespace/$deployment mit $replicas Replica(s)"
    kubectl -n "$namespace" scale deployment "$deployment" --replicas=0
    cat <<EOF

Fehler ausgelöst ($(date -u +%FT%TZ)). Erwartete Wirkung:
  - https://affine.<baseDomain>/info antwortet nicht mehr mit 200, der Smoke-Test schlägt fehl
  - nach einigen Minuten feuert alert-affine-staging-availability an ops-intake (Fired-Ereignis für affine/staging)
  - der Agent startet app-deploy.yml mit seiner operationId; danach laufen wieder die Replicas aus
    platform/environments/staging/affine.yaml, der Smoke-Test ist grün und der Alarm wird aufgelöst

Rücksetzen ohne Agent: $0 reset --environment staging
EOF
    ;;
  reset)
    operation="manual-affine-reset-$(date -u +%Y%m%d%H%M%S)"
    gh workflow run app-deploy.yml -f appId=affine -f environment="$environment" -f operationId="$operation"
    echo "app-deploy.yml gestartet mit operationId $operation; Lauf: gh run list --workflow app-deploy.yml"
    ;;
esac
