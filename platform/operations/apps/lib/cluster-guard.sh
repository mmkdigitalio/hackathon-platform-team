#!/usr/bin/env bash
# Gemeinsame lokale Prüfung des Zielclusters vor Kubernetes-Abfragen und Änderungen.
set -euo pipefail

require_platform_cluster() {
  local target_env="${1:-}" server host=""
  [[ -n "$target_env" ]] || { echo "Abbruch ohne Änderung: Zielumgebung fehlt; --environment muss gesetzt und nicht leer sein." >&2; return 1; }

  if [[ "${OPS_ALLOW_ANY_CLUSTER:-}" == "true" ]]; then
    echo "WARNUNG: OPS_ALLOW_ANY_CLUSTER=true überspringt die Clusterprüfung; nur für lokale kind-Tests verwenden!" >&2
    return 0
  fi

  # config view liest ausschließlich die lokale Kubeconfig, ohne Verbindung zum Cluster.
  server="$(kubectl config view --minify -o jsonpath='{.clusters[0].cluster.server}')" || server=""
  # Nur den Host aus der URL verwenden; Pfad, Port und fremde Namensbestandteile zählen nicht.
  if [[ "$server" =~ ^https?://([a-zA-Z0-9.-]+)(:[0-9]+)?([/?#][^[:space:]]*)?$ ]]; then
    host="${BASH_REMATCH[1]}"
  fi
  if [[ "$host" != "mmk-platform-$target_env-"?*".azmk8s.io" ]]; then
    echo "Abbruch ohne Änderung: API-Server '${server:-leer oder nicht lesbar}' gehört nicht zum Zielcluster; erwartet: mmk-platform-$target_env-*.azmk8s.io." >&2
    return 1
  fi
}
