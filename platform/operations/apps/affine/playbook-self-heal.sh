#!/usr/bin/env bash
# Playbook Selbstheilung: stoppt AFFiNE (0 Replicas) und verfolgt Alarm, Reparatur durch den Agenten und Wiederherstellung.
#
#   playbook-self-heal.sh check --environment staging   nur Vorprüfung, keine Änderung
#   playbook-self-heal.sh run   --environment staging   Vorprüfung, Stopp, alle Stufen verfolgen
#   playbook-self-heal.sh simulate --environment staging   Probelauf mit simulierten Systemen (ca. 2 min), ohne Eingriff
#     [--scheme https|http] [--poll-seconds 30] [--report <datei.json>] [--html <verzeichnis>]
#
# simulate ersetzt kubectl, gh, az, curl und psql durch simulation/fake-cli.sh und kennzeichnet jede Ausgabe als Simulation.
# --html schreibt eine Live-Ansicht (index.html, status.js) für den Browser, etwa für eine Demo.
# Stufen und Ablauf: platform/operations/apps/affine/playbook-self-heal.md. Gestoppt wird über fault-scale-to-zero.sh.
# Voraussetzungen: KUBECONFIG auf den Staging-Cluster, gh und az angemeldet, jq, curl.
# Optional für den Auftragsstatus: psql und OPS_DATABASE_URL oder PG*-Variablen (Datenbank operations).
# Exit-Codes: 0 alle Stufen erfüllt, 1 Vorprüfung oder Stufe gescheitert bzw. Zeit überschritten, 2 Aufruffehler.
set -euo pipefail

usage() { sed -n '2,14p' "$0" >&2; exit 2; }

command="${1:-}"; shift || true
environment=""; scheme=""; poll=""; report=""; html_dir=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --environment) environment="${2:-}"; shift 2 ;;
    --scheme) scheme="${2:-}"; shift 2 ;;
    --poll-seconds) poll="${2:-}"; shift 2 ;;
    --report) report="${2:-}"; shift 2 ;;
    --html) html_dir="${2:-}"; shift 2 ;;
    *) usage ;;
  esac
done
[[ "$command" == "check" || "$command" == "run" || "$command" == "simulate" ]] || usage
[[ -z "$scheme" || "$scheme" == "https" || "$scheme" == "http" ]] || usage
[[ -z "$poll" || "$poll" =~ ^[1-9][0-9]*$ ]] || usage

if [[ "$environment" != "staging" ]]; then
  echo "Abbruch ohne Änderung: das Playbook ist nur für staging erlaubt (angegeben: '${environment:-leer}')." >&2
  exit 1
fi

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(git -C "$here" rev-parse --show-toplevel)"

# Simulation: alle Gegenstellen aus simulation/bin, kein Kubeconfig, keine Datenbank, keine Azure-Subscription
simulation=""
if [[ "$command" == "simulate" ]]; then
  simulation=1 command=run
  SIM_STATE="$(mktemp -d)"
  export SIM_STATE PATH="$here/simulation/bin:$PATH" KUBECONFIG=/dev/null PGHOST=simulation AZURE_SUBSCRIPTION_ID=simulation
  unset OPS_DATABASE_URL
  poll="${poll:-2}"
  html_dir="${html_dir:-${TMPDIR:-/tmp}/selbstheilung-simulation}"
