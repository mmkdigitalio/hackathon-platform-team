"""Tasks 3.1 bis 3.4, 4.1, 4.3, 4.6, 4.7 und das Dispatch-Beispiel aus 1.2."""

import threading
import time
import uuid
from datetime import timedelta

import pytest

from api import normalize
from api.catalog import Catalog
from contracts import (
    WORKFLOW_DISPATCH,
    AgentResult,
    ExternalRef,
    JobState,
    LeaseLost,
    LockNotHeld,
    NestedTargetLock,
    ReconcileStatus,
    TransitionRejected,
)
from persistence import actions, intake, jobs, locks
from persistence.db import connect
from worker.runner import Worker, WorkerConfig
from worker.services import WorkerServices

from . import fakes
from .conftest import connector


def enqueue(db_url, platform_root, ref="health-check:affine:staging:availability") -> str:
    body = {"deliveryId": str(uuid.uuid4()), "externalRef": ref, "appId": "affine", "environment": "staging",
            "eventType": "health-check-failed", "severity": "error"}
    event = normalize.maintenance(body, b"{}", Catalog(platform_root))
    with connect(db_url) as conn:
        return intake.store_event(conn, event, body).job_id


def job(db_url, job_id) -> dict:
    with connect(db_url) as conn:
        return jobs.get_job(conn, job_id)


def transitions(db_url, job_id) -> list[dict]:
    with connect(db_url) as conn:
        return conn.execute(
            "SELECT from_state, to_state, accepted, reason FROM job_transitions WHERE job_id = %s ORDER BY id",
            (job_id,),
        ).fetchall()


def make_worker(db_url, script, reconciler=None, **config) -> tuple[Worker, fakes.ScriptedExecutor]:
    executor = fakes.ScriptedExecutor(script)
    cfg = WorkerConfig(worker_id=config.pop("worker_id", f"w-{uuid.uuid4().hex[:6]}"), **config)
    return Worker(connector(db_url), executor, reconciler or fakes.StaticReconciler(), cfg), executor


def claim(db_url, worker_id="w1", lease_seconds=60):
    with connect(db_url) as conn:
        return jobs.claim_next(conn, worker_id, lease_seconds)


def services_for(db_url, c: jobs.Claim) -> WorkerServices:
    return WorkerServices(connector(db_url), c.lease, c.job, execution_id=None)


# --- 3.1 Lease ---------------------------------------------------------------------------


