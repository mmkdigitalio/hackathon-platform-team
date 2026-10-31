"""Worker: übernimmt Aufträge, hält Lease und Heartbeat und ruft D's Ausführung auf.

Scheduling, Besitz, Zustandsübergänge und Persistenz liegen hier; Diagnose, Maßnahmen
und der Abgleich offener Aktionen nach einem Neustart in D's ``agent.execute``.
Eine zweite Queue innerhalb von D gibt es nicht.
"""

import logging
import os
import socket
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

import psycopg
from psycopg.types.json import Jsonb

from contracts import (
    AgentExecution,
    AgentInvocation,
    AgentResult,
    ContextRefs,
    ConversationRef,
    IssueRef,
    Ownership,
    OwnershipLost,
    ResumeInfo,
    RunLimits,
)
from contracts.errors import LeaseLost, TransitionRejected
from contracts.states import TERMINAL_STATES, JobState
from persistence import actions, jobs, locks, portal
from persistence.jobs import Claim

from .services import LeaseGuard, build_services, to_record

log = logging.getLogger("ops.worker")


def default_limits() -> RunLimits:
    try:
        from agent.config import limits_from_env
    except ImportError:
        return RunLimits()
    return limits_from_env()


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
    # Git-Stand, zu dem D Katalog und Sollzustand liest.
    context_git_ref: str = ""
    limits: Callable[[], RunLimits] = field(default=default_limits)

    def __post_init__(self):
        # Mehrfach vor Ablauf erneuern, auch bei kurz eingestelltem Lease.
        self.renew_interval_seconds = min(self.renew_interval_seconds, self.lease_seconds / 3)
        if not self.worker_id:
            self.worker_id = f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:6]}"
        if self.code_version is None:
            self.code_version = os.environ.get("OPS_GIT_SHA")
        if not self.context_git_ref:
            self.context_git_ref = os.environ.get("OPS_CONTEXT_GIT_REF", "main")


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
        execute: AgentExecution,
        config: WorkerConfig | None = None,
    ):
        self.connect = connect
        self.execute = execute
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

        def renew():
            try:
                with self.connect() as conn:
                    jobs.renew(conn, claim.lease, self.config.lease_seconds)
                    locks.renew_owned_by(conn, claim.lease.lock_owner_ref, self.config.lock_ttl_seconds)
            except LeaseLost:
                log.warning("Besitz an Auftrag %s verloren; keine weiteren Schreibschritte", claim.lease.job_id)

        renewer = _Ticker("lease-renewal", self.config.renew_interval_seconds, renew)
        renewer.start()
        try:
            self._handle(claim)
        except (LeaseLost, OwnershipLost) as exc:
            log.warning("Auftrag %s abgebrochen: %s", claim.lease.job_id, exc)
        finally:
            renewer.stop()
            self.current_job_id = None
        return claim.lease.job_id

    # --- Ein Auftrag ---------------------------------------------------------------

    def _transition(self, claim: Claim, state: JobState, reason: str, **kwargs) -> None:
        with self.connect() as conn:
            jobs.request_transition(conn, claim.lease, state, reason, requested_by="worker", **kwargs)

    def _handle(self, claim: Claim) -> None:
        job, lease = claim.job, claim.lease
        if job["attempts"] > self.config.max_attempts:
            self._transition(claim, JobState.NEEDS_ATTENTION, f"Wiederholungslimit ({self.config.max_attempts}) erreicht")
            return
        if JobState(job["state"]) == JobState.QUEUED:
            self._transition(claim, JobState.INVESTIGATING, "Untersuchung beginnt")

        with self.connect() as conn:
            with conn.transaction():
                lease.verify(conn)
                operation_id = self._operation(conn, claim)
                execution_id = str(
                    conn.execute(
                        """
                        INSERT INTO executions (job_id, worker_id, lease_version, resumed, code_version)
                        VALUES (%s, %s, %s, %s, %s) RETURNING id
                        """,
                        (lease.job_id, self.config.worker_id, lease.version, claim.resumed, self.config.code_version),
                    ).fetchone()["id"]
                )
            events = conn.execute(
                "SELECT source, external_ref, event_type, payload FROM events WHERE job_id = %s ORDER BY received_at",
                (lease.job_id,),
            ).fetchall()
            open_actions = [to_record(a) for a in actions.open_actions_for_job(conn, lease.job_id)]

        invocation = AgentInvocation(
            job_id=lease.job_id,
            incident_id=str(job["incident_id"]),
            execution_id=execution_id,
            app_id=job["app_id"],
            environment=job["environment"],
            operation_id=operation_id,
            ownership=Ownership(worker_id=lease.owner, lease_version=lease.version),
            limits=self.config.limits(),
            context=self._context(events),
            # Gleiche thread_id je Auftrag: D setzt den Checkpoint nach Neustart fort.
            resume=ResumeInfo(thread_id=lease.job_id, open_actions=open_actions),
        )
        services, checkpoints = build_services(self.connect, lease, job, lock_ttl_seconds=self.config.lock_ttl_seconds)
        try:
            result = self.execute(invocation, services)
        except (LeaseLost, OwnershipLost):
            # Nichts mehr schreiben, keine Sperre freigeben: der Auftrag gehört womöglich schon anderen.
            raise
        except Exception as exc:
            log.exception("Agentenausführung für Auftrag %s fehlgeschlagen", lease.job_id)
            self._finish_failed(claim, execution_id, f"{exc.__class__.__name__}: {exc}")
            return
        finally:
            checkpoints.close()

        LeaseGuard(self.connect, lease).assert_valid()
        with self.connect() as conn:
            locks.release_owned_by(conn, lease.lock_owner_ref)
        self._persist_result(claim, execution_id, result)
        self._apply_result(claim, result)

    def _operation(self, conn, claim: Claim) -> str:
        """Stabile operationId je Auftrag; bei Wiederaufnahme dieselbe."""
        row = conn.execute(
            "SELECT id FROM operations WHERE job_id = %s AND kind = 'agent' ORDER BY started_at LIMIT 1",
            (claim.lease.job_id,),
        ).fetchone()
        if row:
            return row["id"]
        op = locks.ensure_operation(
            conn,
            locks.new_operation_id(),
            app_id=claim.job["app_id"],
            environment=claim.job["environment"],
            kind="agent",
            owner_ref=claim.lease.lock_owner_ref,
            job_id=claim.lease.job_id,
        )
        return op["id"]

    def _context(self, events: list[dict]) -> ContextRefs:
        alert = next((e["payload"] for e in reversed(events) if e["source"] == "azure-monitor"), None)
        github = [e for e in events if e["source"] == "github"]
        issue, text = None, None
        if github:
            repo, _, number = github[0]["external_ref"].rpartition("#")
            issue = IssueRef(repository=repo, number=int(number))
            parts = [github[0]["payload"].get("issue", {}).get("body") or ""]
            parts += [(e["payload"].get("comment") or {}).get("body") or "" for e in github[1:]]
            text = "\n\n---\n\n".join(p for p in parts if p)
        conversation = None
        portal_events = [e for e in events if e["source"] == "portal"]
        if portal_events:
            conversation_id = portal_events[-1]["external_ref"]
            with self.connect() as conn:
                messages = portal.conversation_messages(conn, conversation_id)
            reporter = next((m["author"] for m in reversed(messages) if m["role"] == "reporter"),
                            portal_events[-1]["payload"].get("reporter"))
            conversation = ConversationRef(id=conversation_id, reporter=reporter)
            text = "\n".join(
                f"[{m['created_at'].isoformat()}] {m['role']} ({m['author'] or m['role']}): {m['body']}"
                for m in messages if m["role"] in ("reporter", "agent")
            )[-4000:]
        return ContextRefs(
            git_ref=self.config.context_git_ref,
            alert_payload=alert,
            issue=issue,
            conversation=conversation,
            issue_text=text,
            event_type=events[0]["event_type"] if events else None,
        )

    # --- Ergebnis --------------------------------------------------------------------

    def _finish_failed(self, claim: Claim, execution_id: str, error: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE executions SET finished_at = now(), error = %s WHERE id = %s", (error, execution_id)
            )
            locks.release_owned_by(conn, claim.lease.lock_owner_ref)
        self._transition(claim, JobState.FAILED, f"Agentenausführung fehlgeschlagen: {error}")

    def _persist_result(self, claim: Claim, execution_id: str, result: AgentResult) -> None:
        lease = claim.lease
        with self.connect() as conn, conn.transaction():
            lease.verify(conn)
            conn.execute(
                """
                UPDATE executions
                   SET finished_at = now(), proposed_state = %s, reason = %s, limit_reached = %s,
                       required_change = %s, model_settings = %s, usage = %s, "references" = %s,
                       config_ref = %s
                 WHERE id = %s
                """,
                (
                    result.proposal, result.reason, result.limit_reached, result.required_change,
                    Jsonb({"provider": result.usage.provider, "model": result.usage.model}),
                    Jsonb(result.usage.model_dump()), Jsonb(result.references),
                    _config_ref(result.references), execution_id,
                ),
            )
            for f in result.findings:
                conn.execute(
                    """
                    INSERT INTO findings (job_id, execution_id, confirmed, statement, provenance,
                                          evidence_refs, observed_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (lease.job_id, execution_id, f.certainty == "confirmed", f.statement, f.provenance,
                     Jsonb(f.evidence_refs), f.observed_at),
                )
            for t in result.tool_calls:
                conn.execute(
                    """
                    INSERT INTO tool_calls (job_id, execution_id, tool, target, action_id, ok, error_kind,
                                            started_at, duration_ms)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (lease.job_id, execution_id, t.name, t.target, t.action_id, t.status == "ok",
                     t.error_kind, t.started_at, t.duration_ms),
                )
            for v in result.verification:
                release = v.release.model_dump()
                conn.execute(
                    """
                    INSERT INTO verifications (job_id, kind, app_id, environment, success, release,
                                               check_ref, started_at, finished_at, evidence)
                    VALUES (%s, 'smoke-test', %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (lease.job_id, v.app_id, v.environment, v.success, Jsonb(release), v.check_ref,
                     v.started_at, v.finished_at,
                     Jsonb({"jobName": v.job_name, "actionId": v.action_id, "exitCode": v.exit_code,
                            "timedOut": v.timed_out, "result": v.result, "evidenceRefs": v.evidence_refs})),
                )
                k = v.kubernetes
                if k is not None:
                    conn.execute(
                        """
                        INSERT INTO verifications (job_id, kind, app_id, environment, success, release,
                                                   started_at, evidence)
                        VALUES (%s, 'k8s-state', %s, %s, %s, %s, %s, %s)
                        """,
                        (lease.job_id, v.app_id, v.environment, k.rollout_complete and k.ready_replicas >= 1,
                         Jsonb({**release, "image_digests": k.image_digests}), k.captured_at,
                         Jsonb(k.model_dump(mode="json"))),
                    )

    def _apply_result(self, claim: Claim, result: AgentResult) -> None:
        with self.connect() as conn:
            current = JobState(jobs.get_job(conn, claim.lease.job_id)["state"])
        if current in TERMINAL_STATES:
            return
        reason = result.reason + (f" – nötige Änderung: {result.required_change}" if result.required_change else "")
        if result.proposal != "resolved":
            self._transition(claim, JobState(result.proposal), reason)
            return
        expected = _expected_release(result.references)
        try:
            if current in (JobState.INVESTIGATING, JobState.ACTING):
                self._transition(claim, JobState.VERIFYING, "Nachweise des Agenten liegen vor")
            with self.connect() as conn:
                jobs.request_transition(conn, claim.lease, JobState.RESOLVED, reason,
                                        requested_by="agent", expected_release=expected)
        except TransitionRejected as rejected:
            # Abgelehnt und protokolliert; der Auftrag bleibt sichtbar ungelöst.
            self._transition(claim, JobState.NEEDS_ATTENTION, f"Abschluss abgelehnt: {rejected.reason}")


def _desired(references: dict) -> dict:
    value = references.get("desiredState")
    return value if isinstance(value, dict) else {}


def _expected_release(references: dict) -> dict | None:
    """Erwarteter Zielstand aus dem Sollzustand, den D zum Git-Stand des Auftrags gelesen hat."""
    digest = _desired(references).get("imageDigest")
    return {"image_digest": digest} if digest else None


def _config_ref(references: dict) -> str | None:
    desired = _desired(references)
    if desired.get("path"):
        return f"{desired['path']}@{desired.get('gitRef') or '?'}"
    return None

