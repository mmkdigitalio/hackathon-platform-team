"""CLI ``ops``: Migrationen, Operations-Sperre, Aktionen und Laufnachweise.

Greift direkt auf PostgreSQL zu (OPS_DATABASE_URL oder PGHOST/PGUSER/PGPASSWORD/
PGDATABASE/PGSSLMODE) und funktioniert ohne laufende Agenten-API.

Exit-Codes: 0 ok, 1 Fehler, 2 Aufruf, 3 Schema fehlt/veraltet, 4 Sperre belegt,
5 Datenbank nicht erreichbar, 6 ungeklärte Aktionen, 7 Sperre nicht im Besitz.
"""

import argparse
import json
import os
import sys
from datetime import UTC, datetime

from contracts.actions import WORKFLOW_LOCK_WAIT_SECONDS, ActionStatus, LockHandle
from contracts.errors import (
    DatabaseUnavailable,
    LockNotHeld,
    SchemaMissing,
    SchemaVersionMismatch,
    TargetLocked,
    UnresolvedActions,
)

from . import actions, db, locks, migrate, portal, runs

EXIT = {
    SchemaMissing: 3,
    SchemaVersionMismatch: 3,
    TargetLocked: 4,
    DatabaseUnavailable: 5,
    UnresolvedActions: 6,
    LockNotHeld: 7,
}


def _err(message: str) -> None:
    print(f"ops: {message}", file=sys.stderr)


def _github_output(**values) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            for key, value in values.items():
                f.write(f"{key}={value}\n")


