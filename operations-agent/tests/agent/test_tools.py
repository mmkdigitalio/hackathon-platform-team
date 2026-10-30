"""Adaptertests: positive Fälle, Zeitüberschreitung, Berechtigungsfehler und Zielbeschränkung."""

from __future__ import annotations

import httpx
import pytest

from contracts import IssueRef, OwnershipLost
from tools.common import LockBusy, NotAllowed, PermissionDenied, TimedOut, UnclearOutcome, ValidationFailed
from tools.dispatch import WorkflowDispatcher
from tools.github import AGENT_MARKER
from tools.http_check import HttpProbe
from tools.restart import RestartAction
from tools.targets import Target
from tools.verification import SmokeTest, job_name, parse_result

from .conftest import DESIRED, REPO, SMOKE_IMAGE, World

AFFINE = Target("affine", "staging")


# Zielbeschränkung


def test_foreign_namespace_rejected_before_request(world: World) -> None:
    with pytest.raises(ValidationFailed):
        world.policy().namespace("kube-system")
    with pytest.raises(ValidationFailed):
        world.policy().target("affine", "production")
    assert world.cluster.calls == []
    assert world.policy().namespace("operations-agent-staging") == Target("operations-agent", "staging")


def test_http_only_under_base_domain(world: World) -> None:
    with pytest.raises(NotAllowed):
        world.policy().check_url("https://evil.example.com/")


# Kubernetes


def test_workload_state_has_time_and_resource_reference(world: World) -> None:
    state, extra, refs = world.k8s().workload_state(AFFINE, "affine")
    assert state.ready_replicas == 1 and state.desired_replicas == 1 and state.rollout_complete
    assert state.image_digests == ["sha256:" + "a" * 64]
    assert refs[0].startswith("k8s://affine-staging/deployment/affine@")
    assert extra["pods"][0]["restarts"] == 0


def test_logs_forbidden_is_structured_error_not_empty_logs(world: World) -> None:
    world.cluster.forbid.add(("read_logs", "affine-staging"))
    with pytest.raises(PermissionDenied) as e:
        world.k8s().logs(AFFINE, "affine")
    assert e.value.details["status"] == 403


def test_kubernetes_timeout(world: World) -> None:
    world.cluster.timeouts.add(("list_events", "affine-staging"))
    with pytest.raises(TimedOut):
        world.k8s().events(AFFINE)


def test_logs_are_bounded(world: World) -> None:
    world.cluster.logs["affine-staging"] = "x" * 50_000
    logs, refs = world.k8s().logs(AFFINE, "affine", tail_lines=10_000)
    assert sum(len(line) for entry in logs for line in entry["lines"]) <= 16_000
    assert refs


# HTTP


def test_http_503_is_a_finding(world: World) -> None:
    world.http_status = 503
    result, refs = world.probe().check("affine", "/info")
    assert result["statusCode"] == 503 and result["healthy"] is False and result["errorType"] is None
    assert result["url"] == "https://affine.1-2-3-4.sslip.io/info" and "durationMs" in result and refs


def test_http_timeout_is_a_finding(world: World) -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("t", request=request)

    probe = HttpProbe(world.policy(), http=httpx.Client(transport=httpx.MockTransport(boom)))
    result, _ = probe.check("affine", "/info")
    assert result["errorType"] == "timeout" and result["healthy"] is False


# GitHub


def test_last_desired_state_commit(world: World) -> None:
    c = world.github.client().last_commit("platform/environments/staging/affine.yaml")
    assert c["sha"] == "c0ffee1234567890" and c["author"] == "kollegin" and c["date"] == "2026-10-30T08:00:00Z"
    assert "not-a-url" in c["diff"]


def test_read_file_at_ref(world: World) -> None:
    f = world.github.client().get_file("platform/environments/staging/affine.yaml", "main")
    assert f["content"] == DESIRED


def test_comment_has_marker_and_job_reference(world: World) -> None:
    world.github.client().comment_issue(IssueRef(repository=REPO, number=7), "job-9", "Befund")
    body = world.github.comments[0]["body"]
    assert body.startswith(AGENT_MARKER) and "job-9" in body


