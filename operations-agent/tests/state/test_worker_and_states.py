"""Tasks 3.1 bis 3.4, 4.1, 4.3, 4.6, 4.7 und das Dispatch-Beispiel aus 1.2 (Vertrag contracts.v0_1)."""

import threading
import time
import uuid
from datetime import timedelta

import pytest

from api import normalize
from api.catalog import Catalog
from contracts import OwnershipLost, RunLimits
from contracts.errors import LeaseLost, NestedTargetLock, TransitionRejected
from contracts.states import JobState
from persistence import actions, intake, jobs, locks
from persistence.db import connect
from worker.runner import Worker, WorkerConfig
from worker.services import build_services

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


def make_worker(db_url, script, **config) -> tuple[Worker, fakes.ScriptedExecutor]:
    executor = fakes.ScriptedExecutor(script)
    cfg = WorkerConfig(worker_id=config.pop("worker_id", f"w-{uuid.uuid4().hex[:6]}"),
                       limits=RunLimits, **config)
    return Worker(connector(db_url), executor, cfg), executor


def claim(db_url, worker_id="w1", lease_seconds=60):
    with connect(db_url) as conn:
        return jobs.claim_next(conn, worker_id, lease_seconds)


def services_for(db_url, c: jobs.Claim, operation_id="op-test"):
    with connect(db_url) as conn:
        locks.ensure_operation(conn, operation_id, app_id="affine", environment="staging", kind="agent",
                               owner_ref=c.lease.lock_owner_ref, job_id=c.lease.job_id)
    services, _ = build_services(connector(db_url), c.lease, c.job, lock_ttl_seconds=300)
    return services


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

    def script(inv, s):
        calls.append(inv.job_id)
        time.sleep(0.3)
        return fakes.result(inv)

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
    stale = services_for(db_url, first)
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
    with pytest.raises(OwnershipLost):
        stale.ownership.assert_valid()
    with pytest.raises(OwnershipLost):
        stale.actions.record_planned("op-test", "smoke_test", "affine-staging/job", {})
    with pytest.raises(OwnershipLost):
        stale.state.request(job_id, "investigating", "veraltet")
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
    with connect(db_url) as conn:
        assert locks.status(conn, "affine", "staging") is None  # Sperre freigegeben


def test_investigation_without_action_may_resolve(db_url, platform_root):
    """Ohne Maßnahme: Worker führt nach verifying, Nachweis nach Beginn der Untersuchung genügt."""
    job_id = enqueue(db_url, platform_root)

    def script(inv, s):
        return fakes.result(inv, "resolved", "Ziel bereits gesund", verification=[fakes.evidence(inv)],
                            references={"desiredState": fakes.DESIRED})

    make_worker(db_url, script)[0].run_once()
    assert job(db_url, job_id)["state"] == "resolved"


def test_repeated_verifying_and_second_attempt(db_url, platform_root):
    """D beantragt verifying mehrfach und nach negativem Test erneut acting."""
    job_id = enqueue(db_url, platform_root)
    c = claim(db_url)
    s = services_for(db_url, c)
    assert s.state.request(job_id, "investigating", "x") is True  # queued -> investigating
    for state in ("acting", "verifying", "verifying", "acting", "verifying"):
        assert s.state.request(job_id, state, "x") is True
    path = [t["to_state"] for t in transitions(db_url, job_id) if t["accepted"]]
    assert path[-4:] == ["verifying", "investigating", "acting", "verifying"]


def test_transition_service_rejects_and_logs(db_url, platform_root):
    job_id = enqueue(db_url, platform_root)
    c = claim(db_url)
    s = services_for(db_url, c)
    assert s.state.request(job_id, "acting", "aus queued nicht erlaubt") is False
    assert s.state.request("anderer-auftrag", "investigating", "x") is False
    last = transitions(db_url, job_id)[-1]
    assert last["accepted"] is False and last["to_state"] == "acting"
    assert job(db_url, job_id)["state"] == "queued"


