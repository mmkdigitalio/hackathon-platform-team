"""Gemeinsame Operations-Sperre je App und Umgebung (E9).

Agent und Deploy-Workflows verwenden dieselbe Tabelle direkt in PostgreSQL, ohne
laufende Agenten-API. Jede Operation läuft unter einer stabilen ``operationId``;
Sperrbesitz (Besitzer + Sperrversion) ist davon getrennt.
"""

import hashlib
import time
import uuid
from collections.abc import Callable
from datetime import datetime

import psycopg
from psycopg.types.json import Jsonb

from contracts.actions import OPEN_ACTION_STATUSES, WORKFLOW_LOCK_WAIT_SECONDS, LockHandle
from contracts.errors import LockNotHeld, TargetLocked, UnresolvedActions

DEFAULT_LOCK_TTL_SECONDS = 30 * 60
_OPEN = [s.value for s in OPEN_ACTION_STATUSES]


def new_operation_id() -> str:
    return f"op-{uuid.uuid4()}"


def ensure_operation(
    conn: psycopg.Connection,
    operation_id: str,
    *,
    app_id: str,
    environment: str,
    kind: str,
    owner_ref: str,
    job_id: str | None = None,
    git_sha: str | None = None,
    workflow_run: str | None = None,
    note: str | None = None,
) -> dict:
    conn.execute(
        """
        INSERT INTO operations (id, app_id, environment, kind, owner_ref, job_id, status,
                                git_sha, workflow_run, note)
        VALUES (%s, %s, %s, %s, %s, %s, 'active', %s, %s, %s)
        ON CONFLICT (id) DO NOTHING
        """,
        (operation_id, app_id, environment, kind, owner_ref, job_id, git_sha, workflow_run, note),
    )
    op = conn.execute("SELECT * FROM operations WHERE id = %s", (operation_id,)).fetchone()
    if (op["app_id"], op["environment"]) != (app_id, environment):
        raise ValueError(
            f"Operation {operation_id} gehört zu {op['app_id']}/{op['environment']}, "
            f"nicht zu {app_id}/{environment}"
        )
    return op


def _handle(row: dict) -> LockHandle:
    return LockHandle(
        app_id=row["app_id"],
        environment=row["environment"],
        operation_id=row["operation_id"],
        owner_ref=row["owner_ref"],
        lock_version=row["lock_version"],
        expires_at=row["expires_at"],
    )


def unresolved_actions(conn: psycopg.Connection, operation_id: str, app_id: str, environment: str) -> list[str]:
    rows = conn.execute(
        """
        SELECT id FROM actions
         WHERE operation_id = %s AND app_id = %s AND environment = %s
           AND mutating AND status = ANY(%s)
         ORDER BY created_at
        """,
        (operation_id, app_id, environment, _OPEN),
    ).fetchall()
    return [r["id"] for r in rows]


def try_acquire(
    conn: psycopg.Connection,
    *,
    app_id: str,
    environment: str,
    operation_id: str,
    owner_ref: str,
    kind: str,
    ttl_seconds: int = DEFAULT_LOCK_TTL_SECONDS,
    job_id: str | None = None,
    git_sha: str | None = None,
    workflow_run: str | None = None,
) -> LockHandle:
    """Ein Erwerbsversuch ohne Warten. Wirft TargetLocked oder UnresolvedActions."""
    with conn.transaction():
        ensure_operation(
            conn,
            operation_id,
            app_id=app_id,
            environment=environment,
            kind=kind,
            owner_ref=owner_ref,
            job_id=job_id,
            git_sha=git_sha,
            workflow_run=workflow_run,
        )
        row = conn.execute(
            """
            SELECT *, expires_at > now() AS valid FROM operation_locks
             WHERE app_id = %s AND environment = %s
            FOR UPDATE
            """,
            (app_id, environment),
        ).fetchone()
        if row is not None:
            if row["valid"]:
                if row["owner_ref"] == owner_ref and row["operation_id"] == operation_id:
                    return _reissue(conn, row, ttl_seconds)
                raise TargetLocked(app_id, environment, row["operation_id"], row["owner_ref"])
            # Abgelaufen: nur ohne ungeklärte externe Änderung neu vergeben.
            open_ids = unresolved_actions(conn, row["operation_id"], app_id, environment)
            if open_ids:
                raise UnresolvedActions(app_id, environment, row["operation_id"], open_ids)
            conn.execute(
                "DELETE FROM operation_locks WHERE app_id = %s AND environment = %s",
                (app_id, environment),
            )
        new = conn.execute(
            """
            INSERT INTO operation_locks (app_id, environment, operation_id, owner_ref,
                                         lock_version, expires_at)
            VALUES (%s, %s, %s, %s, nextval('operation_lock_version'),
                    now() + make_interval(secs => %s))
            ON CONFLICT (app_id, environment) DO NOTHING
            RETURNING *
            """,
            (app_id, environment, operation_id, owner_ref, ttl_seconds),
        ).fetchone()
        if new is None:
            # Gleichzeitiger Erwerb hat gewonnen.
            other = conn.execute(
                "SELECT * FROM operation_locks WHERE app_id = %s AND environment = %s",
                (app_id, environment),
            ).fetchone()
            raise TargetLocked(app_id, environment, other["operation_id"], other["owner_ref"])
        return _handle(new)


