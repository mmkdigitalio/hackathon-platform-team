#!/usr/bin/env bash
# Task 6.4: erlaubte und verweigerte Verben des ServiceAccounts operations-agent.
# Aufruf: KUBECONFIG=<admin-kubeconfig> rbac-check.sh [agent-namespace] [app-namespace]
set -uo pipefail
NS=${1:-operations-agent-staging}; APP=${2:-affine-staging}
SA=system:serviceaccount:$NS:operations-agent
fail=0
check() {  # check <erwartet yes|no> <verb> <ressource>
  got=$(kubectl auth can-i "$2" "$3" -n "$APP" --as "$SA" 2>/dev/null)
  [ "$got" = "$1" ] && mark=ok || { mark=FEHLER; fail=1; }
  printf '%-7s %-6s %-22s erwartet %-3s ist %s\n' "$mark" "$2" "$3" "$1" "$got"
}
for r in deployments replicasets pods pods/log events namespaces jobs; do check yes get "$r"; done
check yes list pods; check yes patch deployments; check yes create jobs; check yes delete jobs
check no delete deployments; check no create deployments; check no update deployments
check no get secrets; check no create pods/exec; check no delete pods; check no patch namespaces
check no create rolebindings; check no '*' '*'
exit $fail
