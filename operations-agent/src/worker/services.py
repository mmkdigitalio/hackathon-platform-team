"""C's Dienste für die Agentenausführung nach ``contracts.v0_1.OperationsServices``.

Jeder Aufruf läuft in einer eigenen Transaktion und prüft den Auftragsbesitz. Besitz- und
Sperrverlust erscheinen für D als ``OwnershipLost``.
"""

from collections.abc import Callable
from functools import wraps
from typing import Literal

import psycopg

from contracts import (
    ActionRecord,
    LockAcquisition,
    OperationsServices,
    OwnershipLost,
)
from contracts.actions import MUTATING_AGENT_KINDS, ActionSnapshot, LockHandle
from contracts.errors import LeaseLost, LockNotHeld, TargetLocked, TransitionRejected, UnresolvedActions
from contracts.states import JobState
from persistence import actions, jobs, locks
from persistence.db import conninfo
from persistence.jobs import JobLease

Connect = Callable[[], psycopg.Connection]


def _ownership(fn):
    """Übersetzt C's Besitzfehler in den Vertragsfehler ``OwnershipLost``."""

    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (LeaseLost, LockNotHeld) as exc:
            raise OwnershipLost(str(exc)) from exc

    return wrapper


def to_record(snap: ActionSnapshot) -> ActionRecord:
    return ActionRecord(
        action_id=snap.action_id,
        operation_id=snap.operation_id,
        kind=snap.kind,
        target=snap.target,
        status=snap.status.value,
        correlation=snap.correlation,
        external_refs=snap.external_refs,
        created_at=snap.created_at,
        updated_at=snap.updated_at,
    )


class LeaseGuard:
    def __init__(self, connect: Connect, lease: JobLease):
        self._connect, self.lease = connect, lease

    @_ownership
    def assert_valid(self) -> None:
        with self._connect() as conn, conn.transaction():
            self.lease.verify(conn)


class ActionLogService:
    def __init__(self, connect: Connect, lease: JobLease):
        self._connect, self.lease = connect, lease

    @_ownership
    def record_planned(self, operation_id, kind, target, correlation) -> ActionRecord:
        with self._connect() as conn:
            snap = actions.record_action(
                conn,
                self.lease,
                operation_id=operation_id,
                kind=kind,
                target=target,
                mutating=kind in MUTATING_AGENT_KINDS,
                correlation=correlation,
            )
        return to_record(snap)

    @_ownership
    def update(self, action_id, status, external_refs=None) -> ActionRecord:
        with self._connect() as conn:
            snap = actions.update_status(conn, self.lease, action_id, status, external_refs=external_refs)
        return to_record(snap)

    def get(self, action_id) -> ActionRecord | None:
        with self._connect() as conn:
            snap = actions.get_action(conn, action_id)
        return to_record(snap) if snap else None

    def list_for_operation(self, operation_id) -> list[ActionRecord]:
        with self._connect() as conn:
            return [to_record(s) for s in actions.actions_for_operation(conn, operation_id)]


class LockService:
    """Sperre ohne Warten und ohne ``--force``; Besitzer ist der Auftrag (``job:<id>``)."""

    def __init__(self, connect: Connect, lease: JobLease, job: dict, ttl_seconds: int):
        self._connect, self.lease, self.job, self.ttl = connect, lease, job, ttl_seconds
        self._handles: dict[tuple[str, str], LockHandle] = {}

    @_ownership
    def try_acquire(self, app_id, environment, operation_id) -> LockAcquisition:
        with self._connect() as conn:
            with conn.transaction():
                self.lease.verify(conn)
            try:
                handle = locks.try_acquire(
                    conn,
                    app_id=app_id,
                    environment=environment,
                    operation_id=operation_id,
                    owner_ref=self.lease.lock_owner_ref,
                    kind="agent",
                    ttl_seconds=self.ttl,
                    job_id=self.lease.job_id,
                )
            except TargetLocked as busy:
                return LockAcquisition(acquired=False, holder_operation_id=busy.operation_id)
            except UnresolvedActions as blocked:
                return LockAcquisition(acquired=False, holder_operation_id=blocked.operation_id)
        self._handles[(app_id, environment)] = handle
        return LockAcquisition(acquired=True)

    def holder(self, app_id, environment) -> str | None:
        """Operation, die das Ziel blockiert: gültige Sperre oder abgelaufene mit ungeklärter Aktion."""
        with self._connect() as conn:
            row = locks.status(conn, app_id, environment)
            if row is None:
                return None
            if row["valid"] or locks.unresolved_actions(conn, row["operation_id"], app_id, environment):
                return row["operation_id"]
        return None

    def holds(self, app_id, environment, operation_id) -> bool:
        handle = self._handles.get((app_id, environment))
        if handle is None or handle.operation_id != operation_id:
            return False
        with self._connect() as conn:
            try:
                locks.check(conn, handle)
            except LockNotHeld:
                return False
        return True

    @_ownership
    def release(self, app_id, environment, operation_id) -> None:
        handle = self._handles.pop((app_id, environment), None)
        if handle is None or handle.operation_id != operation_id:
            raise LockNotHeld(f"Operation {operation_id} hält die Sperre {app_id}/{environment} nicht")
        with self._connect() as conn:
            locks.release(conn, handle)


