"""Regressionstests zu den Befunden des Codex-Reviews vom 30.10.2026."""

from __future__ import annotations

from datetime import timedelta

import pytest
from langchain_core.messages import HumanMessage
from pydantic import SecretStr

from agent.config import ModelSettings
from agent.limits import RunBudget, UsageCallback
from agent.outcome import valid_verification
from agent.recorder import RunRecorder
from contracts import KubernetesState, ReleaseRef, RunLimits, VerificationEvidence
from tools.common import PermissionDenied, UnclearOutcome, redact, register_secret
from tools.dispatch import mentions

from .conftest import World
from .fakes import FakeDeployment, ScriptedModel, call, now, past, say, script
from .test_runner import broken_config, broken_scaled_to_zero
from .test_tools import AFFINE, dispatcher, restarter

DIGEST = "sha256:" + "a" * 64


def evidence(success: bool, started) -> VerificationEvidence:
    k = KubernetesState(
        namespace="affine-staging", deployment="affine", desired_replicas=1, ready_replicas=1,
        image_digests=[DIGEST], restart_counts={}, rollout_complete=True, captured_at=started + timedelta(seconds=5),
    )
    return VerificationEvidence(
        app_id="affine", environment="staging", release=ReleaseRef(), check_ref="sha256:b", started_at=started,
        finished_at=started, success=success, job_name="smoke", action_id="a", kubernetes=k,
    )


# 1: laufender Workflow verhindert resolved, auch bei positivem Zwischentest
def test_running_workflow_blocks_resolved(world: World) -> None:
    broken_scaled_to_zero(world)
    world.github.polls_until_done = 10_000
    model = ScriptedModel(responder=script(call("redeploy_desired_state", {"reason": "0 Replicas"}), call("run_smoke_test"), say("fertig")))
    result = world.run(model, world.invocation(limits=RunLimits(max_runtime_seconds=3600)))
    assert result.verification and result.verification[-1].success
    assert result.proposal == "needs_attention" and "noch nicht abgeschlossen" in result.reason


# 2: verlorene Patch-Antwort wird am Template abgeglichen, kein zweiter Patch
def test_restart_lost_response_is_reconciled(world: World) -> None:
    world.cluster.lose_patch_response = True
    res = restarter(world).run(AFFINE, "affine", "op-1", world.clock() + 300)
    assert res.rollout == "complete" and res.action.status == "succeeded"
    assert world.cluster.calls.count(("patch_deployment", "affine-staging")) == 1


def test_restart_lost_response_unreadable_is_unclear(world: World) -> None:
    world.cluster.lose_patch_response = True
    world.cluster.on_patch = lambda c, ns, name: c.timeouts.add(("read_deployment", ns))
    with pytest.raises(UnclearOutcome):
        restarter(world).run(AFFINE, "affine", "op-1", world.clock() + 300)


# 3: ein angestoßener Neustart entwertet ältere positive Tests
def test_attempted_mutation_invalidates_earlier_evidence(world: World) -> None:
    def respond(msgs, i):
        if i == 1:
            world.cluster.forbid.add(("patch_deployment", "affine-staging"))
        return [call("run_smoke_test"), call("restart_workload", {"reason": "x"}), say("fertig")][min(i, 2)]

    result = world.run(ScriptedModel(responder=respond))
    restart = next(c for c in result.tool_calls if c.name == "restart_workload")
    assert restart.error_kind == "permission"
    # Runner verifiziert nach dem Versuch erneut; erst dieser Test zählt.
    assert len(result.verification) == 2 and result.verification[-1].started_at > result.verification[0].started_at


# 4: unvollständige Laufliste erlaubt keinen erneuten Dispatch
def test_planned_dispatch_with_incomplete_run_list_is_unclear(world: World) -> None:
    for i in range(600):
        world.github.add_run(f"deploy affine staging op=op-{i} action=act-x{i}")
    a = world.actions.record_planned("op-1", "workflow_dispatch", "t", {"workflow": "app-deploy.yml"})
    world.actions.items[a.action_id] = a.model_copy(update={"created_at": past(10)})
    with pytest.raises(UnclearOutcome):
        dispatcher(world).reconcile(AFFINE, world.actions.get(a.action_id), world.clock() + 600)