def test_dispatch_without_actions_permission(world: World) -> None:
    world.github.dispatch_mode = "forbidden"
    d = WorkflowDispatcher(world.github.client(), world.services, frozenset({"app-deploy.yml"}), sleep=world.clock.sleep, clock=world.clock)
    with pytest.raises(PermissionDenied) as e:
        d.dispatch(AFFINE, "app-deploy.yml", "op-1", world.clock() + 600)
    assert e.value.details["missing_permission"] == "actions=write"
    assert world.actions.of_kind("workflow_dispatch")[0].status == "failed"


def test_github_timeout(world: World) -> None:
    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("t", request=request)

    from tools.github import API, GitHubClient

    gh = GitHubClient("x", REPO, http=httpx.Client(base_url=API, transport=httpx.MockTransport(slow)))
    with pytest.raises(TimedOut):
        gh.get_file("a", "main")


# Workflow-Dispatch


def dispatcher(world: World) -> WorkflowDispatcher:
    return WorkflowDispatcher(world.github.client(), world.services, frozenset({"app-deploy.yml"}), sleep=world.clock.sleep, clock=world.clock)


def test_unlisted_workflow_rejected_without_github_call(world: World) -> None:
    with pytest.raises(NotAllowed):
        dispatcher(world).dispatch(AFFINE, "foundation-up.yml", "op-1", world.clock() + 600)
    assert world.github.requests == [] and world.actions.items == {}


def test_dispatch_correlates_by_action_id_and_reports_running(world: World) -> None:
    world.github.add_run("deploy affine staging op=op-1 action=act-other")  # zeitlich ähnlich, falsche actionId
    world.github.polls_until_done = 99
    d = dispatcher(world)
    wr = d.dispatch(AFFINE, "app-deploy.yml", "op-1", world.clock() + 600)
    assert wr.action.action_id in wr.run["title"]
    wr = d.wait(wr, world.clock() + 30)
    assert wr.state == "in_progress" and wr.action.status == "started"
    world.github.polls_until_done = 0
    done = d.wait(wr, world.clock() + 600)
    assert done.state == "success" and done.action.status == "succeeded"


def test_dispatch_timeout_after_accept_finds_run(world: World) -> None:
    world.github.dispatch_mode = "timeout_after_accept"
    wr = dispatcher(world).dispatch(AFFINE, "app-deploy.yml", "op-1", world.clock() + 600)
    assert wr.run is not None and wr.action.external_refs["runId"] == str(wr.run["id"])
    assert world.github.workflow_calls() == 1


