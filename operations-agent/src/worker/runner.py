"""Worker: übernimmt Aufträge, hält Lease und Heartbeat und ruft D's Ausführung auf.

Scheduling, Besitz, Wiederaufnahme und Übergabe liegen hier; Diagnose und Maßnahmen
in D's ``AgentExecutor``. Eine zweite Queue innerhalb von D gibt es nicht.
"""

import logging
import os
import socket
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass

import psycopg
from psycopg.types.json import Jsonb

from contracts.actions import ActionStatus, ReconcileResult, ReconcileStatus
from contracts.agent import (
    ActionReconciler,
    AgentExecutor,
    AgentInvocation,
    AgentResult,
    ContextRefs,
    LeaseInfo,
    ResumeInfo,
)
from contracts.errors import LeaseLost, TargetLocked, TransitionRejected, UnresolvedActions
from contracts.states import TERMINAL_STATES, JobState
from persistence import actions, jobs, locks
from persistence.jobs import Claim

from .services import WorkerServices

log = logging.getLogger("ops.worker")


@dataclass
class WorkerConfig:
    worker_id: str = ""
    lease_seconds: int = 120
    renew_interval_seconds: float = 30
    heartbeat_interval_seconds: float = 30
    poll_interval_seconds: float = 5
    max_attempts: int = 3
    # Kurz, weil der Worker die Sperre mit dem Lease erneuert; nach Absturz bald frei.
    lock_ttl_seconds: int = 300
    code_version: str | None = None

    def __post_init__(self):
        # Mehrfach vor Ablauf erneuern, auch bei kurz eingestelltem Lease.
        self.renew_interval_seconds = min(self.renew_interval_seconds, self.lease_seconds / 3)
        if not self.worker_id:
            self.worker_id = f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:6]}"
        if self.code_version is None:
            self.code_version = os.environ.get("OPS_GIT_SHA")


class _Ticker(threading.Thread):
    """Ruft ``fn`` in festen Abständen auf, unabhängig vom laufenden Auftrag."""

    def __init__(self, name: str, interval: float, fn: Callable[[], None]):
        super().__init__(name=name, daemon=True)
        self.interval = interval
        self.fn = fn
        self.stopped = threading.Event()

    def run(self):
        while not self.stopped.wait(self.interval):
            try:
                self.fn()
            except Exception:  # Ticker darf nie still sterben
                log.exception("%s fehlgeschlagen", self.name)

    def stop(self):
        self.stopped.set()


