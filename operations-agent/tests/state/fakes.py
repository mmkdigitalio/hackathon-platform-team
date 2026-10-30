"""Vertragskonforme Testausführung ohne Modell, eigene Queue oder Zustandsdatenbank."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from contracts import (
    WORKFLOW_DISPATCH,
    ActionSnapshot,
    AgentInvocation,
    AgentResult,
    ExternalRef,
    Finding,
    JobState,
    OperationServices,
    ReconcileResult,
    ReconcileStatus,
    Release,
)

RELEASE = Release(git_sha="3f9c2e1", image_digest="sha256:" + "a" * 64, chart_version="0.24.1")
OTHER_RELEASE = Release(git_sha="0000000", image_digest="sha256:" + "b" * 64, chart_version="0.23.0")


class ScriptedExecutor:
    """Führt eine Testfunktion als 'Agent' aus und zählt Aufrufe."""

    def __init__(self, script: Callable[[AgentInvocation, OperationServices], AgentResult]):
        self.script = script
        self.invocations: list[AgentInvocation] = []

    def run(self, invocation, services):
        self.invocations.append(invocation)
        return self.script(invocation, services)


class StaticReconciler:
    def __init__(self, status=ReconcileStatus.RUNNING, reason="läuft", refs=None):
        self.result = ReconcileResult(status=status, reason=reason, external_refs=refs or [])
        self.calls: list[ActionSnapshot] = []

    def reconcile(self, action):
        self.calls.append(action)
        return self.result


def now() -> datetime:
    return datetime.now(UTC)


def verify_ok(services, release=RELEASE, *, test_started=None, k8s_at=None):
    """Positiver Smoke-Test und Kubernetes-Zustand nach der Maßnahme."""
    t = test_started or now()
    smoke = services.record_verification(
        "smoke-test", success=True, release=release, started_at=t,
        finished_at=t + timedelta(seconds=5), check_ref="sha256:" + "c" * 64,
        evidence={"httpStatus": 200},
    )
    k8s = services.record_verification(
        "k8s-state", success=True, release=release, started_at=k8s_at or t + timedelta(seconds=6),
        evidence={"readyReplicas": 1},
    )
    return [smoke, k8s]


def restart_and_resolve(invocation, services) -> AgentResult:
    """Normaler Lauf: Diagnose, Sperre, Maßnahme, Verifikation, Abschlussantrag."""
    t0 = now()
    services.record_tool_call("k8s.get_deployment", {"name": "affine"}, ok=True,
                              result_summary="0/0 Replicas", started_at=t0, finished_at=now())
    services.record_finding(Finding(statement="Deployment hat 0 Replicas", confirmed=True,
                                    evidence_refs=["tool_call:k8s.get_deployment"]))
    services.transition(JobState.ACTING, "Neustart")
    services.acquire_target_lock()
    action = services.record_action("k8s-scale", "affine-staging/deployment/affine")
    services.action_started(action, ExternalRef(system="kubernetes", ref="affine-staging/deployment/affine"))
    services.action_finished(action, True, "auf 1 Replica skaliert")
    services.release_target_lock()
    services.transition(JobState.VERIFYING, "Prüfung")
    verification_ids = verify_ok(services)
    return AgentResult(
        proposed_state="resolved",
        reason="Sollzustand wiederhergestellt",
        findings=[Finding(statement="Ursache vermutlich manuelle Skalierung", confirmed=False)],
        action_ids=[action],
        verification_ids=verification_ids,
        expected_release=RELEASE,
        model_settings={"provider": "test", "model": "scripted"},
        code_version="test-sha",
        config_ref="platform/environments/staging/affine.yaml@3f9c2e1",
    )


def dispatch_deploy(invocation, services, *, crash_after_dispatch=False) -> AgentResult:
    """Agent startet app-deploy.yml, ohne die Ziel-Sperre zu halten."""
    services.transition(JobState.ACTING, "Sollzustand über Deploy-Workflow wiederherstellen")
    action = services.record_action(WORKFLOW_DISPATCH, "app-deploy.yml",
                                     detail={"inputs": {"appId": "affine", "environment": "staging"}})
    # ... hier ruft D die GitHub-API auf; der Lauf wird angenommen ...
    if crash_after_dispatch:
        raise SystemExit("Prozessabbruch nach externer Annahme")
    services.action_started(action, ExternalRef(system="github-actions", ref="https://github.com/x/y/actions/runs/1"))
    return AgentResult(proposed_state="needs_attention", reason="Test endet hier", action_ids=[action])
