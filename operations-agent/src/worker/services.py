"""C's Dienste für die Agentenausführung (Vertrag ``OperationServices``).

Jeder Aufruf läuft in einer eigenen Transaktion und prüft den Auftragsbesitz.
"""

from collections.abc import Callable
from datetime import datetime

import psycopg
from psycopg.types.json import Jsonb

from contracts.actions import ActionStatus, ExternalRef, LockHandle
from contracts.agent import Finding, Release
from contracts.states import JobState
from persistence import actions, jobs, locks
from persistence.jobs import JobLease


class WorkerServices:
    def __init__(
        self,
        connect: Callable[[], psycopg.Connection],
        lease: JobLease,
        job: dict,
        execution_id: str,
        *,
        lock_ttl_seconds: int = locks.DEFAULT_LOCK_TTL_SECONDS,
    ):
        self._connect = connect
        self.lease = lease
        self.job = job
        self.execution_id = execution_id
        self.lock_ttl_seconds = lock_ttl_seconds
        self._operation_id: str | None = None
        self._lock: LockHandle | None = None

    # --- Operation und Sperre ------------------------------------------------------

    @property
    def operation_id(self) -> str:
        """Stabile operationId des Auftrags; bei Wiederaufnahme dieselbe."""
        if self._operation_id is None:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT id FROM operations WHERE job_id = %s AND kind = 'agent'
                     ORDER BY started_at LIMIT 1
                    """,
                    (self.lease.job_id,),
                ).fetchone()
                if row:
                    self._operation_id = row["id"]
                else:
                    with conn.transaction():
                        self.lease.verify(conn)
                        op = locks.ensure_operation(
                            conn,
                            locks.new_operation_id(),
                            app_id=self.job["app_id"],
                            environment=self.job["environment"],
                            kind="agent",
                            owner_ref=self.lease.lock_owner_ref,
                            job_id=self.lease.job_id,
                        )
                    self._operation_id = op["id"]
        return self._operation_id

    def acquire_target_lock(self) -> None:
        # Agent wartet nie und nutzt nie --force (Konfliktregel, E9).
        with self._connect() as conn:
            with conn.transaction():
                self.lease.verify(conn)
            self._lock = locks.try_acquire(
                conn,
                app_id=self.job["app_id"],
                environment=self.job["environment"],
                operation_id=self.operation_id,
                owner_ref=self.lease.lock_owner_ref,
                kind="agent",
                ttl_seconds=self.lock_ttl_seconds,
                job_id=self.lease.job_id,
            )

    def release_target_lock(self) -> None:
        if self._lock is None:
            return
        with self._connect() as conn:
            locks.release(conn, self._lock)
        self._lock = None

    # --- Auftrag -------------------------------------------------------------------

    def check_lease(self) -> None:
        with self._connect() as conn, conn.transaction():
            self.lease.verify(conn)

    def transition(self, to_state: JobState, reason: str, *, expected_release: Release | None = None) -> None:
        with self._connect() as conn:
            jobs.request_transition(
                conn,
                self.lease,
                to_state,
                reason,
                requested_by="agent",
                expected_release=expected_release.model_dump() if expected_release else None,
            )

    # --- Aktionen ------------------------------------------------------------------

    def record_action(self, kind, target, *, mutating=True, correlation=None, detail=None) -> str:
        with self._connect() as conn:
            return actions.record_action(
                conn,
                self.lease,
                operation_id=self.operation_id,
                kind=kind,
                target=target,
                mutating=mutating,
                correlation=correlation,
                detail=detail,
            )

    def action_started(self, action_id: str, external_ref: ExternalRef | None = None) -> None:
        with self._connect() as conn:
            actions.update_status(conn, self.lease, action_id, ActionStatus.STARTED, external_ref=external_ref)

    def action_finished(self, action_id: str, success: bool, reason: str) -> None:
        status = ActionStatus.SUCCEEDED if success else ActionStatus.FAILED
        with self._connect() as conn:
            actions.update_status(conn, self.lease, action_id, status, reason=reason)

    def action_unclear(self, action_id: str, reason: str) -> None:
        with self._connect() as conn:
            actions.update_status(conn, self.lease, action_id, ActionStatus.UNCLEAR, reason=reason)

    # --- Nachweise -----------------------------------------------------------------

    def record_verification(
        self,
        kind,
        *,
        success: bool,
        release: Release,
        started_at: datetime,
        finished_at: datetime | None = None,
        check_ref: str | None = None,
        evidence: dict | None = None,
        app_id: str | None = None,
        environment: str | None = None,
    ) -> str:
        with self._connect() as conn, conn.transaction():
            self.lease.verify(conn)
            row = conn.execute(
                """
                INSERT INTO verifications (job_id, kind, app_id, environment, success, release,
                                           check_ref, started_at, finished_at, evidence)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    self.lease.job_id, kind, app_id or self.job["app_id"],
                    environment or self.job["environment"], success, Jsonb(release.model_dump()),
                    check_ref, started_at, finished_at, Jsonb(evidence or {}),
                ),
            ).fetchone()
        return str(row["id"])

    def record_tool_call(self, tool, arguments, *, ok, result_summary, started_at, finished_at) -> None:
        with self._connect() as conn, conn.transaction():
            self.lease.verify(conn)
            conn.execute(
                """
                INSERT INTO tool_calls (job_id, execution_id, tool, arguments, ok, result_summary,
                                        started_at, finished_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (self.lease.job_id, self.execution_id, tool, Jsonb(arguments), ok, result_summary,
                 started_at, finished_at),
            )

    def record_finding(self, finding: Finding) -> None:
        with self._connect() as conn, conn.transaction():
            self.lease.verify(conn)
            conn.execute(
                """
                INSERT INTO findings (job_id, execution_id, confirmed, statement, evidence_refs)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (self.lease.job_id, self.execution_id, finding.confirmed, finding.statement,
                 Jsonb(finding.evidence_refs)),
            )