class TransitionService:
    """Zustandswünsche von D; C prüft jeden Übergang gegen die Übergangstabelle."""

    def __init__(self, connect: Connect, lease: JobLease):
        self._connect, self.lease = connect, lease

    @_ownership
    def request(self, job_id, state, reason) -> bool:
        if job_id != self.lease.job_id:
            return False
        target = JobState(state)
        with self._connect() as conn:
            current = JobState(jobs.get_job(conn, job_id)["state"])
            if current == target:
                return True  # erneuter Antrag, z. B. zweiter Smoke-Test
            try:
                if current == JobState.VERIFYING and target == JobState.ACTING:
                    # Erneuter begrenzter Versuch: zurück in die Untersuchung, dann handeln.
                    jobs.request_transition(conn, self.lease, JobState.INVESTIGATING,
                                            "erneuter Versuch nach Verifikation", requested_by="agent")
                jobs.request_transition(conn, self.lease, target, reason, requested_by="agent")
            except TransitionRejected:
                return False
        return True


class ConversationPostService:
    """Schreibt Agentenantworten nur unter gültigem Besitz in Portal-Vorfälle."""

    def __init__(self, connect: Connect, lease: JobLease):
        self._connect, self.lease = connect, lease

    def _job(self, conn, job_id: str) -> dict:
        if job_id != self.lease.job_id:
            raise OwnershipLost("Auftrag gehört nicht zu diesem Besitz")
        job = self.lease.verify(conn)
        incident = conn.execute("SELECT source FROM incidents WHERE id = %s", (job["incident_id"],)).fetchone()
        if incident["source"] != "portal":
            raise ValueError("Auftrag gehört nicht zum Portal")
        return job

    @_ownership
    def post(self, job_id: str, text: str, kind: Literal["reply", "result"]) -> str:
        if kind not in ("reply", "result") or not isinstance(text, str) or not text.strip():
            raise ValueError("Antwort und Art müssen gültig sein")
        note = "… [gekürzt]"
        body = text if len(text) <= 4000 else text[:4000 - len(note)] + note
        with self._connect() as conn, conn.transaction():
            job = self._job(conn, job_id)
            # Ergebnis auch bei Wiederaufnahme nur einmal speichern; Lease sperrt die Auftragszeile.
            if kind == "result":
                existing = conn.execute(
                    "SELECT id FROM conversation_messages WHERE job_id = %s AND kind = 'result'", (job_id,),
                ).fetchone()
                if existing:
                    return str(existing["id"])
            row = conn.execute(
                """INSERT INTO conversation_messages (incident_id, job_id, role, author, kind, body)
                   VALUES (%s, %s, 'agent', 'Betriebsagent', %s, %s) RETURNING id""",
                (job["incident_id"], job_id, kind, body),
            ).fetchone()
            return str(row["id"])

    @_ownership
    def has_result(self, job_id: str) -> bool:
        with self._connect() as conn, conn.transaction():
            self._job(conn, job_id)
            return conn.execute(
                "SELECT 1 FROM conversation_messages WHERE job_id = %s AND kind = 'result' LIMIT 1", (job_id,),
            ).fetchone() is not None


class CheckpointService:
    """LangGraph-Checkpointer auf eigener Verbindung; Tabellen legt ``ops migrate`` an."""

    def __init__(self):
        self._conn: psycopg.Connection | None = None

    def checkpointer(self):
        from agent.persistence import checkpointer, connect

        if self._conn is None:
            self._conn = connect(conninfo())
        return checkpointer(self._conn)

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None


def build_services(connect: Connect, lease: JobLease, job: dict, *, lock_ttl_seconds: int) -> tuple[
    OperationsServices, CheckpointService
]:
    checkpoints = CheckpointService()
    with connect() as conn:
        incident = conn.execute("SELECT source FROM incidents WHERE id = %s", (job["incident_id"],)).fetchone()
    services = OperationsServices(
        actions=ActionLogService(connect, lease),
        lock=LockService(connect, lease, job, lock_ttl_seconds),
        state=TransitionService(connect, lease),
        ownership=LeaseGuard(connect, lease),
        checkpoints=checkpoints,
        conversation=ConversationPostService(connect, lease) if incident["source"] == "portal" else None,
    )
    return services, checkpoints