fi
poll="${poll:-30}"
[[ -z "$html_dir" ]] || { mkdir -p "$html_dir"; html_dir="$(cd "$html_dir" && pwd)"; }
[[ -z "$report" || "$report" == /* ]] || report="$PWD/$report"
cd "$root"

namespace="affine-$environment"
deployment="affine"
agent_namespace="operations-agent-$environment"
rule="alert-affine-$environment-availability"
reset_hint="Rücksetzen ohne Agent: $here/fault-scale-to-zero.sh reset --environment $environment"

# Zeitgrenzen je Stufe in Sekunden (Herleitung im Playbook)
limit_down=300 limit_fired=1200 limit_repair=900 limit_deploy=1800 limit_healthy=600 limit_resolved=1200 limit_job=900

# --- Stufen und Ausgabe -------------------------------------------------------------------

keys=(preflight stop down fired repair deploy healthy resolved job)
labels=("Vorprüfung" "AFFiNE gestoppt" "App nicht erreichbar" "Alarm ausgelöst" "Agent greift ein"
  "Deploy-Lauf grün" "App wieder erreichbar" "Alarm aufgelöst" "Auftrag abgeschlossen")
actors=("Playbook" "Playbook" "AFFiNE" "Azure Monitor" "Betriebsagent" "GitHub Actions" "Kubernetes" "Azure Monitor" "Betriebsagent")
st_state=(); st_detail=(); st_link=(); st_start=(); st_at=()
for i in "${!keys[@]}"; do st_state[i]=pending; st_detail[i]=""; st_link[i]=""; st_start[i]=""; st_at[i]=""; done

t0="$(date +%s)"
result=running finished="" observation="" current=0 app_url="" agent_url=""

live=""; [[ -t 1 ]] && live=1
if [[ -n "$live" && -z "${NO_COLOR:-}" ]]; then
  c_ok=$'\033[32m' c_err=$'\033[31m' c_run=$'\033[33m' c_dim=$'\033[2m' c_bold=$'\033[1m' c_off=$'\033[0m'
else
  c_ok="" c_err="" c_run="" c_dim="" c_bold="" c_off=""
fi
spinner=("⠋" "⠙" "⠹" "⠸" "⠼" "⠴" "⠦" "⠧" "⠇" "⠏")

# Breite in Zeichen statt Bytes (printf in bash 3.2 zählt Umlaute doppelt)
pad() { printf '%s%*s' "$1" $(($2 - ${#1})) ''; }
clock() { printf '%02d:%02d' $(($1 / 60)) $(($1 % 60)); }
clear_line() { if [[ -n "$live" ]]; then printf '\r\033[K'; fi; }
log() { clear_line; printf '%s[%s]%s %s\n' "$c_dim" "$(date -u +%T)" "$c_off" "$*"; }

status_json() {
  for i in "${!keys[@]}"; do
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "${keys[i]}" "${labels[i]}" "${actors[i]}" "${st_state[i]}" \
      "${st_detail[i]}" "${st_link[i]}" "${st_start[i]}" "${st_at[i]}"
  done | jq -R -s -c --arg env "$environment" --arg result "$result" --arg started "$t0" --arg finished "$finished" \
    --arg obs "$observation" --arg app "$app_url" --arg agent "$agent_url" --arg rule "$rule" --argjson current "$current" \
    --argjson simulation "$([[ -n "$simulation" ]] && echo true || echo false)" '
    def ts: if . == "" then null else (tonumber | todate) end;
    {app: "affine", environment: $env, simulation: $simulation, result: $result, started: ($started | ts), finished: ($finished | ts),
     updated: (now | floor | todate), current: $current, observation: $obs, rule: $rule,
     appUrl: (if $app == "" then null else $app end), agentUrl: (if $agent == "" then null else $agent end),
     stages: [split("\n")[] | select(length > 0) | split("\t")
       | {key: .[0], label: .[1], actor: .[2], state: .[3], detail: .[4],
          link: (if .[5] == "" then null else .[5] end), startedAt: (.[6] | ts), at: (.[7] | ts)}]}'
}

render() {
  [[ -n "$html_dir" ]] || return 0
  { printf 'window.__playbook = '; status_json; printf ';\n'; } >"$html_dir/status.js.tmp"
  mv "$html_dir/status.js.tmp" "$html_dir/status.js"
}

begin() {
  current="$1"; observation=""
  st_state[$1]=running; st_start[$1]="$(date +%s)"
  render
}

finish() {  # Stufe, Zustand, Detail[, Link]
  local symbol color link="${4:-}" shown=""
  st_state[$1]="$2"; st_detail[$1]="$3"; st_link[$1]="$link"; st_at[$1]="$(date +%s)"
  # Lange Links (Azure-Portal) nur in der Live-Ansicht und im Bericht
  [[ -z "$link" || ${#link} -gt 80 ]] || shown="  $c_dim$link$c_off"
  [[ -n "${st_start[$1]}" ]] || st_start[$1]="${st_at[$1]}"
  case "$2" in
    done) symbol="✓" color="$c_ok" ;;
    failed) symbol="✗" color="$c_err" ;;
    *) symbol="–" color="$c_dim" ;;
  esac
  clear_line
  printf '  %s%s%s %s%s%s %s+%s%s  %s%s\n' "$color" "$symbol" "$c_off" "$c_bold" "$(pad "${labels[$1]}" 22)" "$c_off" \
    "$c_dim" "$(clock $((st_at[$1] - t0)))" "$c_off" "$3" "$shown"
  render
}

live_line() {
  [[ -n "$live" ]] || return 0
  local elapsed=$(($(date +%s) - st_start[current]))
  printf '\r\033[K  %s%s%s %s %s%s%s  %s' "$c_run" "${spinner[elapsed % 10]}" "$c_off" "$(pad "${labels[current]}" 22)" \
    "$c_dim" "$(clock "$elapsed")" "$c_off" "${observation:0:70}"
}

summary() {
  local total=$((${finished:-$(date +%s)} - t0)) i
  echo
  case "$result" in
    passed) printf '%s%s✓ Selbstheilung erfolgreich in %s%s\n' "$c_bold" "$c_ok" "$(clock "$total")" "$c_off" ;;
    checked) printf '%s%s✓ Vorprüfung bestanden%s\n' "$c_bold" "$c_ok" "$c_off" ;;
    *) printf '%s%s✗ Playbook %s nach %s%s\n' "$c_bold" "$c_err" "$([[ $result == aborted ]] && echo abgebrochen || echo gescheitert)" "$(clock "$total")" "$c_off" ;;
  esac
  for i in "${!keys[@]}"; do
    [[ -n "${st_at[i]}" ]] || continue
    printf '  %s+%s%s  %s %s(Dauer %s)%s %s\n' "$c_dim" "$(clock $((st_at[i] - t0)))" "$c_off" "$(pad "${labels[i]}" 22)" \
      "$c_dim" "$(clock $((st_at[i] - st_start[i])))" "$c_off" "${st_detail[i]}"
  done
}

write_report() {
  [[ -n "$report" ]] || return 0
  status_json | jq . >"$report"
  log "Bericht: $report"
}

conclude() {
  result="$1"; finished="$(date +%s)"; observation=""
  render
  summary
  write_report
}

fail() {
  [[ "${st_state[current]}" != running ]] || finish "$current" failed "$*"
  conclude failed
  [[ "$command" == "run" && -n "${triggered:-}" ]] && echo "$reset_hint" >&2
  exit 1
}

trap '[[ "${st_state[current]}" != running ]] || finish "$current" failed "abgebrochen"; conclude aborted; [[ -n "${triggered:-}" ]] && echo "$reset_hint" >&2; exit 130' INT TERM

# Wartet, bis die Prüffunktion erfolgreich ist; 1 nach Ablauf der Zeitgrenze. Prüffunktionen setzen observation.
wait_for() {
  local stage="$1" limit="$2" check="$3" deadline i
  begin "$stage"
  deadline=$(($(date +%s) + limit))
  [[ -n "$live" ]] || log "Warte auf: ${labels[stage]} (höchstens $((limit / 60)) min)"
  while ! "$check"; do
    (($(date +%s) < deadline)) || { observation="Zeitgrenze $((limit / 60)) min erreicht"; return 1; }
    render
    for ((i = 0; i < poll; i++)); do live_line; sleep 1; done
  done
}

# --- Abfragen ------------------------------------------------------------------------------

http_code() { curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$1" || true; }

deploy_field() { kubectl -n "$namespace" get deployment "$deployment" -o jsonpath="{.$1}" 2>/dev/null || true; }

restart_annotation() { deploy_field 'spec.template.metadata.annotations.ops\.mmk/action-id'; }

# Alarminstanzen der Regel; Azure liefert alertRule als Ressourcen-ID, die mit dem Regelnamen endet.
alerts() {
  local json
  json="$(az rest --method get --only-show-errors \
    --url "https://management.azure.com/subscriptions/$subscription/providers/Microsoft.AlertsManagement/alerts?api-version=2019-05-05-preview&timeRange=1d" \
    2>/dev/null)" || { observation="Azure-Abfrage fehlgeschlagen, neuer Versuch"; return 1; }
  jq -c --arg rule "$rule" '(.value // [])[] | .properties.essentials as $e
    | select(($e.alertRule // "") | ascii_downcase | endswith($rule))
    | {id: .id, condition: $e.monitorCondition, fired: $e.startDateTime, resolved: ($e.monitorConditionResolvedDateTime // null)}' <<<"$json"
}

portal_link() { [[ -z "$simulation" ]] || return 0; echo "https://portal.azure.com/#view/Microsoft_Azure_Monitoring_Alerts/AlertDetails.ReactView/alertId/$(jq -rn --arg id "$1" '$id | @uri')"; }

# --- Vorprüfung ----------------------------------------------------------------------------

preflight() {
  begin 0
  local tool missing=""
  for tool in kubectl gh az jq curl; do command -v "$tool" >/dev/null || missing+=" $tool"; done
  [[ -z "$missing" ]] || fail "Werkzeuge fehlen:$missing"
  [[ -n "${KUBECONFIG:-}" ]] || fail "KUBECONFIG auf den Staging-Cluster fehlt (platform/operations/foundation/operations.md)"
  gh auth status >/dev/null 2>&1 || fail "gh nicht angemeldet: gh auth login"

  subscription="${AZURE_SUBSCRIPTION_ID:-$(sed -n 's/^SUBSCRIPTION_ID=//p' "platform/scripts/bootstrap/$environment.env")}"
  [[ -n "$subscription" ]] || fail "Subscription unbekannt: AZURE_SUBSCRIPTION_ID setzen"
  az account show --subscription "$subscription" >/dev/null 2>&1 || fail "az nicht angemeldet oder kein Zugriff auf $subscription: az login"
  kubectl version --request-timeout=10s >/dev/null 2>&1 || fail "Cluster über KUBECONFIG nicht erreichbar"

  local desired ready app_host agent_host tls path active busy
  desired="$(deploy_field spec.replicas)"; ready="$(deploy_field status.readyReplicas)"
  [[ -n "$desired" ]] || fail "Deployment $namespace/$deployment nicht gefunden"
  [[ "$desired" -ge 1 && "${ready:-0}" -ge 1 ]] || fail "Ausgangszustand nicht gesund: $desired Replica(s) gewünscht, ${ready:-0} bereit. Erst $here/fault-scale-to-zero.sh reset --environment $environment"
  baseline_annotation="$(restart_annotation)"

  app_host="$(kubectl -n "$namespace" get ingress -o jsonpath='{.items[0].spec.rules[0].host}')"
  agent_host="$(kubectl -n "$agent_namespace" get ingress -o jsonpath='{.items[0].spec.rules[0].host}')"
  [[ -n "$app_host" && -n "$agent_host" ]] || fail "Ingress von AFFiNE oder Agent nicht gefunden"
  if [[ -z "$scheme" ]]; then
    tls="$(kubectl -n "$namespace" get ingress -o jsonpath='{.items[0].spec.tls[0].hosts[0]}')"
    scheme="$([[ -n "$tls" ]] && echo https || echo http)"
  fi
  path="$(sed -n '/^availability:/,/^[^ #]/s/^  path: *//p' platform/catalog/affine/app.yaml)"
  app_url="$scheme://$app_host${path:-/info}"
  agent_url="$scheme://$agent_host/healthz"

  [[ "$(http_code "$app_url")" == 200 ]] || fail "$app_url antwortet nicht mit 200"
  [[ "$(http_code "$agent_url")" == 200 ]] || fail "Agent nicht bereit: $agent_url antwortet nicht mit 200 (Worker-Heartbeat oder Datenbank, platform/operations/state/worker.md)"

  active="$(alerts)" || fail "Alarme der Subscription $subscription nicht abfragbar (Rolle Reader oder Monitoring Reader nötig)"
  [[ -z "$(jq -c 'select(.condition == "Fired")' <<<"$active")" ]] \
    || fail "Alarm $rule ist bereits aktiv; erst die Auflösung abwarten, sonst entsteht kein neues Fired-Ereignis"

  busy="$(for s in queued in_progress; do gh run list --workflow app-deploy.yml --status "$s" --limit 20 --json displayTitle,url; done \
    | jq -rs --arg t "affine/$environment" 'add // [] | .[] | select(.displayTitle | contains("app-deploy " + $t + " ")) | .url')"
  [[ -z "$busy" ]] || fail "app-deploy.yml für affine/$environment läuft bereits: $busy"

  if command -v psql >/dev/null && [[ -n "${OPS_DATABASE_URL:-}${PGHOST:-}" ]]; then db=1; else db=""; fi

  finish 0 "done" "AFFiNE $ready/$desired bereit, Agent bereit, kein aktiver Alarm${db:+, Datenbank verfügbar}"
}

# --- Prüffunktionen der Stufen -------------------------------------------------------------

app_down() { code="$(http_code "$app_url")"; observation="$app_url → HTTP $code"; [[ "$code" != 200 ]]; }

alert_fired() {
  local found
  found="$(alerts)" || return 1
  fired="$(jq -sc --arg since "$since" '[.[] | select(.condition == "Fired" and .fired >= $since)] | sort_by(.fired) | last // empty' <<<"$found")"
  observation="Verfügbarkeitstest läuft alle 5 min, noch kein Fired ($(date -u +%T))"
  [[ -n "$fired" ]]
}

alert_resolved() {
  local found
  found="$(alerts)" || return 1
  observation="Alarm noch aktiv, Auflösung nach erfolgreichen Testläufen ($(date -u +%T))"
  [[ -n "$(jq -c --arg id "$(jq -r .id <<<"$fired")" 'select(.id == $id and .condition == "Resolved")' <<<"$found")" ]]
}

# Redeploy: app-deploy.yml mit einer operationId des Agenten (op-<uuid>) im run-name. Neustart: neue Annotation.
agent_repair() {
  run="$(gh run list --workflow app-deploy.yml --limit 30 --json databaseId,displayTitle,createdAt,url \
    | jq -c --arg since "$since" --arg t "affine/$environment" '[.[] | select(.createdAt >= $since)
        | select(.displayTitle | startswith("app-deploy " + $t + " op=op-"))] | sort_by(.createdAt) | first // empty')"
  observation="Agent untersucht: Soll-/Istvergleich, Logs, Events ($(date -u +%T))"
  if [[ -n "$run" ]]; then repair="redeploy"; return 0; fi
  annotation="$(restart_annotation)"
  if [[ -n "$annotation" && "$annotation" != "$baseline_annotation" ]]; then repair="restart"; return 0; fi
  return 1
}