@pytest.mark.parametrize("from_path,target", [
    ([], JobState.RESOLVED),
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


def _restart_then(evidence_fn, references=None):
    """Neustart als Maßnahme, danach der übergebene Nachweis."""

    def script(inv, s):
        s.state.request(inv.job_id, "acting", "Neustart")
        assert s.lock.try_acquire(inv.app_id, inv.environment, inv.operation_id).acquired
        a = s.actions.record_planned(inv.operation_id, "restart", "affine-staging/deployment/affine", {})
        before = fakes.now() - timedelta(seconds=1)
        time.sleep(0.05)
        s.actions.update(a.action_id, "succeeded", {"rollout": "complete"})
        s.lock.release(inv.app_id, inv.environment, inv.operation_id)
        s.state.request(inv.job_id, "verifying", "Prüfung")
        return fakes.result(inv, "resolved", "Agent meint: gelöst", actions=[a.action_id],
                            verification=evidence_fn(inv, before),
                            references={"desiredState": fakes.DESIRED} if references is None else references)

    return script


@pytest.mark.parametrize("evidence_fn,references,reason", [
    (lambda inv, before: [fakes.evidence(inv, started=before)], None, "vor der letzten Maßnahme"),
    (lambda inv, before: [fakes.evidence(inv).model_copy(update={"kubernetes": None})], None, "Kubernetes-Zustand"),
    (lambda inv, before: [fakes.evidence(inv, k8s_digests=[fakes.OTHER_DIGEST])], None, "Kubernetes-Zustand zeigt Release"),
    (lambda inv, before: [fakes.evidence(inv, release=fakes.OTHER_RELEASE)], None, "Test lief gegen Release"),
    (lambda inv, before: [fakes.evidence(inv)], {}, "erwarteter Zielstand"),
    (lambda inv, before: [], None, "kein positiver fachlicher Test"),
], ids=["test-vor-massnahme", "ohne-k8s", "falscher-release-k8s", "falscher-release-test", "ohne-sollzustand", "ohne-test"])
def test_resolved_rejected_without_matching_evidence(db_url, platform_root, evidence_fn, references, reason):
    """Modell/Agent beantragt resolved ohne passenden Nachweis: abgelehnt, protokolliert, sichtbar ungelöst."""
    job_id = enqueue(db_url, platform_root)
    make_worker(db_url, _restart_then(evidence_fn, references))[0].run_once()
    j = job(db_url, job_id)
    assert j["state"] == "needs_attention"
    assert j["state_reason"].startswith("Abschluss abgelehnt") and reason in j["state_reason"]
    assert any(not t["accepted"] and t["to_state"] == "resolved" for t in transitions(db_url, job_id))


# --- 3.3 Aufruf über den Vertrag ------------------------------------------------------------


def test_executor_gets_contract_invocation(db_url, platform_root):
    job_id = enqueue(db_url, platform_root)
    worker, executor = make_worker(db_url, lambda inv, s: fakes.result(inv, "failed", "x"),
                                   context_git_ref="3f9c2e1")
    worker.run_once()
    inv = executor.invocations[0]
    assert inv.contract_version == "0.1" and inv.job_id == job_id
    assert inv.operation_id.startswith("op-")
    assert inv.ownership.lease_version == 1
    assert inv.context.git_ref == "3f9c2e1" and inv.context.event_type == "health-check-failed"
    assert inv.resume.thread_id == job_id and inv.resume.open_actions == []
    assert job(db_url, job_id)["state"] == "failed"
    assert worker.run_once() is None  # keine zweite Queue: nichts mehr zu tun


def test_alert_and_issue_context(db_url, platform_root):
    import json

    from .conftest import REPO_ROOT

    payloads = REPO_ROOT / "platform" / "schemas" / "events" / "examples" / "payloads"
    raw = (payloads / "github-issue-opened.json").read_bytes()
    event = normalize.github("issues", "d-ctx", json.loads(raw), raw, Catalog(platform_root),
                             {"mmkdigitalio/hackathon-platform-team"})
    with connect(db_url) as conn:
        intake.store_event(conn, event, json.loads(raw))
    worker, executor = make_worker(db_url, lambda inv, s: fakes.result(inv))
    worker.run_once()
    ctx = executor.invocations[0].context
    assert ctx.issue.repository == "mmkdigitalio/hackathon-platform-team" and ctx.issue.number == 42
    assert "### App" in ctx.issue_text and ctx.alert_payload is None


def test_executor_exception_fails_job(db_url, platform_root):
    job_id = enqueue(db_url, platform_root)

    def boom(inv, s):
        raise RuntimeError("Adapter kaputt")

    make_worker(db_url, boom)[0].run_once()
    assert job(db_url, job_id)["state"] == "failed"
    assert "Adapter kaputt" in job(db_url, job_id)["state_reason"]


def test_required_change_is_visible(db_url, platform_root):
    job_id = enqueue(db_url, platform_root)

    def script(inv, s):
        return fakes.result(inv, "needs_attention", "Verifikation nach erlaubten Maßnahmen negativ",
                            required_change="Sollzustand per Pull Request korrigieren (Commit abc123)")

    make_worker(db_url, script)[0].run_once()
    assert "Commit abc123" in job(db_url, job_id)["state_reason"]


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
        return fakes.result(inv)

    worker, _ = make_worker(db_url, slow, worker_id="hb", heartbeat_interval_seconds=0.2)
    worker.start_heartbeat()
    try:
        t = threading.Thread(target=worker.run_once)
        t.start()
        for _ in range(4):
            time.sleep(0.25)
            with connect(db_url) as conn:
                beats.append(conn.execute(
                    "SELECT extract(epoch FROM now() - beat_at) AS age, current_job_id FROM worker_heartbeats"
                ).fetchone())
        t.join()
    finally:
        worker.stop_heartbeat()
    assert all(float(b["age"]) < 0.6 for b in beats)
    assert any(b["current_job_id"] is not None for b in beats)


# --- 4.1 / 4.7 Aktionsprotokoll und Wiederaufnahme ----------------------------------------


def _crash_after_dispatch(db_url, platform_root):
    job_id = enqueue(db_url, platform_root)
    worker, first = make_worker(db_url, lambda i, s: fakes.dispatch_deploy(i, s, crash_after_dispatch=True),
                                lease_seconds=1)
    with pytest.raises(SystemExit):
        worker.run_once()
    return job_id, first.invocations[0]


def test_crash_after_external_dispatch_keeps_action(db_url, platform_root):
    job_id, _ = _crash_after_dispatch(db_url, platform_root)
    with connect(db_url) as conn:
        open_actions = actions.open_actions_for_job(conn, job_id)
    assert [a.kind for a in open_actions] == ["workflow_dispatch"]
    assert open_actions[0].status == "planned"
    assert job(db_url, job_id)["state"] == "acting"


def test_resume_hands_open_action_and_same_thread_to_agent(db_url, platform_root):
    """D gleicht offene Aktionen selbst ab; C liefert sie mit stabiler Identität und gleicher thread_id."""
    job_id, first = _crash_after_dispatch(db_url, platform_root)
    time.sleep(1.2)
    dispatches = []

    def resumed(inv, s):
        assert [a.kind for a in inv.resume.open_actions] == ["workflow_dispatch"]
        open_action = inv.resume.open_actions[0]
        # D's Abgleich findet den Lauf über die actionId und verfolgt ihn weiter.
        s.actions.update(open_action.action_id, "started", {"runId": "1", "reconciled": "über actionId zugeordnet"})
        dispatches.extend(a for a in s.actions.list_for_operation(inv.operation_id) if a.kind == "workflow_dispatch")
        return fakes.result(inv)

    worker2, executor2 = make_worker(db_url, resumed)
    worker2.run_once()
    inv = executor2.invocations[0]
    assert inv.operation_id == first.operation_id
    assert inv.resume.thread_id == first.resume.thread_id == job_id
    assert inv.ownership.lease_version == 2
    assert len(dispatches) == 1 and dispatches[0].external_refs["runId"] == "1"


def test_unclear_action_blocks_new_owner(db_url, platform_root):
    """Mehrdeutiger Ausgang: D meldet unclear und needs_attention; Sperre wird nicht blind neu vergeben."""
    job_id, first = _crash_after_dispatch(db_url, platform_root)
    time.sleep(1.2)

    def resumed(inv, s):
        a = inv.resume.open_actions[0]
        s.actions.update(a.action_id, "unclear", {"reason": "mehrere Läufe tragen dieselbe actionId"})
        return fakes.result(inv, reason=f"Workflow-Lauf für {a.action_id} unklar")

    make_worker(db_url, resumed)[0].run_once()
    j = job(db_url, job_id)
    assert j["state"] == "needs_attention" and "unklar" in j["state_reason"]
    # Ein Workflow unter derselben Operation bekommt die Sperre, eine fremde Operation nach Ablauf nicht.
    with connect(db_url) as conn:
        handle = locks.try_acquire(conn, app_id="affine", environment="staging", operation_id=first.operation_id,
                                   owner_ref="github-run:x/1/1", kind="deploy", ttl_seconds=1)
    time.sleep(1.2)
    from contracts.errors import UnresolvedActions

    with connect(db_url) as conn, pytest.raises(UnresolvedActions):
        locks.try_acquire(conn, app_id="affine", environment="staging", operation_id="op-other",
                          owner_ref="github-run:x/2/1", kind="deploy")
    assert handle.operation_id == first.operation_id


# --- 1.2 Dispatch ohne doppelte Ziel-Sperre ------------------------------------------------


def test_dispatch_requires_released_target_lock(db_url, platform_root):
    job_id = enqueue(db_url, platform_root)
    c = claim(db_url)
    s = services_for(db_url, c)
    s.state.request(job_id, "investigating", "x")
    s.state.request(job_id, "acting", "x")
    assert s.lock.try_acquire("affine", "staging", "op-test").acquired
    with pytest.raises(NestedTargetLock):
        s.actions.record_planned("op-test", "workflow_dispatch", "app-deploy.yml", {})
    s.lock.release("affine", "staging", "op-test")
    dispatch = s.actions.record_planned("op-test", "workflow_dispatch", "app-deploy.yml", {"workflow": "app-deploy.yml"})

    # Der dispatchte Workflow erwirbt die Sperre selbst unter der operationId des Agenten.
    with connect(db_url) as conn:
        handle = locks.try_acquire(conn, app_id="affine", environment="staging", operation_id="op-test",
                                   owner_ref="github-run:x/1/1", kind="deploy")
        assert s.lock.holder("affine", "staging") == "op-test"  # D darf dispatchen/weiterverfolgen
        assert not s.lock.holds("affine", "staging", "op-test")  # hält aber selbst keine Sperre
        wf = actions.record_action(conn, handle, operation_id="op-test", kind="pulumi-up", target="affine/staging",
                                   correlation={"agentActionId": dispatch.action_id})
        actions.update_status(conn, handle, wf.action_id, "succeeded")
        locks.release(conn, handle, operation_status="completed")
        assert conn.execute("SELECT count(*) AS n FROM operation_locks").fetchone()["n"] == 0


def test_mutating_agent_action_requires_target_lock(db_url, platform_root):
    enqueue(db_url, platform_root)
    c = claim(db_url)
    s = services_for(db_url, c)
    with pytest.raises(OwnershipLost):
        s.actions.record_planned("op-test", "restart", "affine", {})
    s.actions.record_planned("op-test", "smoke_test", "affine-staging/job", {})


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


# --- 4.6 Besitzverlust ----------------------------------------------------------------------


def test_lost_job_lease_stops_agent_writes(db_url, platform_root):
    job_id = enqueue(db_url, platform_root)
    c = claim(db_url, lease_seconds=1)
    s = services_for(db_url, c)
    s.state.request(job_id, "investigating", "x")
    time.sleep(1.2)
    with pytest.raises(OwnershipLost):
        s.lock.try_acquire("affine", "staging", "op-test")
    with pytest.raises(OwnershipLost):
        s.actions.record_planned("op-test", "restart", "affine", {})
    with connect(db_url) as conn:
        assert locks.status(conn, "affine", "staging") is None


def test_ownership_lost_during_execution_writes_nothing(db_url, platform_root):
    job_id = enqueue(db_url, platform_root)

    def slow(inv, s):
        time.sleep(1.3)  # Lease (1 s) läuft ab, Erneuerung wird hier unterdrückt
        return fakes.result(inv, "failed", "zu spät")

    worker, _ = make_worker(db_url, slow, lease_seconds=1)
    worker.config.renew_interval_seconds = 60
    worker.run_once()
    assert job(db_url, job_id)["state"] == "investigating"  # nichts geschrieben, wird wiederaufgenommen
