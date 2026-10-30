#!/usr/bin/env bash
# Einziger Berührungspunkt des Deploy-Workflows mit C's Operations-Client `ops`.
# Die Aufrufsyntax von `ops` ist hier gekapselt; ändert C die CLI, wird nur diese Datei angepasst.
#
#   ops-lock.sh acquire        Sperre erwerben (max. 15 Minuten warten) oder Break-glass prüfen
#   ops-lock.sh renew-loop     Sperre im Hintergrund jede Minute erneuern
#   ops-lock.sh check          Besitz prüfen; Exit != 0 bei verlorenem Besitz
#   ops-lock.sh release        Sperre freigeben; eigene Operation (ohne ACTION_ID) abschließen
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
# Sperrbesitz = Besitzer (Lauf-URL) + Sperrversion aus dem Erwerb (C: platform/operations/state/locks.md).
owned() { owned_args=(--owner "$RUN_URL" --lock-version "$(cat "$state_dir/lock-version")"); }

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
      at="$(date -u +%FT%TZ)"
      {
        echo "### ⚠️ Break-glass"
        echo ""
        echo "Deployment **ohne Operations-Sperre**, weil PostgreSQL nicht erreichbar war."
        echo ""
        echo "| App | Umgebung | operationId | Git-SHA | Lauf | Zeitpunkt | Grund |"
        echo "| --- | --- | --- | --- | --- | --- | --- |"
        echo "| $APP_ID | $ENVIRONMENT | \`$OPERATION_ID\` | \`${GITHUB_SHA:-}\` | $RUN_URL | $at | ${reason//|/ } |"
        echo ""
        echo "Nachtrag nach Wiederherstellung (Pflicht, Ergebnis des Laufs eintragen):"
        echo ""
        echo '```bash'
        echo "ops lock backfill --app $APP_ID --env $ENVIRONMENT --git-sha ${GITHUB_SHA:-<sha>} \\"
        echo "  --workflow-run $RUN_URL --at $at --outcome <succeeded|failed> --note \"operationId $OPERATION_ID\""
        echo '```'
      } >> "$summary"
      echo "::warning::Break-glass: Lauf ohne Sperre, Vermerk in der Zusammenfassung"
      exit 0
    fi
    if [[ "${FORCE:-false}" == "true" ]]; then
      echo "::notice::force abgelehnt: PostgreSQL ist erreichbar, es gilt die normale Sperre"
    fi
    # Konfliktregel: höchstens 15 Minuten warten. Ausgaben (lock-version) in eine eigene Datei.
    if ! GITHUB_OUTPUT="$state_dir/acquire.out" ops lock acquire "${target[@]}" --owner "$RUN_URL" \
        --wait-timeout 900 2>"$state_dir/acquire-error"; then
      cat "$state_dir/acquire-error" >&2
      holder="$(grep -o 'gesperrt durch Operation [^ ]*\|ungeklärte Aktionen [^;]*' "$state_dir/acquire-error" | head -1 || true)"
      echo "::error::Sperre für $APP_ID/$ENVIRONMENT nicht erhalten: ${holder:-siehe Log}"
      echo "Abbruch ohne Zieländerung: Sperre für $APP_ID/$ENVIRONMENT nicht erhalten (${holder:-siehe Log})." >> "$summary"
      exit 1
    fi
    sed -n 's/^lock-version=//p' "$state_dir/acquire.out" > "$state_dir/lock-version"
    touch "$state_dir/acquired"
    ;;
  renew-loop)
    break_glass && exit 0
    owned
    (
      while sleep 60; do
        ops lock renew "${target[@]}" "${owned_args[@]}" || { touch "$state_dir/lost"; exit 1; }
      done
    ) >/dev/null 2>&1 &
    echo $! > "$state_dir/renew-pid"
    ;;
  check)
    break_glass && exit 0
    owned
    if [[ -f "$state_dir/lost" ]] || ! ops lock check "${target[@]}" "${owned_args[@]}"; then
      echo "::error::Sperrbesitz für Operation $OPERATION_ID verloren; keine weitere Zieländerung"
      exit 1
    fi
    ;;
  release)
    [[ -f "$state_dir/renew-pid" ]] && kill "$(cat "$state_dir/renew-pid")" 2>/dev/null || true
    break_glass && exit 0
    [[ -f "$state_dir/acquired" ]] || exit 0
    owned
    # Ohne actionId gehört die Operation dem Lauf und wird mit der Sperre abgeschlossen;
    # bei einem Dispatch des Agenten schließt der Agent seine Operation selbst.
    finish=()
    if [[ -z "${ACTION_ID:-}" ]]; then
      [[ "$(cat "$state_dir/last-status" 2>/dev/null)" == "succeeded" ]] && finish=(--status completed) || finish=(--status failed)
    fi
    ops lock release "${target[@]}" "${owned_args[@]}" ${finish[@]+"${finish[@]}"}
    ;;
  record)
    break_glass && exit 0
    status="${2:?Status fehlt}"
    owned
    # Eigene Aktion des Laufs; die actionId des Agenten (Dispatch) ist nur Korrelation.
    if [[ ! -f "$state_dir/action-id" ]]; then
      action_id="act-deploy-${GITHUB_RUN_ID:-$(date +%s)}-${GITHUB_RUN_ATTEMPT:-1}-$APP_ID-$ENVIRONMENT"
      args=("${target[@]}" "${owned_args[@]}" --kind deploy --target "$APP_ID/$ENVIRONMENT" --action-id "$action_id")
      [[ -n "${ACTION_ID:-}" ]] && args+=(--agent-action-id "$ACTION_ID")
      GITHUB_OUTPUT=/dev/null ops actions record "${args[@]}" >/dev/null
      echo "$action_id" > "$state_dir/action-id"
    fi
    args=("${target[@]}" "${owned_args[@]}" --action-id "$(cat "$state_dir/action-id")" --status "$status"
          --external-ref "runUrl=$RUN_URL")
    [[ -n "${3:-}" ]] && args+=(--result-file "$3")
    ops actions update "${args[@]}"
    echo "$status" > "$state_dir/last-status"
    ;;
  *)
    echo "unbekannter Befehl: $1" >&2
    exit 2
    ;;
esac