def acquire_waiting(
    connect: Callable[[], psycopg.Connection],
    *,
    wait_seconds: int = WORKFLOW_LOCK_WAIT_SECONDS,
    poll_seconds: float = 15.0,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    on_wait: Callable[[TargetLocked, float], None] | None = None,
    **kwargs,
) -> LockHandle:
    """Konfliktregel für Workflows: in festen Abständen bis ``wait_seconds`` versuchen."""
    deadline = clock() + wait_seconds
    while True:
        with connect() as conn:
            try:
                return try_acquire(conn, **kwargs)
            except TargetLocked as busy:
                remaining = deadline - clock()
                if remaining <= 0:
                    raise
                if on_wait:
                    on_wait(busy, remaining)
        sleep(min(poll_seconds, max(remaining, 0)))


def _reissue(conn, row, ttl_seconds) -> LockHandle:
    """Erneuter Erwerb durch denselben Besitzer, z. B. einen wiederaufgenommenen Auftrag.

    Die neue Sperrversion macht Handles eines veralteten Vorbesitzers ungültig.
    """
    new = conn.execute(
        """
        UPDATE operation_locks
           SET expires_at = now() + make_interval(secs => %s),
               lock_version = nextval('operation_lock_version')
         WHERE app_id = %s AND environment = %s
        RETURNING *
        """,
        (ttl_seconds, row["app_id"], row["environment"]),
    ).fetchone()
    return _handle(new)


def verify(conn: psycopg.Connection, handle: LockHandle) -> dict:
    """Besitzprüfung innerhalb einer Transaktion; wirft LockNotHeld."""
    row = conn.execute(
        """
        SELECT * FROM operation_locks
         WHERE app_id = %s AND environment = %s AND owner_ref = %s AND lock_version = %s
           AND expires_at > now()
        FOR SHARE
        """,
        (handle.app_id, handle.environment, handle.owner_ref, handle.lock_version),
    ).fetchone()
    if row is None:
        raise LockNotHeld(
            f"Sperre {handle.app_id}/{handle.environment} (Version {handle.lock_version}) "
            f"gehört nicht mehr {handle.owner_ref}"
        )
    return row


def check(conn: psycopg.Connection, handle: LockHandle) -> LockHandle:
    with conn.transaction():
        return _handle(verify(conn, handle))


def renew(conn: psycopg.Connection, handle: LockHandle, ttl_seconds: int = DEFAULT_LOCK_TTL_SECONDS) -> LockHandle:
    with conn.transaction():
        row = conn.execute(
            """
            UPDATE operation_locks SET expires_at = now() + make_interval(secs => %s)
             WHERE app_id = %s AND environment = %s AND owner_ref = %s AND lock_version = %s
               AND expires_at > now()
            RETURNING *
            """,
            (ttl_seconds, handle.app_id, handle.environment, handle.owner_ref, handle.lock_version),
        ).fetchone()
    if row is None:
        raise LockNotHeld(f"Erneuerung der Sperre {handle.app_id}/{handle.environment} abgewiesen")
    return _handle(row)