deploy_done() {
  local status
  status="$(gh run view "$run_id" --json status,conclusion,jobs -q '[.status, .conclusion,
    ([.jobs[] | select(.status == "in_progress") | .steps[]? | select(.status == "in_progress") | .name] | first // "")] | join("\u001f")' 2>/dev/null || true)"
  # Trennzeichen ohne Leerraum, damit ein leeres conclusion nicht entfällt
  IFS=$'\x1f' read -r run_status conclusion step <<<"$status" || true
  observation="Lauf ${run_status:-unbekannt}${step:+: $step}"
  [[ "$run_status" == "completed" ]]
}

app_healthy() {
  ready="$(deploy_field status.readyReplicas)"
  code="$(http_code "$app_url")"
  observation="${ready:-0} Replica(s) bereit, $app_url → HTTP $code"
  [[ "${ready:-0}" -ge 1 && "$code" == 200 ]]
}

psql_query() { psql ${OPS_DATABASE_URL:+"$OPS_DATABASE_URL"} -XAtq -v ON_ERROR_STOP=1 "$@"; }

# Auftrag zur Operation des Agenten; endet bei resolved, failed oder needs_attention.
job_finished() {
  local sql="select j.id, j.state, coalesce(j.state_reason, '') from operations o join jobs j on j.id = o.job_id where o.id = :'op';"
  job="$(psql_query -F ' ' -v op="$operation_id" <<<"$sql" 2>/dev/null || true)"
  job_state="$(awk '{print $2}' <<<"$job")"
  observation="Auftrag ${job_state:-nicht gefunden}"
  [[ "$job_state" == "resolved" || "$job_state" == "failed" || "$job_state" == "needs_attention" ]]
}

# --- Ablauf --------------------------------------------------------------------------------

if [[ -n "$html_dir" ]]; then
  cp "$here/playbook-self-heal.html" "$html_dir/index.html"
  render
  echo "Live-Ansicht: file://$html_dir/index.html"
  if [[ -n "$live" ]] && command -v open >/dev/null; then open "$html_dir/index.html"; fi
fi

printf '\n%sPlaybook Selbstheilung · affine/%s%s\n' "$c_bold" "$environment" "$c_off"
if [[ -n "$simulation" ]]; then
  printf '%s%sSIMULATION%s %s– simulierte Systeme, kein Eingriff in staging%s\n' "$c_bold" "$c_run" "$c_off" "$c_dim" "$c_off"
fi
echo
preflight
printf '  %sApp%s    %s\n  %sAgent%s  %s\n  %sAlarm%s  %s\n\n' "$c_dim" "$c_off" "$app_url" "$c_dim" "$c_off" "$agent_url" "$c_dim" "$c_off" "$rule"
if [[ "$command" == "check" ]]; then
  conclude checked
  exit 0
fi

# 60 s Spielraum für Uhrabweichungen zu Azure und GitHub
since="$(jq -nr --argjson e "$(($(date +%s) - 60))" '$e | todate')"

begin 1
trigger_output="$("$here/fault-scale-to-zero.sh" trigger --environment "$environment" 2>&1)" || { echo "$trigger_output" >&2; fail "fault-scale-to-zero.sh trigger gescheitert"; }
triggered=1
finish 1 "done" "$(head -n 1 <<<"$trigger_output") → 0 Replicas"

wait_for 2 "$limit_down" app_down || fail "$app_url antwortet weiter mit 200"
finish 2 "done" "$app_url → HTTP $code"

wait_for 3 "$limit_fired" alert_fired \
  || fail "kein Fired für $rule; Testergebnisse: Application Insights → Availability (platform/operations/foundation/monitoring.md)"
finish 3 "done" "$rule Fired um $(jq -r '.fired[11:19]' <<<"$fired") UTC" "$(portal_link "$(jq -r .id <<<"$fired")")"

wait_for 4 "$limit_repair" agent_repair \
  || fail "keine Reparatur des Agenten; Logs: kubectl -n $agent_namespace logs deploy/operations-agent-worker --since=30m"
if [[ "$repair" == "redeploy" ]]; then
  run_id="$(jq -r .databaseId <<<"$run")"
  operation_id="$(jq -r .displayTitle <<<"$run" | sed -n 's/.* op=\([^ ]*\).*/\1/p')"
  finish 4 "done" "Sollzustand wiederherstellen: app-deploy.yml ($operation_id)" "$(jq -r .url <<<"$run")"

  wait_for 5 "$limit_deploy" deploy_done || fail "Deploy-Lauf $run_id nicht abgeschlossen"
  [[ "$conclusion" == "success" ]] || fail "Deploy-Lauf $run_id endete mit '$conclusion': gh run view $run_id --log-failed"
  finish 5 "done" "Rollout und Smoke-Test erfolgreich" "$(jq -r .url <<<"$run")"
else
  finish 4 "done" "Neustart (ops.mmk/action-id=$annotation)"
  finish 5 "skipped" "kein Deploy-Lauf, Agent hat neu gestartet"
  if [[ -n "${db:-}" ]]; then
    operation_id="$(psql_query -v act="$annotation" <<<"select operation_id from actions where id = :'act';" 2>/dev/null || true)"
  fi
fi

wait_for 6 "$limit_healthy" app_healthy || fail "AFFiNE nicht wieder erreichbar (${ready:-0} Replica(s) bereit)"
finish 6 "done" "${ready} Replica(s) bereit, $app_url → HTTP 200"

wait_for 7 "$limit_resolved" alert_resolved || fail "Alarm $(jq -r .id <<<"$fired" | sed 's|.*/||') nicht aufgelöst"
finish 7 "done" "Alarm $(jq -r .id <<<"$fired" | sed 's|.*/||') Resolved" "$(portal_link "$(jq -r .id <<<"$fired")")"

if [[ -n "${db:-}" && -n "${operation_id:-}" ]]; then
  wait_for 8 "$limit_job" job_finished || fail "Auftrag zur Operation $operation_id nicht abgeschlossen: ${job:-nicht gefunden}"
  [[ "$job_state" == "resolved" ]] || fail "Auftrag endete mit $job_state: $job"
  finish 8 "done" "Auftrag $(awk '{print $1}' <<<"$job") resolved (ops runs show)"
else
  finish 8 "skipped" "nicht geprüft: psql und PG*-Variablen fehlen, Nachweis per ops runs show"
fi

conclude passed