class Worker:
    def __init__(
        self,
        connect: Callable[[], psycopg.Connection],
        executor: AgentExecutor,
        reconciler: ActionReconciler,
        config: WorkerConfig | None = None,
    ):
        self.connect = connect
        self.executor = executor
        self.reconciler = reconciler
        self.config = config or WorkerConfig()
        self.current_job_id: str | None = None
        self._heartbeat: _Ticker | None = None

    # --- Heartbeat -----------------------------------------------------------------

    def beat(self) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO worker_heartbeats (worker_id, beat_at, current_job_id)
                VALUES (%s, now(), %s)
                ON CONFLICT (worker_id) DO UPDATE
                   SET beat_at = now(), current_job_id = EXCLUDED.current_job_id
                """,
                (self.config.worker_id, self.current_job_id),
            )

    def start_heartbeat(self) -> None:
        self.beat()
        self._heartbeat = _Ticker("heartbeat", self.config.heartbeat_interval_seconds, self.beat)
        self._heartbeat.start()

    def stop_heartbeat(self) -> None:
        if self._heartbeat:
            self._heartbeat.stop()

    # --- Schleife ------------------------------------------------------------------

    def run_forever(self, stop: threading.Event) -> None:
        self.start_heartbeat()
        log.info("Worker %s gestartet", self.config.worker_id)
        try:
            while not stop.is_set():
                try:
                    handled = self.run_once()
                except psycopg.OperationalError as exc:
                    log.error("Datenbank nicht erreichbar: %s", exc.__class__.__name__)
                    handled = None
                if handled is None:
                    stop.wait(self.config.poll_interval_seconds)
        finally:
            self.stop_heartbeat()
            log.info("Worker %s beendet", self.config.worker_id)

    def run_once(self) -> str | None:
        """Bearbeitet höchstens einen Auftrag; liefert dessen ID oder None."""
        with self.connect() as conn:
            claim = jobs.claim_next(conn, self.config.worker_id, self.config.lease_seconds)
        if claim is None:
            return None
        self.current_job_id = claim.lease.job_id
        lease_lost = threading.Event()

        def renew():
            try:
                with self.connect() as conn:
                    jobs.renew(conn, claim.lease, self.config.lease_seconds)
                    locks.renew_owned_by(conn, claim.lease.lock_owner_ref, self.config.lock_ttl_seconds)
            except LeaseLost:
                lease_lost.set()
                log.warning("Besitz an Auftrag %s verloren; keine weiteren Schreibschritte", claim.lease.job_id)

        renewer = _Ticker("lease-renewal", self.config.renew_interval_seconds, renew)
        renewer.start()
        try:
            self._handle(claim)
        except LeaseLost as exc:
            log.warning("Auftrag %s abgebrochen: %s", claim.lease.job_id, exc)
        finally:
            renewer.stop()
            self.current_job_id = None
        return claim.lease.job_id

    # --- Ein Auftrag ---------------------------------------------------------------

    def _abort(self, claim: Claim, state: JobState, reason: str) -> None:
        with self.connect() as conn:
            jobs.request_transition(conn, claim.lease, state, reason, requested_by="worker")
        log.info("Auftrag %s -> %s: %s", claim.lease.job_id, state, reason)

    def _handle(self, claim: Claim) -> None:
        job, lease = claim.job, claim.lease
        if job["attempts"] > self.config.max_attempts:
            self._abort(claim, JobState.NEEDS_ATTENTION, f"Wiederholungslimit ({self.config.max_attempts}) erreicht")
            return

        resume = None
        if claim.resumed:
            resume = self._reconcile(claim)
            if resume is None:
                return

        if JobState(job["state"]) == JobState.QUEUED:
            with self.connect() as conn:
                jobs.request_transition(conn, lease, JobState.INVESTIGATING, "Untersuchung beginnt", requested_by="worker")
            state = JobState.INVESTIGATING
        else:
            state = JobState(job["state"])

        with self.connect() as conn:
            execution_id = str(
                conn.execute(
                    """
                    INSERT INTO executions (job_id, worker_id, lease_version, resumed, code_version)
                    VALUES (%s, %s, %s, %s, %s) RETURNING id
                    """,
                    (lease.job_id, self.config.worker_id, lease.version, claim.resumed, self.config.code_version),
                ).fetchone()["id"]
            )
            trigger_events = self._trigger_events(conn, lease.job_id)

        services = WorkerServices(self.connect, lease, job, execution_id, lock_ttl_seconds=self.config.lock_ttl_seconds)
        invocation = AgentInvocation(
            job_id=lease.job_id,
            incident_id=str(job["incident_id"]),
            execution_id=execution_id,
            app_id=job["app_id"],
            environment=job["environment"],
            state=state,
            trigger_events=trigger_events,
            context=self._context(job, trigger_events),
            lease=LeaseInfo(owner=lease.owner, version=lease.version, expires_at=claim.expires_at),
            resume=resume,
        )

        # Bei LeaseLost wird nichts mehr geschrieben und keine Sperre freigegeben:
        # der Auftrag gehört dann womöglich schon einem anderen Worker.
        try:
            result = self.executor.run(invocation, services)
        except (TargetLocked, UnresolvedActions) as blocked:
            # Konfliktregel: der Agent wartet nicht.
            self._release_locks(lease)
            self._finish_execution(execution_id, None, str(blocked))
            reason = (
                f"gesperrt durch Operation {blocked.operation_id}"
                if isinstance(blocked, TargetLocked)
                else str(blocked)
            )
            self._abort(claim, JobState.NEEDS_ATTENTION, reason)
            return
        except LeaseLost:
            raise
        except Exception as exc:
            log.exception("Agentenausführung für Auftrag %s fehlgeschlagen", lease.job_id)
            error = f"{exc.__class__.__name__}: {exc}"
            self._release_locks(lease)
            self._finish_execution(execution_id, None, error)
            self._abort(claim, JobState.FAILED, f"Agentenausführung fehlgeschlagen: {error}")
            return

        self._release_locks(lease)
        self._finish_execution(execution_id, result, None)
        self._apply_result(claim, result)

    def _apply_result(self, claim: Claim, result: AgentResult) -> None:
        with self.connect() as conn:
            current = JobState(jobs.get_job(conn, claim.lease.job_id)["state"])
        if current in TERMINAL_STATES:
            return
        target = JobState(result.proposed_state)
        try:
            with self.connect() as conn:
                jobs.request_transition(
                    conn,
                    claim.lease,
                    target,
                    result.reason,
                    requested_by="agent",
                    expected_release=result.expected_release.model_dump() if result.expected_release else None,
                )
        except TransitionRejected as rejected:
            # Abgelehnt und protokolliert; der Auftrag bleibt sichtbar ungelöst.
            self._abort(claim, JobState.NEEDS_ATTENTION, f"Abschluss abgelehnt: {rejected.reason}")

    # --- Wiederaufnahme ------------------------------------------------------------

    def _reconcile(self, claim: Claim) -> ResumeInfo | None:
        """Gleicht offene Aktionen ab, bevor D weiterarbeitet. None heißt: Auftrag beendet."""
        with self.connect() as conn:
            open_actions = actions.open_actions_for_job(conn, claim.lease.job_id)
        reconciled: dict[str, ReconcileResult] = {}
        still_open = []
        for action in open_actions:
            try:
                outcome = self.reconciler.reconcile(action)
            except Exception as exc:
                outcome = ReconcileResult(status=ReconcileStatus.AMBIGUOUS, reason=f"Abgleich fehlgeschlagen: {exc}")
            reconciled[action.action_id] = outcome
            new_status = {
                ReconcileStatus.SUCCEEDED: ActionStatus.SUCCEEDED,
                ReconcileStatus.FAILED: ActionStatus.FAILED,
                ReconcileStatus.NOT_STARTED: ActionStatus.FAILED,
                ReconcileStatus.RUNNING: ActionStatus.STARTED,
                ReconcileStatus.AMBIGUOUS: ActionStatus.UNCLEAR,
            }[outcome.status]
            with self.connect() as conn:
                # Ohne Guard: Abgleich gehört zur Wiederaufnahme; der Besitz ist frisch übernommen.
                with conn.transaction():
                    claim.lease.verify(conn)
                snap = action
                for ref in outcome.external_refs or [None]:
                    snap = actions.update_status(
                        conn, None, action.action_id, new_status,
                        reason=f"Abgleich: {outcome.reason}", external_ref=ref,
                    )
            if outcome.status == ReconcileStatus.AMBIGUOUS:
                self._abort(
                    claim,
                    JobState.NEEDS_ATTENTION,
                    f"Klärung erforderlich: Ausgang von Aktion {action.action_id} ({action.kind}) unklar – {outcome.reason}",
                )
                return None
            if outcome.status == ReconcileStatus.RUNNING:
                still_open.append(snap)
        return ResumeInfo(
            previous_state=JobState(claim.job["state"]),
            attempt=claim.job["attempts"],
            open_actions=still_open,
            reconciled=reconciled,
        )

    # --- Hilfen --------------------------------------------------------------------

    def _release_locks(self, lease) -> None:
        with self.connect() as conn:
            locks.release_owned_by(conn, lease.lock_owner_ref)

    def _finish_execution(self, execution_id: str, result: AgentResult | None, error: str | None) -> None:
        with self.connect() as conn, conn.transaction():
            conn.execute(
                """
                UPDATE executions
                   SET finished_at = now(), proposed_state = %s, reason = %s, summary = %s,
                       model_settings = %s, usage = %s, error = %s,
                       code_version = coalesce(%s, code_version), config_ref = %s
                 WHERE id = %s
                """,
                (
                    result.proposed_state if result else None,
                    result.reason if result else None,
                    result.summary if result else None,
                    Jsonb(result.model_settings if result else {}),
                    Jsonb(result.usage.model_dump() if result else {}),
                    error,
                    result.code_version if result else None,
                    result.config_ref if result else None,
                    execution_id,
                ),
            )
            if result:
                for finding in result.findings:
                    conn.execute(
                        """
                        INSERT INTO findings (job_id, execution_id, confirmed, statement, evidence_refs)
                        SELECT job_id, id, %s, %s, %s FROM executions WHERE id = %s
                        """,
                        (finding.confirmed, finding.statement, Jsonb(finding.evidence_refs), execution_id),
                    )

    @staticmethod
    def _trigger_events(conn, job_id: str) -> list[dict]:
        rows = conn.execute(
            """
            SELECT schema_version, source, delivery_id, external_ref, app_id, environment,
                   event_type, severity, state, occurred_at, received_at, source_ref, mapping_error
              FROM events WHERE job_id = %s ORDER BY received_at
            """,
            (job_id,),
        ).fetchall()
        return [
            {
                "schemaVersion": r["schema_version"],
                "source": r["source"],
                "deliveryId": r["delivery_id"],
                "externalRef": r["external_ref"],
                "appId": r["app_id"],
                "environment": r["environment"],
                "eventType": r["event_type"],
                "severity": r["severity"],
                "state": r["state"],
                "occurredAt": r["occurred_at"].isoformat() if r["occurred_at"] else None,
                "receivedAt": r["received_at"].isoformat(),
                "sourceRef": r["source_ref"],
                "mappingError": r["mapping_error"],
            }
            for r in rows
        ]

    @staticmethod
    def _context(job: dict, events: list[dict]) -> ContextRefs:
        issue = next((e["externalRef"] for e in events if e["source"] == "github"), None)
        return ContextRefs(
            catalog_entry=f"platform/catalog/{job['app_id']}/app.yaml",
            desired_state=f"platform/environments/{job['environment']}/{job['app_id']}.yaml",
            issue=issue,
        )
