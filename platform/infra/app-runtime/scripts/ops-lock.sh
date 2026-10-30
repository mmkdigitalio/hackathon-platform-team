#!/usr/bin/env bash
# Einziger Berührungspunkt des Deploy-Workflows mit C's Operations-Client `ops`.
# Die Aufrufsyntax von `ops` ist hier gekapselt; ändert C die CLI, wird nur diese Datei angepasst.
#
#   ops-lock.sh acquire        Sperre erwerben (max. 15 Minuten warten) oder Break-glass prüfen
#   ops-lock.sh renew-loop     Sperre im Hintergrund jede Minute erneuern
#   ops-lock.sh check          Besitz prüfen; Exit != 0 bei verlorenem Besitz
#   ops-lock.sh release        Sperre freigeben
#   ops-lock.sh record <status> [ergebnisdatei]   Aktion mit Lauf-URL protokollieren
#
# Umgebung: APP_ID, ENVIRONMENT, OPERATION_ID, ACTION_ID (optional), RUN_URL, FORCE (true|false),
#           PGHOST, PGUSER, PGPASSWORD, PGDATABASE, PGSSLMODE (aus den Foundation-Ausgaben).
set -euo pipefail

: "${APP_ID:?}" "${ENVIRONMENT:?}" "${OPERATION_ID:?}" "${RUN_URL:?}"
state_dir="${RUNNER_TEMP:-/tmp}/ops-lock-$APP_ID-$ENVIRONMENT"
mkdir -p "$state_dir"
summary="${GITHUB_STEP_SUMMARY:-/dev/stderr}"
target=(--app "$APP_ID" --env "$ENVIRONMENT" --operation-id "$OPERATION_ID")

break_glass() { [[ -f "$state_dir/break-glass" ]]; }

postgres_reachable() {
  PGCONNECT_TIMEOUT=10 psql -X -q -t -c 'select 1' >/dev/null 2>"$state_dir/pg-error"
}

case "${1:?Befehl fehlt}" in
  acquire)
    if ! postgres_reachable; then
      reason="$(head -c 300 "$state_dir/pg-error" | tr '\n' ' ')"
      if [[ "${FORCE:-false}" != "true" ]]; then
        echo "::error::PostgreSQL nicht erreichbar; ohne force beginnt keine Zieländerung. ${reason}"
        exit 1
      fi
      touch "$state_dir/break-glass"
      {
        echo "### ⚠️ Break-glass"
        echo ""
        echo "Deployment **ohne Operations-Sperre**, weil PostgreSQL nicht erreichbar war."
        echo ""
        echo "| App | Umgebung | operationId | Git-SHA | Lauf | Zeitpunkt | Grund |"
        echo "| --- | --- | --- | --- | --- | --- | --- |"
        echo "| $APP_ID | $ENVIRONMENT | \`$OPERATION_ID\` | \`${GITHUB_SHA:-}\` | $RUN_URL | $(date -u +%FT%TZ) | ${reason//|/ } |"
        echo ""
        echo "Nachtrag nach Wiederherstellung: \`ops lock backfill\` laut platform/operations/apps/README.md."
      } >> "$summary"
      echo "::warning::Break-glass: Lauf ohne Sperre, Vermerk in der Zusammenfassung"
      exit 0
    fi
    if [[ "${FORCE:-false}" == "true" ]]; then
      echo "::notice::force abgelehnt: PostgreSQL ist erreichbar, es gilt die normale Sperre"
    fi
    if ! ops lock acquire "${target[@]}" --owner "$RUN_URL" --wait-seconds 900 2>"$state_dir/acquire-error"; then
      cat "$state_dir/acquire-error" >&2
      holder="$(grep -o 'operation[^ ]*[[:space:]]*[A-Za-z0-9_.:-]*' "$state_dir/acquire-error" | head -1 || true)"
      echo "::error::Sperre für $APP_ID/$ENVIRONMENT nach 15 Minuten nicht erhalten: ${holder:-siehe Log}"
      echo "Abbruch ohne Zieländerung: Sperre für $APP_ID/$ENVIRONMENT belegt (${holder:-siehe Log})." >> "$summary"
      exit 1
    fi
    touch "$state_dir/acquired"
    ;;
  renew-loop)
    break_glass && exit 0
    (
      while sleep 60; do
        ops lock renew "${target[@]}" || { touch "$state_dir/lost"; exit 1; }
      done
    ) >/dev/null 2>&1 &
    echo $! > "$state_dir/renew-pid"
    ;;
  check)
    break_glass && exit 0
    if [[ -f "$state_dir/lost" ]] || ! ops lock check "${target[@]}"; then
      echo "::error::Sperrbesitz für Operation $OPERATION_ID verloren; keine weitere Zieländerung"
      exit 1
    fi
    ;;
  release)
    [[ -f "$state_dir/renew-pid" ]] && kill "$(cat "$state_dir/renew-pid")" 2>/dev/null || true
    break_glass && exit 0
    [[ -f "$state_dir/acquired" ]] || exit 0
    ops lock release "${target[@]}"
    ;;
  record)
    break_glass && exit 0
    status="${2:?Status fehlt}"
    args=("${target[@]}" --kind deploy --status "$status" --external-ref "$RUN_URL")
    [[ -n "${ACTION_ID:-}" ]] && args+=(--action-id "$ACTION_ID")
    [[ -n "${3:-}" ]] && args+=(--result-file "$3")
    ops action record "${args[@]}"
    ;;
  *)
    echo "unbekannter Befehl: $1" >&2
    exit 2
    ;;
esac