def release(
    conn: psycopg.Connection, handle: LockHandle, *, operation_status: str | None = None
) -> None:
    """Gibt nur die eigene Sperre frei; eine fremde bleibt bestehen."""
    with conn.transaction():
        row = conn.execute(
            """
            DELETE FROM operation_locks
             WHERE app_id = %s AND environment = %s AND owner_ref = %s AND lock_version = %s
            RETURNING operation_id
            """,
            (handle.app_id, handle.environment, handle.owner_ref, handle.lock_version),
        ).fetchone()
        if row is None:
            current = status(conn, handle.app_id, handle.environment)
            holder = f"Operation {current['operation_id']}" if current else "niemand"
            raise LockNotHeld(
                f"Freigabe abgewiesen: Sperre {handle.app_id}/{handle.environment} gehört {holder}"
            )
        if operation_status:
            finish_operation(conn, row["operation_id"], operation_status)


def finish_operation(conn: psycopg.Connection, operation_id: str, status: str) -> None:
    conn.execute(
        """
        UPDATE operations SET status = %s, finished_at = now()
         WHERE id = %s AND status = 'active'
        """,
        (status, operation_id),
    )


def status(conn: psycopg.Connection, app_id: str, environment: str) -> dict | None:
    return conn.execute(
        """
        SELECT l.*, l.expires_at > now() AS valid, o.kind, o.job_id, o.workflow_run
          FROM operation_locks l JOIN operations o ON o.id = l.operation_id
         WHERE l.app_id = %s AND l.environment = %s
        """,
        (app_id, environment),
    ).fetchone()


def release_owned_by(conn: psycopg.Connection, owner_ref: str) -> list[tuple[str, str]]:
    """Gibt alle Sperren eines Besitzers frei, etwa am Auftragsende."""
    with conn.transaction():
        rows = conn.execute(
            "DELETE FROM operation_locks WHERE owner_ref = %s RETURNING app_id, environment",
            (owner_ref,),
        ).fetchall()
    return [(r["app_id"], r["environment"]) for r in rows]


def renew_owned_by(conn: psycopg.Connection, owner_ref: str, ttl_seconds: int) -> int:
    with conn.transaction():
        cur = conn.execute(
            """
            UPDATE operation_locks SET expires_at = now() + make_interval(secs => %s)
             WHERE owner_ref = %s AND expires_at > now()
            """,
            (ttl_seconds, owner_ref),
        )
        return cur.rowcount


def backfill_break_glass(
    conn: psycopg.Connection,
    *,
    app_id: str,
    environment: str,
    git_sha: str,
    workflow_run: str,
    at: datetime,
    outcome: str = "succeeded",
    note: str | None = None,
) -> str:
    """Trägt ein Break-glass-Deployment nachträglich ins Aktionsprotokoll ein (idempotent)."""
    digest = hashlib.sha256(f"{app_id}|{environment}|{workflow_run}".encode()).hexdigest()[:16]
    operation_id = f"break-glass-{digest}"
    text = "break-glass" + (f": {note}" if note else "")
    with conn.transaction():
        conn.execute(
            """
            INSERT INTO operations (id, app_id, environment, kind, owner_ref, status, note,
                                    git_sha, workflow_run, started_at, finished_at)
            VALUES (%s, %s, %s, 'break-glass', %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO NOTHING
            """,
            (operation_id, app_id, environment, f"workflow:{workflow_run}",
             "completed" if outcome == "succeeded" else "failed", text, git_sha, workflow_run, at, at),
        )
        conn.execute(
            """
            INSERT INTO actions (id, operation_id, app_id, environment, kind, target, mutating,
                                 status, status_reason, external_refs, detail,
                                 created_at, updated_at, finished_at)
            VALUES (%s, %s, %s, %s, 'deploy', %s, true, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO NOTHING
            """,
            (f"{operation_id}-deploy", operation_id, app_id, environment,
             f"{app_id}/{environment}", outcome, text,
             Jsonb({"workflowRun": workflow_run}),
             Jsonb({"gitSha": git_sha, "breakGlass": True}), at, at, at),
        )
    return operation_id
