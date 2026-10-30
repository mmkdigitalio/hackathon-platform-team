"""Aktionsprotokoll: jede externe Maßnahme wird vor dem Aufruf gespeichert.

Schreibzugriffe brauchen einen gültigen Besitz: ``JobLease`` für den Agenten,
``LockHandle`` für Deploy-Workflows. Ändernde Agentenaktionen verlangen zusätzlich die
Ziel-Sperre der eigenen Operation; ein Workflow-Dispatch verlangt das Gegenteil.
"""

import uuid

import psycopg
from psycopg.types.json import Jsonb

from contracts.actions import (
    OPEN_ACTION_STATUSES,
    WORKFLOW_DISPATCH,
    ActionSnapshot,
    ActionStatus,
    LockHandle,
)
from contracts.errors import LockNotHeld, NestedTargetLock

from . import locks
from .jobs import JobLease

Guard = JobLease | LockHandle

_NEXT = {
    ActionStatus.PLANNED: {ActionStatus.STARTED, ActionStatus.SUCCEEDED, ActionStatus.FAILED, ActionStatus.UNCLEAR},
    ActionStatus.STARTED: {ActionStatus.SUCCEEDED, ActionStatus.FAILED, ActionStatus.UNCLEAR},
    ActionStatus.UNCLEAR: {ActionStatus.STARTED, ActionStatus.SUCCEEDED, ActionStatus.FAILED},
    ActionStatus.SUCCEEDED: set(),
    ActionStatus.FAILED: set(),
}
_FINAL = {ActionStatus.SUCCEEDED, ActionStatus.FAILED}


def _verify_guard(conn: psycopg.Connection, guard: Guard) -> None:
    if isinstance(guard, JobLease):
        guard.verify(conn)
    else:
        locks.verify(conn, guard)


def record_action(
    conn: psycopg.Connection,
    guard: Guard,
    *,
    operation_id: str,
    kind: str,
    target: str,
    mutating: bool = True,
    correlation: dict[str, str] | None = None,
    detail: dict | None = None,
    action_id: str | None = None,
) -> ActionSnapshot:
    """Speichert eine Aktion mit Status planned."""
    action_id = action_id or f"act-{uuid.uuid4()}"
    with conn.transaction():
        _verify_guard(conn, guard)
        op = conn.execute("SELECT * FROM operations WHERE id = %s", (operation_id,)).fetchone()
        if op is None:
            raise ValueError(f"Operation {operation_id} existiert nicht")
        app_id, environment = op["app_id"], op["environment"]
        job_id = None
        if isinstance(guard, JobLease):
            job_id = guard.job_id
            if mutating:
                held = locks.status(conn, app_id, environment)
                holds_target = bool(
                    held
                    and held["valid"]
                    and held["operation_id"] == operation_id
                    and held["owner_ref"] == guard.lock_owner_ref
                )
                if kind == WORKFLOW_DISPATCH and holds_target:
                    raise NestedTargetLock(
                        f"Ziel-Sperre {app_id}/{environment} vor dem Workflow-Dispatch freigeben; "
                        "der Workflow erwirbt sie selbst"
                    )
                if kind != WORKFLOW_DISPATCH and not holds_target:
                    raise LockNotHeld(f"ändernde Aktion {kind} ohne Ziel-Sperre {app_id}/{environment}")
        elif (guard.app_id, guard.environment) != (app_id, environment):
            raise LockNotHeld(f"Sperre {guard.app_id}/{guard.environment} passt nicht zu Operation {operation_id}")
        existing = conn.execute("SELECT * FROM actions WHERE id = %s", (action_id,)).fetchone()
        if existing:
            if existing["operation_id"] != operation_id or existing["kind"] != kind:
                raise ValueError(f"actionId {action_id} ist bereits anders vergeben")
            return snapshot(existing)
        row = conn.execute(
            """
            INSERT INTO actions (id, operation_id, job_id, app_id, environment, kind, target,
                                 mutating, status, correlation, detail)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'planned', %s, %s)
            RETURNING *
            """,
            (action_id, operation_id, job_id, app_id, environment, kind, target, mutating,
             Jsonb(correlation or {}), Jsonb(detail or {})),
        ).fetchone()
    return snapshot(row)


