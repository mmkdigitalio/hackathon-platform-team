"""Vertragskonforme Testausführung nach ``contracts.v0_1`` ohne Modell, Queue oder eigene Datenbank."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from contracts import (
    AgentInvocation,
    AgentResult,
    Finding,
    KubernetesState,
    OperationsServices,
    ReleaseRef,
    ToolCallRecord,
    Usage,
    VerificationEvidence,
)

DIGEST = "sha256:" + "a" * 64
OTHER_DIGEST = "sha256:" + "b" * 64
RELEASE = ReleaseRef(git_sha="3f9c2e1", image_digest=DIGEST, chart_version="0.24.1")
OTHER_RELEASE = ReleaseRef(git_sha="0000000", image_digest=OTHER_DIGEST, chart_version="0.23.0")
DESIRED = {"path": "platform/environments/staging/affine.yaml", "gitRef": "3f9c2e1", "source": "github", "imageDigest": DIGEST}


class ScriptedExecutor:
    """Führt eine Testfunktion als 'Agent' aus und zählt Aufrufe."""

    def __init__(self, script: Callable[[AgentInvocation, OperationsServices], AgentResult]):
        self.script = script
        self.invocations: list[AgentInvocation] = []

    def __call__(self, invocation, services):
        self.invocations.append(invocation)
        return self.script(invocation, services)


def now() -> datetime:
    return datetime.now(UTC)


def usage() -> Usage:
    return Usage(provider="test", model="scripted", model_calls=3, input_tokens=1000, output_tokens=200, cost_usd=0.01)


def result(inv: AgentInvocation, proposal="needs_attention", reason="Test", **kwargs) -> AgentResult:
    return AgentResult(job_id=inv.job_id, proposal=proposal, reason=reason, usage=usage(), **kwargs)


def evidence(inv, *, success=True, release=RELEASE, started=None, k8s_at=None, k8s_digests=None, ready=1) -> VerificationEvidence:
    t = started or now()
    return VerificationEvidence(
        app_id=inv.app_id,
        environment=inv.environment,
        release=release,
        check_ref="sha256:" + "c" * 64,
        started_at=t,
        finished_at=t + timedelta(seconds=5),
        success=success,
        exit_code=0 if success else 1,
        job_name="smoke-test-1",
        action_id="act-smoke",
        result={"success": success},
        kubernetes=KubernetesState(
            namespace=f"{inv.app_id}-{inv.environment}",
            deployment=inv.app_id,
            desired_replicas=1,
            ready_replicas=ready,
            image_digests=k8s_digests or [release.image_digest],
            restart_counts={},
            rollout_complete=ready >= 1,
            captured_at=k8s_at or t + timedelta(seconds=6),
        ),
        evidence_refs=["k8s://affine-staging/job/smoke-test-1"],
    )


def restart_and_resolve(inv: AgentInvocation, s: OperationsServices) -> AgentResult:
    """Normaler Lauf wie D's Neustart: Sperre, Aktion, Rollout, Freigabe, Verifikation."""
    t0 = now()
    findings = [
        Finding(statement="Deployment hat 0 Replicas", certainty="confirmed", provenance="tool:get_workload_state",
                evidence_refs=["k8s://affine-staging/deployment/affine"], observed_at=t0),
        Finding(statement="Ursache vermutlich manuelle Skalierung", certainty="hypothesis",
                provenance="model", observed_at=t0),
    ]
    assert s.state.request(inv.job_id, "acting", "Neustart")
    acq = s.lock.try_acquire(inv.app_id, inv.environment, inv.operation_id)
    if not acq.acquired:
        return result(inv, reason=f"gesperrt durch Operation {acq.holder_operation_id}")
    action = s.actions.record_planned(inv.operation_id, "restart", "affine-staging/deployment/affine",
                                      {"annotation": "ops.mmk/action-id"})
    s.ownership.assert_valid()
    s.actions.update(action.action_id, "started", {"generation": "7"})
    s.actions.update(action.action_id, "succeeded", {"rollout": "complete"})
    s.lock.release(inv.app_id, inv.environment, inv.operation_id)
    assert s.state.request(inv.job_id, "verifying", "Smoke-Test")
    return result(
        inv,
        "resolved",
        "Positiver Smoke-Test nach der letzten Maßnahme",
        findings=findings,
        actions=[action.action_id],
        tool_calls=[ToolCallRecord(name="get_workload_state", started_at=t0, duration_ms=12, status="ok",
                                   target="affine-staging/affine")],
        verification=[evidence(inv)],
        references={"desiredState": DESIRED},
    )


def dispatch_deploy(inv: AgentInvocation, s: OperationsServices, *, crash_after_dispatch=False) -> AgentResult:
    """Wie D's WorkflowDispatcher: Sperre nur prüfen, Aktion planen, Workflow starten."""
    assert s.state.request(inv.job_id, "acting", "Sollzustand über Deploy-Workflow wiederherstellen")
    holder = s.lock.holder(inv.app_id, inv.environment)
    if holder and holder != inv.operation_id:
        return result(inv, reason=f"gesperrt durch Operation {holder}")
    action = s.actions.record_planned(inv.operation_id, "workflow_dispatch", "app-deploy.yml:affine/staging",
                                      {"workflow": "app-deploy.yml", "operationId": inv.operation_id})
    # ... hier ruft D die GitHub-API auf; der Lauf wird angenommen ...
    if crash_after_dispatch:
        raise SystemExit("Prozessabbruch nach externer Annahme")
    s.actions.update(action.action_id, "started", {"workflow": "app-deploy.yml", "runId": "1"})
    return result(inv, actions=[action.action_id])