def _summary(markdown: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(markdown + "\n")
    else:
        print(markdown, file=sys.stderr)


def default_owner() -> str | None:
    run_id = os.environ.get("GITHUB_RUN_ID")
    if run_id:
        repo = os.environ.get("GITHUB_REPOSITORY", "")
        return f"github-run:{repo}/{run_id}/{os.environ.get('GITHUB_RUN_ATTEMPT', '1')}"
    return None


def default_workflow_run() -> str | None:
    run_id = os.environ.get("GITHUB_RUN_ID")
    if run_id:
        server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
        return f"{server}/{os.environ.get('GITHUB_REPOSITORY', '')}/actions/runs/{run_id}"
    return None


def _connect_checked(args):
    conn = db.connect(args.database_url)
    try:
        db.check_schema(conn)
    except Exception:
        conn.close()
        raise
    return conn


def _handle_from(args) -> LockHandle:
    return LockHandle(
        app_id=args.app,
        environment=args.env,
        operation_id=args.operation_id or "",
        owner_ref=args.owner,
        lock_version=args.lock_version,
        expires_at=datetime.now(UTC),
    )


# --- Befehle -------------------------------------------------------------------------


def cmd_migrate(args) -> int:
    with db.connect(args.database_url) as conn:
        report = migrate.migrate(conn)
    if report.applied:
        print(f"Migrationen eingespielt: {report.applied}; Schema-Version {report.version}")
    else:
        print(f"Schema aktuell (Version {report.version}), keine Änderung")
    print(f"Checkpoint-Schema (LangGraph) Version {report.sdk_version}")
    return 0


def cmd_schema_version(args) -> int:
    from agent.persistence import SDK_SCHEMA_VERSION

    with db.connect(args.database_url) as conn:
        actual = db.current_version(conn)
        sdk_actual = None
        if conn.execute("SELECT to_regclass('checkpoint_migrations') AS t").fetchone()["t"] is not None:
            sdk_actual = conn.execute("SELECT COALESCE(MAX(v), -1) AS v FROM checkpoint_migrations").fetchone()["v"]
        print(json.dumps({
            "expected": migrate.expected_version(),
            "actual": actual,
            "sdkExpected": SDK_SCHEMA_VERSION,
            "sdkActual": sdk_actual,
        }))
        db.check_schema(conn, include_sdk=True)
    return 0


def cmd_lock_acquire(args) -> int:
    owner = args.owner
    try:
        conn = _connect_checked(args)
    except DatabaseUnavailable as exc:
        if not args.force:
            _err(f"{exc}. Abbruch vor jeder Zieländerung (Break-glass nur mit --force).")
            return 5
        return _break_glass(args, str(exc))
    conn.close()
    if args.force:
        print("Hinweis: PostgreSQL erreichbar, --force ist wirkungslos; normale Konfliktregel gilt.")

    operation_id = args.operation_id or locks.new_operation_id()

    def on_wait(busy: TargetLocked, remaining: float) -> None:
        print(
            f"Sperre {args.app}/{args.env} belegt durch Operation {busy.operation_id} "
            f"({busy.owner_ref}); warte noch höchstens {int(remaining)} s"
        )

    handle = locks.acquire_waiting(
        lambda: db.connect(args.database_url),
        wait_seconds=args.wait_timeout,
        poll_seconds=args.poll_interval,
        on_wait=on_wait,
        app_id=args.app,
        environment=args.env,
        operation_id=operation_id,
        owner_ref=owner,
        kind="deploy",
        ttl_seconds=args.ttl,
        git_sha=args.git_sha or os.environ.get("GITHUB_SHA"),
        workflow_run=args.workflow_run or default_workflow_run(),
    )
    print(
        f"Sperre {handle.app_id}/{handle.environment} erworben: Operation {handle.operation_id}, "
        f"Version {handle.lock_version}, gültig bis {handle.expires_at.isoformat()}"
    )
    _github_output(
        **{
            "operation-id": handle.operation_id,
            "lock-version": handle.lock_version,
            "owner": handle.owner_ref,
            "break-glass": "false",
        }
    )
    return 0


def _break_glass(args, problem: str) -> int:
    now = datetime.now(UTC).replace(microsecond=0).isoformat()
    git_sha = args.git_sha or os.environ.get("GITHUB_SHA", "unbekannt")
    run = args.workflow_run or default_workflow_run() or "unbekannt"
    _summary(
        "\n".join(
            [
                "## BREAK-GLASS: Deployment ohne Operations-Sperre",
                "",
                f"PostgreSQL war nicht erreichbar ({problem}). Das Deployment lief mit `--force` ohne Sperre (E9).",
                "",
                "| App | Umgebung | Git-SHA | Workflow-Lauf | Zeitpunkt |",
                "| --- | --- | --- | --- | --- |",
                f"| {args.app} | {args.env} | {git_sha} | {run} | {now} |",
                "",
                "Nach Wiederherstellung der Datenbank nachtragen:",
                "",
                "```",
                f"ops lock backfill --app {args.app} --env {args.env} --git-sha {git_sha} "
                f"--workflow-run {run} --at {now}",
                "```",
            ]
        )
    )
    print(f"WARNUNG: Break-glass – {args.app}/{args.env} läuft ohne Sperre.", file=sys.stderr)
    _github_output(**{"break-glass": "true", "operation-id": "", "lock-version": "", "owner": args.owner})
    return 0


def cmd_lock_renew(args) -> int:
    with _connect_checked(args) as conn:
        handle = locks.renew(conn, _handle_from(args), args.ttl)
    print(f"Sperre erneuert bis {handle.expires_at.isoformat()}")
    return 0


def cmd_lock_check(args) -> int:
    with _connect_checked(args) as conn:
        handle = locks.check(conn, _handle_from(args))
    print(f"Sperre gültig: Operation {handle.operation_id}, bis {handle.expires_at.isoformat()}")
    return 0


def cmd_lock_release(args) -> int:
    with _connect_checked(args) as conn:
        locks.release(conn, _handle_from(args), operation_status=args.status)
    print(f"Sperre {args.app}/{args.env} freigegeben")
    return 0


def cmd_lock_status(args) -> int:
    with _connect_checked(args) as conn:
        row = locks.status(conn, args.app, args.env)
    print(json.dumps(row, default=str, indent=2) if row else json.dumps({"locked": False}))
    return 0


def cmd_lock_backfill(args) -> int:
    at = datetime.fromisoformat(args.at)
    with _connect_checked(args) as conn:
        operation_id = locks.backfill_break_glass(
            conn,
            app_id=args.app,
            environment=args.env,
            git_sha=args.git_sha,
            workflow_run=args.workflow_run,
            at=at,
            outcome=args.outcome,
            note=args.note,
        )
    print(f"Break-glass-Deployment nachgetragen als Operation {operation_id}")
    return 0


def _pairs(values: list[str] | None) -> dict[str, str]:
    out = {}
    for item in values or []:
        key, sep, value = item.partition("=")
        if not sep or not key:
            raise ValueError(f"erwartet Schlüssel=Wert, nicht {item!r}")
        out[key] = value
    return out


def cmd_actions_record(args) -> int:
    correlation = _pairs(args.correlation)
    if args.agent_action_id:
        correlation["agentActionId"] = args.agent_action_id
    with _connect_checked(args) as conn:
        action_id = actions.record_action(
            conn,
            _handle_from(args),
            operation_id=args.operation_id,
            kind=args.kind,
            target=args.target or f"{args.app}/{args.env}",
            correlation=correlation,
            action_id=args.action_id,
            mutating=not args.read_only,
        ).action_id
    print(action_id)
    _github_output(**{"action-id": action_id})
    return 0


def cmd_actions_update(args) -> int:
    detail = None
    if args.result_file:
        with open(args.result_file, encoding="utf-8") as f:
            detail = {"result": json.load(f)}
    with _connect_checked(args) as conn:
        snap = actions.update_status(
            conn, _handle_from(args), args.action_id, args.status, reason=args.reason,
            external_refs=_pairs(args.external_ref), detail=detail,
        )
    print(f"Aktion {snap.action_id}: {snap.status}")
    return 0


def cmd_actions_resolve(args) -> int:
    with _connect_checked(args) as conn:
        snap = actions.update_status(
            conn, None, args.action_id, args.status, reason=f"geklärt durch {args.by}: {args.reason}"
        )
    print(f"Aktion {snap.action_id} geklärt: {snap.status}")
    return 0


def cmd_runs_show(args) -> int:
    with _connect_checked(args) as conn:
        run = runs.get_run(conn, args.job_id)
    if run is None:
        _err(f"Auftrag {args.job_id} nicht gefunden")
        return 1
    print(json.dumps(run, indent=2, ensure_ascii=False))
    return 0


def cmd_portal_code(args) -> int:
    with _connect_checked(args) as conn:
        if args.portal_command == "create":
            code_id, code = portal.create_code(conn, args.label, args.expires_days)
            print(json.dumps({"id": code_id, "code": code}, ensure_ascii=False))
        elif args.portal_command == "list":
            print(json.dumps(portal.list_codes(conn), default=str, ensure_ascii=False, indent=2))
        elif not portal.revoke_code(conn, args.id):
            _err("Zugangscode nicht gefunden")
            return 1
        else:
            print(f"Zugangscode {args.id} widerrufen")
    return 0


def cmd_events_validate(args) -> int:
    from .event_schema import validate_files

    failures = validate_files(args.files, expect_invalid=args.expect_invalid)
    for line in failures:
        _err(line)
    return 1 if failures else 0


def cmd_health_check(args) -> int:
    from .health_check import run_health_check

    return run_health_check(args)


# --- Parser --------------------------------------------------------------------------


def _lock_args(p, *, owner_required=True, version=False, operation=False):
    p.add_argument("--app", required=True)
    p.add_argument("--env", required=True)
    owner = default_owner()
    p.add_argument("--owner", default=owner, required=owner is None and owner_required,
                   help="Besitzerreferenz; Standard im Workflow github-run:<repo>/<run>/<attempt>")
    if version:
        p.add_argument("--lock-version", type=int, required=True)
    if operation:
        p.add_argument("--operation-id")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ops", description="Operations-Client (Strang C)")
    parser.add_argument("--database-url", default=None, help="Standard: OPS_DATABASE_URL bzw. PG*-Variablen")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("migrate", help="Schema anlegen/aktualisieren (idempotent)").set_defaults(fn=cmd_migrate)
    sub.add_parser("schema-version", help="erwartete und vorhandene Version").set_defaults(fn=cmd_schema_version)

    codes = sub.add_parser("portal-code", help="Chat-Zugangscodes").add_subparsers(dest="portal_command", required=True)
    p = codes.add_parser("create", help="Code einmalig ausgeben")
    p.add_argument("--label", required=True)
    p.add_argument("--expires-days", type=int)
    p.set_defaults(fn=cmd_portal_code)
    codes.add_parser("list", help="Codes ohne Geheimnisse auflisten").set_defaults(fn=cmd_portal_code)
    p = codes.add_parser("revoke", help="Code widerrufen")
    p.add_argument("id")
    p.set_defaults(fn=cmd_portal_code)

    lock = sub.add_parser("lock", help="Operations-Sperre").add_subparsers(dest="lock_command", required=True)
    p = lock.add_parser("acquire", help="Sperre erwerben (Workflow-Konfliktregel)")
    _lock_args(p, operation=True)
    p.add_argument("--wait-timeout", type=int, default=WORKFLOW_LOCK_WAIT_SECONDS)
    p.add_argument("--poll-interval", type=float, default=15.0)
    p.add_argument("--ttl", type=int, default=locks.DEFAULT_LOCK_TTL_SECONDS)
    p.add_argument("--git-sha")
    p.add_argument("--workflow-run")
    p.add_argument("--force", action="store_true",
                   help="Break-glass: nur wirksam, wenn PostgreSQL nicht erreichbar ist (E9)")
    p.set_defaults(fn=cmd_lock_acquire)

    for name, fn, help_text in (
        ("renew", cmd_lock_renew, "Sperre erneuern"),
        ("check", cmd_lock_check, "Besitz prüfen"),
        ("release", cmd_lock_release, "Sperre freigeben"),
    ):
        p = lock.add_parser(name, help=help_text)
        _lock_args(p, version=True, operation=True)
        if name == "renew":
            p.add_argument("--ttl", type=int, default=locks.DEFAULT_LOCK_TTL_SECONDS)
        if name == "release":
            # Ohne Angabe bleibt die Operation unverändert, z. B. die des Agenten bei einem Dispatch.
            p.add_argument("--status", choices=["completed", "failed"], help="Operation abschließen")
        p.set_defaults(fn=fn)

    p = lock.add_parser("status", help="aktuellen Sperrbesitz anzeigen")
    p.add_argument("--app", required=True)
    p.add_argument("--env", required=True)
    p.set_defaults(fn=cmd_lock_status)

    p = lock.add_parser("backfill", help="Break-glass-Deployment nachtragen")
    p.add_argument("--app", required=True)
    p.add_argument("--env", required=True)
    p.add_argument("--git-sha", required=True)
    p.add_argument("--workflow-run", required=True)
    p.add_argument("--at", required=True, help="Zeitpunkt aus dem Vermerk (ISO 8601)")
    p.add_argument("--outcome", choices=["succeeded", "failed", "unclear"], default="succeeded")
    p.add_argument("--note")
    p.set_defaults(fn=cmd_lock_backfill)

    act = sub.add_parser("actions", help="Aktionsprotokoll").add_subparsers(dest="actions_command", required=True)
    p = act.add_parser("record", help="Aktion vor dem externen Aufruf speichern")
    _lock_args(p, version=True)
    p.add_argument("--operation-id", required=True)
    p.add_argument("--kind", required=True)
    p.add_argument("--target")
    p.add_argument("--action-id")
    p.add_argument("--agent-action-id", help="actionId des Agenten aus dem Dispatch (Korrelation)")
    p.add_argument("--correlation", action="append", metavar="SCHLÜSSEL=WERT")
    p.add_argument("--read-only", action="store_true")
    p.set_defaults(fn=cmd_actions_record)

    p = act.add_parser("update", help="Status/externe Referenz ergänzen")
    _lock_args(p, version=True)
    p.add_argument("--operation-id", required=True)
    p.add_argument("--action-id", required=True)
    p.add_argument("--status", required=True, choices=[s.value for s in ActionStatus if s != ActionStatus.PLANNED])
    p.add_argument("--reason")
    p.add_argument("--external-ref", action="append", metavar="SCHLÜSSEL=WERT", help="z. B. runUrl=https://…")
    p.add_argument("--result-file", help="JSON-Datei, z. B. Verifikationsergebnis; landet unter detail.result")
    p.set_defaults(fn=cmd_actions_update)

    p = act.add_parser("resolve", help="ungeklärte Aktion nach Prüfung abschließen")
    p.add_argument("--action-id", required=True)
    p.add_argument("--status", required=True, choices=["succeeded", "failed"])
    p.add_argument("--reason", required=True)
    p.add_argument("--by", default=os.environ.get("USER") or os.environ.get("GITHUB_ACTOR") or "unbekannt")
    p.set_defaults(fn=cmd_actions_resolve)

    p = sub.add_parser("runs", help="Laufnachweise").add_subparsers(dest="runs_command", required=True)
    show = p.add_parser("show", help="Laufnachweis eines Auftrags als JSON")
    show.add_argument("job_id")
    show.set_defaults(fn=cmd_runs_show)

    p = sub.add_parser("events", help="Ereignisvertrag").add_subparsers(dest="events_command", required=True)
    val = p.add_parser("validate", help="JSON-Dateien gegen platform/schemas/events/ prüfen")
    val.add_argument("files", nargs="+")
    val.add_argument("--expect-invalid", action="store_true", help="Dateien müssen abgelehnt werden")
    val.set_defaults(fn=cmd_events_validate)

    p = sub.add_parser("health-check", help="deterministische Gesundheitsprüfung aller Katalog-Apps")
    p.add_argument("--env", default="staging")
    p.add_argument("--base-domain", required=True)
    p.add_argument("--api-host", required=True, help="operationsApiHost für Wartungsaufträge")
    p.add_argument("--scheme", default="https", choices=["https", "http"])
    p.add_argument("--timeout", type=float, default=10.0)
    p.add_argument("--dry-run", action="store_true", help="keinen Wartungsauftrag senden")
    p.set_defaults(fn=cmd_health_check)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except tuple(EXIT) as exc:
        _err(str(exc))
        return EXIT[type(exc)]
    except ValueError as exc:
        _err(str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