def update_status(
    conn: psycopg.Connection,
    guard: Guard | None,
    action_id: str,
    status: ActionStatus | str,
    *,
    reason: str | None = None,
    external_refs: dict[str, str] | None = None,
) -> ActionSnapshot:
    """Ergänzt Status und externe Referenzen unter derselben actionId.

    ``guard=None`` ist nur für die Klärung durch Menschen gedacht (``ops actions resolve``).
    """
    status = ActionStatus(status)
    with conn.transaction():
        if guard is not None:
            _verify_guard(conn, guard)
        row = conn.execute("SELECT * FROM actions WHERE id = %s FOR UPDATE", (action_id,)).fetchone()
        if row is None:
            raise ValueError(f"Aktion {action_id} existiert nicht")
        if isinstance(guard, LockHandle) and row["operation_id"] != guard.operation_id:
            raise LockNotHeld(f"Aktion {action_id} gehört nicht zu Operation {guard.operation_id}")
        if isinstance(guard, JobLease) and str(row["job_id"]) != guard.job_id:
            raise LockNotHeld(f"Aktion {action_id} gehört nicht zu Auftrag {guard.job_id}")
        current = ActionStatus(row["status"])
        if status != current and status not in _NEXT[current]:
            raise ValueError(f"Aktion {action_id}: {current} -> {status} nicht zulässig")
        row = conn.execute(
            """
            UPDATE actions
               SET status = %s, status_reason = coalesce(%s, status_reason),
                   external_refs = external_refs || %s, updated_at = now(),
                   finished_at = CASE WHEN %s THEN now() ELSE finished_at END
             WHERE id = %s
            RETURNING *
            """,
            (status.value, reason, Jsonb({k: str(v) for k, v in (external_refs or {}).items()}),
             status in _FINAL, action_id),
        ).fetchone()
    return snapshot(row)


def snapshot(row: dict) -> ActionSnapshot:
    return ActionSnapshot(
        action_id=row["id"],
        operation_id=row["operation_id"],
        job_id=str(row["job_id"]) if row["job_id"] else None,
        app_id=row["app_id"],
        environment=row["environment"],
        kind=row["kind"],
        target=row["target"],
        mutating=row["mutating"],
        status=row["status"],
        correlation=row["correlation"],
        external_refs=row["external_refs"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def get_action(conn: psycopg.Connection, action_id: str) -> ActionSnapshot | None:
    row = conn.execute("SELECT * FROM actions WHERE id = %s", (action_id,)).fetchone()
    return snapshot(row) if row else None


def actions_for_operation(conn: psycopg.Connection, operation_id: str) -> list[ActionSnapshot]:
    rows = conn.execute(
        "SELECT * FROM actions WHERE operation_id = %s ORDER BY created_at", (operation_id,)
    ).fetchall()
    return [snapshot(r) for r in rows]


def open_actions_for_job(conn: psycopg.Connection, job_id: str) -> list[ActionSnapshot]:
    """Offene Aktionen, die der Auftrag selbst gespeichert hat.

    Aktionen eines dispatchten Workflows unter derselben operationId pflegt der Workflow;
    der Agent verfolgt sie über seine Dispatch-Aktion.
    """
    rows = conn.execute(
        """
        SELECT * FROM actions
         WHERE status = ANY(%(open)s) AND job_id = %(job)s
         ORDER BY created_at
        """,
        {"job": job_id, "open": [s.value for s in OPEN_ACTION_STATUSES]},
    ).fetchall()
    return [snapshot(r) for r in rows]