def test_two_workers_claim_concurrently(db_url, platform_root):
    enqueue(db_url, platform_root)
    barrier = threading.Barrier(2)
    claims = []

    def run(worker_id):
        with connect(db_url) as conn:
            barrier.wait()
            claims.append(jobs.claim_next(conn, worker_id, 60))

    threads = [threading.Thread(target=run, args=(f"w{i}",)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(c is None for c in claims) == [False, True]


def test_only_one_worker_executes(db_url, platform_root):
    enqueue(db_url, platform_root)
    calls = []

    def script(inv, services):
        calls.append(inv.job_id)
        time.sleep(0.3)
        return AgentResult(proposed_state="needs_attention", reason="Test")

    workers = [make_worker(db_url, script)[0] for _ in range(2)]
    threads = [threading.Thread(target=w.run_once) for w in workers]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(calls) == 1


def test_expired_lease_is_taken_over_and_stale_owner_rejected(db_url, platform_root):
    job_id = enqueue(db_url, platform_root)
    first = claim(db_url, "w1", lease_seconds=1)
    assert claim(db_url, "w2") is None  # noch gültig
    time.sleep(1.2)
    second = claim(db_url, "w2")
    assert second.lease.job_id == job_id and second.lease.version == first.lease.version + 1
    assert second.resumed

    with connect(db_url) as conn:
        with pytest.raises(LeaseLost):
            jobs.request_transition(conn, first.lease, JobState.INVESTIGATING, "veraltet", requested_by="w1")
        with pytest.raises(LeaseLost):
            jobs.renew(conn, first.lease, 60)
    stale = services_for(db_url, first)
    with pytest.raises(LeaseLost):
        stale.record_finding(fakes.Finding(statement="x", confirmed=False))
    assert job(db_url, job_id)["state"] == "queued"


def test_lease_renewal_keeps_ownership(db_url, platform_root):
    enqueue(db_url, platform_root)
    c = claim(db_url, "w1", lease_seconds=1)
    for _ in range(3):
        time.sleep(0.5)
        with connect(db_url) as conn:
            jobs.renew(conn, c.lease, 1)
    assert claim(db_url, "w2") is None


# --- 3.2 Zustandsmaschine -----------------------------------------------------------------


def test_normal_run_resolves_with_evidence(db_url, platform_root):
    job_id = enqueue(db_url, platform_root)
    worker, executor = make_worker(db_url, fakes.restart_and_resolve)
    assert worker.run_once() == job_id
    assert job(db_url, job_id)["state"] == "resolved"
    path = [(t["from_state"], t["to_state"]) for t in transitions(db_url, job_id) if t["accepted"]]
    assert path == [("received", "queued"), ("queued", "investigating"), ("investigating", "acting"),
                    ("acting", "verifying"), ("verifying", "resolved")]
    assert executor.invocations[0].state == JobState.INVESTIGATING
    with connect(db_url) as conn:
        assert locks.status(conn, "affine", "staging") is None  # Sperre freigegeben


def test_investigation_without_action_may_verify(db_url, platform_root):
    job_id = enqueue(db_url, platform_root)

    def script(inv, s):
        s.transition(JobState.VERIFYING, "nur prüfen")
        ids = fakes.verify_ok(s)
        return AgentResult(proposed_state="resolved", reason="ok", verification_ids=ids,
                           expected_release=fakes.RELEASE)

    make_worker(db_url, script)[0].run_once()
    assert job(db_url, job_id)["state"] == "resolved"


def test_retry_from_verifying_back_to_investigating(db_url, platform_root):
    job_id = enqueue(db_url, platform_root)
    c = claim(db_url)
    with connect(db_url) as conn:
        for state in (JobState.INVESTIGATING, JobState.VERIFYING, JobState.INVESTIGATING):
            jobs.request_transition(conn, c.lease, state, "x", requested_by="test")
    assert job(db_url, job_id)["state"] == "investigating"


@pytest.mark.parametrize("from_path,target", [
    ([], JobState.RESOLVED),
    ([], JobState.ACTING),
    ([JobState.INVESTIGATING], JobState.RESOLVED),
    ([JobState.INVESTIGATING, JobState.ACTING], JobState.INVESTIGATING),
])
def test_forbidden_transitions_are_logged(db_url, platform_root, from_path, target):
    job_id = enqueue(db_url, platform_root)
    c = claim(db_url)
    with connect(db_url) as conn:
        for state in from_path:
            jobs.request_transition(conn, c.lease, state, "x", requested_by="test")
        before = jobs.get_job(conn, job_id)["state"]
        with pytest.raises(TransitionRejected):
            jobs.request_transition(conn, c.lease, target, "Modell behauptet", requested_by="agent")
    assert job(db_url, job_id)["state"] == before
    last = transitions(db_url, job_id)[-1]
    assert last["accepted"] is False and last["to_state"] == target


def _to_verifying_after_action(db_url, platform_root):
    job_id = enqueue(db_url, platform_root)
    c = claim(db_url)
    s = services_for(db_url, c)
    s.transition(JobState.INVESTIGATING, "x")
    return job_id, c, s


def _act(s):
    s.transition(JobState.ACTING, "x")
    s.acquire_target_lock()
    a = s.record_action("k8s-scale", "affine")
    s.action_finished(a, True, "ok")
    s.release_target_lock()
    s.transition(JobState.VERIFYING, "x")


def test_resolved_rejected_for_test_before_action(db_url, platform_root):
    job_id, c, s = _to_verifying_after_action(db_url, platform_root)
    fakes.verify_ok(s, test_started=fakes.now() - timedelta(seconds=1))  # vor der Maßnahme
    time.sleep(0.05)
    _act(s)
    with pytest.raises(TransitionRejected, match="vor der letzten Maßnahme"):
        s.transition(JobState.RESOLVED, "fertig", expected_release=fakes.RELEASE)
    assert job(db_url, job_id)["state"] == "verifying"


def test_resolved_rejected_without_k8s_state(db_url, platform_root):
    _, c, s = _to_verifying_after_action(db_url, platform_root)
    _act(s)
    s.record_verification("smoke-test", success=True, release=fakes.RELEASE, started_at=fakes.now())
    with pytest.raises(TransitionRejected, match="Kubernetes-Zustand"):
        s.transition(JobState.RESOLVED, "fertig", expected_release=fakes.RELEASE)


def test_resolved_rejected_for_wrong_release(db_url, platform_root):
    _, c, s = _to_verifying_after_action(db_url, platform_root)
    _act(s)
    t = fakes.now()
    s.record_verification("smoke-test", success=True, release=fakes.RELEASE, started_at=t)
    s.record_verification("k8s-state", success=True, release=fakes.OTHER_RELEASE, started_at=t)
    with pytest.raises(TransitionRejected, match="Kubernetes-Zustand zeigt Release"):
        s.transition(JobState.RESOLVED, "fertig", expected_release=fakes.RELEASE)


def test_resolved_rejected_without_expected_release(db_url, platform_root):
    _, c, s = _to_verifying_after_action(db_url, platform_root)
    _act(s)
    fakes.verify_ok(s)
    with pytest.raises(TransitionRejected, match="expected_release"):
        s.transition(JobState.RESOLVED, "fertig")


def test_model_claims_success_without_evidence(db_url, platform_root):
    """Ergebnisvorschlag resolved ohne Nachweis: abgelehnt, protokolliert, sichtbar ungelöst."""
    job_id = enqueue(db_url, platform_root)

    def script(inv, s):
        s.transition(JobState.VERIFYING, "x")
        return AgentResult(proposed_state="resolved", reason="Alles gut", expected_release=fakes.RELEASE)

    make_worker(db_url, script)[0].run_once()
    j = job(db_url, job_id)
    assert j["state"] == "needs_attention" and j["state_reason"].startswith("Abschluss abgelehnt")
    assert any(not t["accepted"] and t["to_state"] == "resolved" for t in transitions(db_url, job_id))


# --- 3.3 Testausführung ohne Queue ---------------------------------------------------------


def test_executor_gets_contract_invocation(db_url, platform_root):
    job_id = enqueue(db_url, platform_root)
    worker, executor = make_worker(db_url, lambda inv, s: AgentResult(proposed_state="failed", reason="x"))
    worker.run_once()
    inv = executor.invocations[0]
    assert inv.contract_version == "0.1" and inv.job_id == job_id
    assert inv.context.desired_state == "platform/environments/staging/affine.yaml"
    assert inv.trigger_events[0]["source"] == "maintenance"
    assert inv.resume is None
    assert worker.run_once() is None  # keine zweite Queue: nichts mehr zu tun


def test_executor_exception_fails_job(db_url, platform_root):
    job_id = enqueue(db_url, platform_root)

    def boom(inv, s):
        raise RuntimeError("Adapter kaputt")

    make_worker(db_url, boom)[0].run_once()
    assert job(db_url, job_id)["state"] == "failed"
    assert "Adapter kaputt" in job(db_url, job_id)["state_reason"]


def test_attempt_limit(db_url, platform_root):
    job_id = enqueue(db_url, platform_root)
    for i in range(3):
        claim(db_url, f"w{i}", lease_seconds=0)
    make_worker(db_url, fakes.restart_and_resolve, max_attempts=3)[0].run_once()
    assert job(db_url, job_id)["state_reason"].startswith("Wiederholungslimit")


# --- 3.4 Heartbeat ------------------------------------------------------------------------


def test_heartbeat_continues_during_long_job(db_url, platform_root):
    enqueue(db_url, platform_root)
    beats = []

    def slow(inv, s):
        time.sleep(1.0)
        return AgentResult(proposed_state="needs_attention", reason="lang")

    worker, _ = make_worker(db_url, slow, worker_id="hb", heartbeat_interval_seconds=0.2)
    worker.start_heartbeat()
    try:
        t = threading.Thread(target=worker.run_once)
        t.start()
        for _ in range(4):
            time.sleep(0.25)
            with connect(db_url) as conn:
                row = conn.execute(
                    "SELECT extract(epoch FROM now() - beat_at) AS age, current_job_id FROM worker_heartbeats"
                ).fetchone()
                beats.append(row)
        t.join()
    finally:
        worker.stop_heartbeat()
    assert all(float(b["age"]) < 0.6 for b in beats)
    assert any(b["current_job_id"] is not None for b in beats)


# --- 4.1 / 4.7 Aktionsprotokoll und Wiederaufnahme ----------------------------------------


def test_crash_after_external_dispatch_keeps_action(db_url, platform_root):
    job_id = enqueue(db_url, platform_root)
    worker, _ = make_worker(db_url, lambda i, s: fakes.dispatch_deploy(i, s, crash_after_dispatch=True),
                            lease_seconds=1)
    with pytest.raises(SystemExit):
        worker.run_once()
    with connect(db_url) as conn:
        open_actions = actions.open_actions_for_job(conn, job_id)
    assert [a.kind for a in open_actions] == [WORKFLOW_DISPATCH]
    assert open_actions[0].status == "pending"
    assert job(db_url, job_id)["state"] == "acting"


def test_resume_follows_same_workflow_instead_of_new_dispatch(db_url, platform_root):
    job_id = enqueue(db_url, platform_root)
    worker, _ = make_worker(db_url, lambda i, s: fakes.dispatch_deploy(i, s, crash_after_dispatch=True),
                            lease_seconds=1)
    with pytest.raises(SystemExit):
        worker.run_once()
    time.sleep(1.2)

    run_ref = ExternalRef(system="github-actions", ref="https://github.com/x/y/actions/runs/1")
    reconciler = fakes.StaticReconciler(ReconcileStatus.RUNNING, "Lauf in_progress", [run_ref])
    dispatches = []

    def resumed(inv, s):
        assert inv.resume is not None and inv.state == JobState.ACTING
        for action in inv.resume.open_actions:
            assert action.external_refs == [run_ref]  # denselben Lauf weiterverfolgen
        if not inv.resume.open_actions:
            dispatches.append(s.record_action(WORKFLOW_DISPATCH, "app-deploy.yml"))
        return AgentResult(proposed_state="needs_attention", reason="Test")

    worker2, executor2 = make_worker(db_url, resumed, reconciler)
    worker2.run_once()
    assert len(reconciler.calls) == 1 and dispatches == []
    with connect(db_url) as conn:
        count = conn.execute("SELECT count(*) AS n FROM actions WHERE kind = %s", (WORKFLOW_DISPATCH,)).fetchone()["n"]
        status = conn.execute("SELECT status FROM actions").fetchone()["status"]
    assert count == 1 and status == "started"


def test_ambiguous_outcome_ends_in_needs_attention(db_url, platform_root):
    job_id = enqueue(db_url, platform_root)
    worker, _ = make_worker(db_url, lambda i, s: fakes.dispatch_deploy(i, s, crash_after_dispatch=True),
                            lease_seconds=1)
    with pytest.raises(SystemExit):
        worker.run_once()
    time.sleep(1.2)
    reconciler = fakes.StaticReconciler(ReconcileStatus.AMBIGUOUS, "kein Lauf mit dieser actionId gefunden")
    worker2, executor2 = make_worker(db_url, fakes.restart_and_resolve, reconciler)
    worker2.run_once()
    j = job(db_url, job_id)
    assert j["state"] == "needs_attention" and j["state_reason"].startswith("Klärung erforderlich")
    assert executor2.invocations == []  # keine weitere Änderung
    with connect(db_url) as conn:
        assert conn.execute("SELECT status FROM actions").fetchone()["status"] == "unclear"


# --- 1.2 Dispatch ohne doppelte Ziel-Sperre ------------------------------------------------


def test_dispatch_requires_released_target_lock(db_url, platform_root):
    enqueue(db_url, platform_root)
    c = claim(db_url)
    s = services_for(db_url, c)
    s.transition(JobState.INVESTIGATING, "x")
    s.transition(JobState.ACTING, "x")
    s.acquire_target_lock()
    with pytest.raises(NestedTargetLock):
        s.record_action(WORKFLOW_DISPATCH, "app-deploy.yml")
    s.release_target_lock()
    action_id = s.record_action(WORKFLOW_DISPATCH, "app-deploy.yml", correlation="corr-1")

    # Der dispatchte Workflow erwirbt die Sperre selbst unter der operationId des Agenten.
    with connect(db_url) as conn:
        handle = locks.try_acquire(conn, app_id="affine", environment="staging", operation_id=s.operation_id,
                                   owner_ref="github-run:x/1/1", kind="deploy")
        wf_action = actions.record_action(conn, handle, operation_id=s.operation_id, kind="pulumi-up",
                                          target="affine/staging", correlation=action_id)
        actions.update_status(conn, handle, wf_action, "succeeded")
        locks.release(conn, handle, operation_status="completed")
        rows = conn.execute("SELECT owner_ref FROM operation_locks").fetchall()
    assert rows == []


def test_mutating_agent_action_requires_target_lock(db_url, platform_root):
    enqueue(db_url, platform_root)
    c = claim(db_url)
    s = services_for(db_url, c)
    with pytest.raises(LockNotHeld):
        s.record_action("k8s-restart", "affine")
    s.record_action("k8s-logs", "affine", mutating=False)


# --- 4.3 Konfliktregel für den Agenten ----------------------------------------------------


def test_agent_does_not_wait_for_pipeline_lock(db_url, platform_root):
    job_id = enqueue(db_url, platform_root)
    with connect(db_url) as conn:
        pipeline = locks.try_acquire(conn, app_id="affine", environment="staging", operation_id="op-pipeline",
                                     owner_ref="github-run:x/9/1", kind="deploy")
    worker, _ = make_worker(db_url, fakes.restart_and_resolve)
    start = time.monotonic()
    worker.run_once()
    assert time.monotonic() - start < 5
    j = job(db_url, job_id)
    assert j["state"] == "needs_attention"
    assert j["state_reason"] == "gesperrt durch Operation op-pipeline"
    with connect(db_url) as conn:
        assert conn.execute("SELECT count(*) AS n FROM actions WHERE mutating").fetchone()["n"] == 0
        assert locks.status(conn, "affine", "staging")["owner_ref"] == pipeline.owner_ref


# --- 4.6 Sperrverlust ----------------------------------------------------------------------


def test_lost_job_lease_stops_agent_writes(db_url, platform_root):
    enqueue(db_url, platform_root)
    c = claim(db_url, lease_seconds=1)
    s = services_for(db_url, c)
    s.transition(JobState.INVESTIGATING, "x")
    time.sleep(1.2)
    with pytest.raises(LeaseLost):
        s.acquire_target_lock()
    with pytest.raises(LeaseLost):
        s.record_action("k8s-scale", "affine")
