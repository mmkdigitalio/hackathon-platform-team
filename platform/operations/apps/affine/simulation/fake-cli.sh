#!/usr/bin/env bash
# Simulierte Gegenstellen für playbook-self-heal.sh simulate: kubectl, gh, az, curl und psql als Symlinks in bin/.
# Kein Aufruf erreicht echte Systeme. Der Ablauf ist eine reine Funktion der Zeit seit dem Stopp
# (Datei $SIM_STATE/trigger, angelegt von "kubectl scale"); Zeitpunkte in Sekunden danach:
#   25 Alarm Fired · 40 Agent startet app-deploy.yml · 100 Lauf grün, AFFiNE bereit · 125 Alarm Resolved · 130 Auftrag resolved
set -euo pipefail

state="${SIM_STATE:?SIM_STATE fehlt}"
t_fired=25 t_dispatch=40 t_lock=48 t_up=58 t_rollout=82 t_smoke=90 t_done=100 t_resolved=125 t_job=130
operation="op-5e1f0c2a-7b3d-4c11-9a8e-2f6d4b1c9e07"

since_trigger() {
  [[ -f "$state/trigger" ]] || { echo -1; return; }
  echo $(($(date +%s) - $(cat "$state/trigger")))
}
at() { jq -nr --argjson e "$(($(cat "$state/trigger") + $1))" '$e | todate'; }
replicas() { local e; e="$(since_trigger)"; ((e < 0 || e >= t_done)) && echo 1 || echo 0; }

case "$(basename "$0")" in
  kubectl)
    args="$*"
    case "$args" in
      version*) ;;
      *"scale deployment"*) date +%s >"$state/trigger"; echo "deployment.apps/affine scaled" ;;
      *"operations-agent-staging get ingress"*) echo "operations-agent.simulation.invalid" ;;
      *"get ingress"*) echo "affine.simulation.invalid" ;;
      *"action-id"*) ;;
      *"spec.replicas"* | *"readyReplicas"*) replicas ;;
    esac
    ;;
  curl)
    url="${*: -1}"
    if [[ "$url" == */healthz || "$(replicas)" -ge 1 ]]; then printf 200; else printf 000; exit 7; fi
    ;;
  az)
    [[ "${1:-}" == "rest" ]] || exit 0
    e="$(since_trigger)"
    if ((e < t_fired)); then echo '{"value": []}'; exit 0; fi
    condition="$( ((e >= t_resolved)) && echo Resolved || echo Fired)"
    jq -n --arg start "$(at "$t_fired")" --arg c "$condition" '{value: [{
      id: "/subscriptions/simulation/providers/Microsoft.AlertsManagement/alerts/sim-affine-staging-availability",
      properties: {essentials: {
        alertRule: "/subscriptions/simulation/resourceGroups/rg-mmk-platform-staging/providers/microsoft.insights/metricalerts/alert-affine-staging-availability",
        monitorCondition: $c, startDateTime: $start}}}]}'
    ;;
  gh)
    case "${1:-} ${2:-}" in
      "auth status") ;;
      "run list")
        e="$(since_trigger)"
        if [[ "$*" == *--status* ]] || ((e < t_dispatch)); then echo '[]'; exit 0; fi
        jq -n --arg at "$(at "$t_dispatch")" --arg op "$operation" '[{databaseId: 1, url: "",
          displayTitle: "app-deploy affine/staging op=\($op) action=act-3f9a1c", createdAt: $at}]'
        ;;
      "run view")
        # Ausgabe in der Form, die playbook-self-heal.sh mit -q erzeugt: Status, Ergebnis, Schritt (getrennt durch 0x1f)
        e="$(since_trigger)"
        if ((e >= t_done)); then printf 'completed\037success\037\n'
        elif ((e >= t_smoke)); then printf 'in_progress\037\037Smoke-Test\n'
        elif ((e >= t_rollout)); then printf 'in_progress\037\037Rollout prüfen\n'
        elif ((e >= t_up)); then printf 'in_progress\037\037Ausrollen\n'
        elif ((e >= t_lock)); then printf 'in_progress\037\037Sperre erwerben\n'
        else printf 'in_progress\037\037Foundation-Ausgaben\n'
        fi
        ;;
    esac
    ;;
  psql)
    e="$(since_trigger)"
    if ((e >= t_job)); then echo "8c2d41e0-5b7a-4f3e-9d61-0a4b7e2c1f95 resolved "
    elif ((e >= t_done)); then echo "8c2d41e0-5b7a-4f3e-9d61-0a4b7e2c1f95 verifying "
    else echo "8c2d41e0-5b7a-4f3e-9d61-0a4b7e2c1f95 acting "
    fi
    ;;
  *) echo "fake-cli.sh: unbekannter Befehl $(basename "$0")" >&2; exit 1 ;;
esac