def test_planned_dispatch_without_run_after_grace_is_not_executed(world: World) -> None:
    a = world.actions.record_planned("op-1", "workflow_dispatch", "t", {"workflow": "app-deploy.yml"})
    world.actions.items[a.action_id] = a.model_copy(update={"created_at": past(10)})
    assert dispatcher(world).reconcile(AFFINE, world.actions.get(a.action_id), world.clock() + 600) is None


# 5: der jüngste Nachweis nach der Maßnahme entscheidet
def test_latest_negative_evidence_wins(world: World) -> None:
    from agent.context import OperationalContext, Sourced

    rec = RunRecorder()
    t0 = now()
    rec.verification = [evidence(True, t0 + timedelta(seconds=1)), evidence(False, t0 + timedelta(seconds=60))]
    ctx = OperationalContext(AFFINE, "main", Sourced({}, ""), Sourced({"image": {"digest": DIGEST}}, ""), "", None, None, None, None, [])
    assert valid_verification(rec, ctx) is None
    rec.verification.append(evidence(True, t0 + timedelta(seconds=120)))
    assert valid_verification(rec, ctx) is not None


# 6: Secrets in Logzeilen werden maskiert
def test_log_secrets_are_redacted(world: World) -> None:
    broken_config(world)
    world.cluster.logs["affine-staging"] = (
        "ERROR db connect failed postgresql://affine:S3cretPw!@db.example:5432/affine\n"
        "ERROR invalid token=ghp_abcdefghijkl and Authorization: Bearer eyJabc.def\n"
    )
    result = world.run(ScriptedModel(responder=script(call("get_logs"), say("x"))))
    dumped = " ".join(result.references["logLines"])
    assert "S3cretPw" not in dumped and "ghp_abcdefghijkl" not in dumped and "eyJabc" not in dumped
    register_secret("very-secret-value-123")
    assert redact("x very-secret-value-123 y") == "x *** y"


# 7: wiedergefundene geplante Maßnahme zählt als Reparaturversuch
def test_reconciled_planned_restart_counts_as_repair(world: World) -> None:
    broken_config(world)
    a = world.actions.record_planned("op-1", "restart", "affine-staging/deployment/affine", {})
    world.cluster.deployments[("affine-staging", "affine")].annotations["ops.mmk/action-id"] = a.action_id
    world.cluster.deployments[("affine-staging", "affine")] = FakeDeployment(
        replicas=1, ready=1, digest=DIGEST, annotations={"ops.mmk/action-id": a.action_id}
    )
    model = ScriptedModel(responder=lambda msgs, i: call("restart_workload", {"reason": f"Versuch {i}"}, n=i))
    result = world.run(model, world.invocation(limits=RunLimits(max_repair_attempts=2, max_runtime_seconds=3600)))
    assert result.limit_reached == "repair_attempts"
    assert world.cluster.calls.count(("patch_deployment", "affine-staging")) == 1


# 8: actionId nur als ganzes Token
def test_action_id_matching_is_exact() -> None:
    assert mentions("deploy op=op-1 action=act-0001", "act-0001")
    assert not mentions("deploy op=op-1 action=act-0001-other", "act-0001")
    assert not mentions("deploy op=op-12 action=x", "op-1")


# 9: Runner-Verifikation zählt gegen die Tool-Grenze und wird protokolliert
def test_runner_verification_respects_tool_limit(world: World) -> None:
    result = world.run(ScriptedModel(responder=script(call("get_events"), say("ok"))), world.invocation(limits=RunLimits(max_tool_calls=1)))
    assert result.limit_reached == "tool_calls" and result.verification == []


def test_runner_verification_is_recorded(world: World) -> None:
    result = world.run(ScriptedModel(responder=script(say("ok"))))
    assert [c.name for c in result.tool_calls] == ["run_smoke_test"]


# 10: Verbrauch auch außerhalb der Middleware-Kette
def test_usage_callback_counts_direct_model_calls() -> None:
    ms = ModelSettings(provider="anthropic", model="m", api_key=SecretStr("k"), price_input_per_mtok=3, price_output_per_mtok=15)
    budget = RunBudget(RunLimits(), ms)
    ScriptedModel(responder=script(say("Zusammenfassung", usage=(2000, 300)))).invoke(
        [HumanMessage("x")], config={"callbacks": [UsageCallback(budget)]}
    )
    assert budget.model_calls == 1 and budget.input_tokens == 2000


def test_permission_error_type_still_structured(world: World) -> None:
    world.cluster.forbid.add(("read_logs", "affine-staging"))
    with pytest.raises(PermissionDenied):
        world.k8s().logs(AFFINE, "affine")