def test_dispatch_timeout_without_run_is_unclear_and_not_repeated(world: World) -> None:
    world.github.dispatch_mode = "no_run"

    def lost(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/dispatches"):
            raise httpx.ReadTimeout("t", request=request)
        return world.github.handle(request)

    from tools.github import API, GitHubClient

    gh = GitHubClient("test-token", REPO, http=httpx.Client(base_url=API, transport=httpx.MockTransport(lost)))
    d = WorkflowDispatcher(gh, world.services, frozenset({"app-deploy.yml"}), sleep=world.clock.sleep, clock=world.clock)
    with pytest.raises(UnclearOutcome):
        d.dispatch(AFFINE, "app-deploy.yml", "op-1", world.clock() + 60)
    assert world.actions.of_kind("workflow_dispatch")[0].status == "unclear"


def test_dispatch_lock_held_elsewhere(world: World) -> None:
    world.lock.owners[("affine", "staging")] = "op-x"
    with pytest.raises(LockBusy) as e:
        dispatcher(world).dispatch(AFFINE, "app-deploy.yml", "op-1", world.clock() + 600)
    assert e.value.holder_operation_id == "op-x" and world.github.requests == []


def test_reconcile_by_action_id_without_new_dispatch(world: World) -> None:
    a = world.actions.record_planned("op-1", "workflow_dispatch", "t", {"workflow": "app-deploy.yml"})
    world.github.add_run(f"deploy affine staging op=op-1 action={a.action_id}")
    wr = dispatcher(world).reconcile(AFFINE, a, world.clock() + 600)
    assert wr is not None and wr.action.external_refs["runId"]
    assert world.github.workflow_calls() == 0


# Neustart


def restarter(world: World) -> RestartAction:
    return RestartAction(world.k8s(), world.services, world.clock.sleep, world.clock)


def test_restart_sets_annotation_under_lock(world: World) -> None:
    res = restarter(world).run(AFFINE, "affine", "op-1", world.clock() + 300)
    dep = world.cluster.deployments[("affine-staging", "affine")]
    assert dep.annotations["ops.mmk/action-id"] == res.action.action_id
    assert res.rollout == "complete" and res.action.status == "succeeded"
    assert world.lock.owners == {} and world.lock.acquire_calls == 1


def test_restart_without_lock_or_ownership_does_not_patch(world: World) -> None:
    world.lock.owners[("affine", "staging")] = "op-deploy"
    with pytest.raises(LockBusy):
        restarter(world).run(AFFINE, "affine", "op-1", world.clock() + 300)
    world.lock.owners.clear()
    world.ownership.valid = False
    with pytest.raises(OwnershipLost):
        restarter(world).run(AFFINE, "affine", "op-1", world.clock() + 300)
    assert ("patch_deployment", "affine-staging") not in world.cluster.calls


def test_restart_reconcile_uses_annotation(world: World) -> None:
    a = world.actions.record_planned("op-1", "restart", "affine-staging/deployment/affine", {})
    assert restarter(world).reconcile(AFFINE, "affine", a, world.clock() + 300) is None
    world.cluster.deployments[("affine-staging", "affine")].annotations["ops.mmk/action-id"] = a.action_id
    res = restarter(world).reconcile(AFFINE, "affine", a, world.clock() + 300)
    assert res is not None and res.rollout == "complete"
    assert ("patch_deployment", "affine-staging") not in world.cluster.calls


# Verifikation


def smoke(world: World) -> SmokeTest:
    return SmokeTest(world.k8s(), world.policy(), world.services, "acr-pull", sleep=world.clock.sleep, clock=world.clock)


def test_smoke_job_contract(world: World) -> None:
    ev = smoke(world).run(AFFINE, "affine", SMOKE_IMAGE, {}, "op-1", 120)
    job = world.cluster.jobs[("affine-staging", ev.job_name)]["body"]
    env = {e.name: e.value for e in job.spec.template.spec.containers[0].env}
    assert env["TARGET_URL"] == "https://affine.1-2-3-4.sslip.io"
    assert ev.action_id.lower() in ev.job_name and job.spec.template.spec.image_pull_secrets[0].name == "acr-pull"
    assert ev.success and ev.exit_code == 0 and ev.kubernetes.captured_at >= ev.started_at
    assert ev.check_ref == "sha256:" + "b" * 64


def test_smoke_exit_zero_but_success_false_is_failure(world: World) -> None:
    world.cluster.smoke = lambda c: (0, '{"success": false}')
    assert not smoke(world).run(AFFINE, "affine", SMOKE_IMAGE, {}, "op-1", 120).success


def test_smoke_hanging_job_times_out(world: World) -> None:
    world.cluster.smoke = lambda c: None
    ev = smoke(world).run(AFFINE, "affine", SMOKE_IMAGE, {}, "op-1", 60)
    assert ev.timed_out and not ev.success and ev.finished_at is None
    assert world.cluster.jobs[("affine-staging", ev.job_name)]["deleted"]


def test_parse_result_and_job_name() -> None:
    assert parse_result('log line\n{"success": true}\n') == {"success": True}
    assert parse_result("kein json") is None
    assert job_name("ACT_0001") == "smoke-act-0001"


def test_kubeconfig_path_is_read_at_call_time(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import kubernetes as k

    seen = {}
    monkeypatch.delenv("KUBERNETES_SERVICE_HOST", raising=False)
    monkeypatch.setenv("OPS_KUBECONFIG", "/tmp/sa.kubeconfig")
    monkeypatch.setenv("OPS_KUBE_CONTEXT", "kind-ops-agent-sa")
    monkeypatch.setattr(k.config, "load_kube_config", lambda **kw: seen.update(kw))
    k.KubernetesAdapter.from_environment(world.policy())
    assert seen == {"config_file": "/tmp/sa.kubeconfig", "context": "kind-ops-agent-sa"}
    monkeypatch.delenv("OPS_KUBE_CONTEXT")
    with pytest.raises(ValidationFailed):
        k.KubernetesAdapter.from_environment(world.policy())
