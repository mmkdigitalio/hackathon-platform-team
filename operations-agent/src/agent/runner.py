"""Aufrufbare Agentenausführung für C's Worker: ``execute(invocation, services) -> AgentResult``."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from deepagents import create_deep_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langgraph.errors import GraphRecursionError

from agent.config import AgentSettings, ModelSettings, agent_settings, model_settings
from agent.context import ContextLoader, OperationalContext, assess_drift, desired_digest
from agent.limits import BudgetMiddleware, LimitReached, RunBudget, UsageCallback
from agent.model import build_chat_model
from agent.outcome import Stop, valid_verification, verification_failed
from agent.prompts import context_files, system_prompt, task_message
from agent.recorder import RunRecorder
from agent.toolset import VERIFY_RESERVE_SECONDS, RunScope, TransitionRejected, build_tools, tool_names
from contracts import AgentInvocation, AgentResult, OperationsServices, OwnershipLost, ResultProposal
from tools.common import LockBusy, ToolError, UnclearOutcome, github_ref, k8s_ref, register_secret
from tools.dispatch import WorkflowDispatcher
from tools.git_repo import GitWorkspace
from tools.github import GitHubClient
from tools.http_check import HttpProbe
from tools.kubernetes import KubernetesAdapter
from tools.restart import RestartAction
from tools.targets import TargetPolicy
from tools.verification import SmokeTest

log = logging.getLogger("operations_agent.agent")

MUTATING = ("restart", "workflow_dispatch")


class OpenRun(Exception):
    """Eigener Workflow-Lauf ist noch offen; der Lauf endet ohne Erfolgsvorschlag."""


@dataclass
class Dependencies:
    """Externe Clients; Tests ersetzen sie, im Betrieb entstehen sie aus der Konfiguration."""

    github: GitHubClient | None = None
    kubernetes: Callable[[TargetPolicy], KubernetesAdapter] | None = None
    http: HttpProbe | None = None
    chat_model: BaseChatModel | None = None
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    poll_seconds: dict[str, float] = field(default_factory=dict)
    git_remote_url: Callable[[str], str] | None = None


def execute(
    invocation: AgentInvocation,
    services: OperationsServices,
    *,
    settings: AgentSettings | None = None,
    model: ModelSettings | None = None,
    deps: Dependencies | None = None,
) -> AgentResult:
    settings = settings or agent_settings()
    model = model or model_settings()
    deps = deps or Dependencies()
    budget = RunBudget(invocation.limits, model, deps.clock)
    rec = RunRecorder()
    register_secret(settings.github_token.get_secret_value())
    register_secret(model.api_key.get_secret_value())
    if settings.github_app_private_key:
        register_secret(settings.github_app_private_key.get_secret_value())
    gh = deps.github or GitHubClient(settings.github_auth(), settings.github_repository)
    loader_k8s: KubernetesAdapter | None = None
    stop: Stop | None = None
    ctx: OperationalContext | None = None
    scope: RunScope | None = None

    try:
        policy = TargetPolicy(ContextLoader(gh, None).catalog_app_ids(invocation.context.git_ref), settings.base_domain)
        target = policy.target(invocation.app_id, invocation.environment)
        loader_k8s = (deps.kubernetes or KubernetesAdapter.from_environment)(policy)
        previous = services.actions.list_for_operation(invocation.operation_id)
        ctx = ContextLoader(gh, loader_k8s).load(invocation, target, previous)
        poll = deps.poll_seconds
        scope = RunScope(
            inv=invocation,
            services=services,
            settings=settings,
            budget=budget,
            recorder=rec,
            ctx=ctx,
            policy=policy,
            k8s=loader_k8s,
            gh=gh,
            probe=deps.http or HttpProbe(policy, scheme=settings.http_scheme),
            restart=RestartAction(loader_k8s, services, deps.sleep, deps.clock, poll.get("restart", 5.0)),
            dispatcher=WorkflowDispatcher(
                gh, services, settings.dispatch_workflows, settings.dispatch_ref, deps.sleep, deps.clock, poll.get("workflow", 10.0)
            ),
            smoke=SmokeTest(
                loader_k8s,
                policy,
                services,
                settings.image_pull_secret,
                settings.http_scheme,
                deps.sleep,
                deps.clock,
                poll.get("smoke", 5.0),
                acr_login_server=settings.acr_login_server,
            ),
            git=GitWorkspace(
                gh,
                services,
                invocation.operation_id,
                invocation.job_id,
                settings.git_workdir,
                settings.git_repositories,
                settings.git_branch_prefix,
                settings.git_denied_paths,
                **({"remote_url": deps.git_remote_url} if deps.git_remote_url else {}),
            ),
        )
        _context_findings(scope)
        budget.repair_attempts = _previous_repairs(previous)
        services.ownership.assert_valid()
        if not _resume(scope):
            _run_model(scope, deps.chat_model or build_chat_model(model))
            _ensure_verification(scope)
    except LimitReached as e:
        stop = Stop("limit", str(e), e.limit)
    except OpenRun as e:
        stop = Stop("open_run", str(e), "runtime")
    except LockBusy as e:
        stop = Stop("lock", str(e))
    except UnclearOutcome as e:
        stop = Stop("unclear", f"{e} (Kandidaten: {', '.join(e.candidates) or 'keine'})")
    except OwnershipLost as e:
        stop = Stop("ownership", f"Besitz verloren: {e}")
    except TransitionRejected as e:
        stop = Stop("transition", str(e))
    except ToolError as e:
        stop = Stop("context", f"Kontext nicht ladbar: {e.kind}: {e}")
    except Exception as e:  # noqa: BLE001 - unerwartete Fehler sichtbar als failed melden
        log.exception("Agentenausführung abgebrochen")
        stop = Stop("error", f"Interner Fehler: {type(e).__name__}")

    result = _result(invocation, scope, ctx, rec, budget, stop)
    if scope is not None and invocation.context.issue and settings.post_issue_comment:
        _comment_result(scope, result)
    return result


def _previous_repairs(previous: list) -> int:
    return sum(
        1
        for a in previous
        if a.kind in MUTATING and a.status != "planned" and "reconciled" not in a.external_refs
    )


def _context_findings(s: RunScope) -> None:
    ctx = s.ctx
    if ctx.alert:
        s.recorder.finding(
            f"Alarm {ctx.alert.get('rule')} ({ctx.alert.get('condition')}) seit {ctx.alert.get('firedAt')}",
            "context:alert",
            [f"alert://{ctx.alert.get('alertId')}"],
        )
    if ctx.actual:
        drift, why = assess_drift(ctx)
        s.recorder.finding(
            f"Soll-/Istvergleich: {drift}: {why}",
            "code:assess_drift",
            [ctx.desired.source, k8s_ref(ctx.actual.namespace, "deployment", ctx.actual.deployment, ctx.actual.captured_at)],
        )
    if ctx.actual_release:
        s.recorder.references["actualRelease"] = ctx.actual_release.model_dump()
    s.recorder.references["desiredState"] = {
        "path": ctx.desired_path,
        "gitRef": ctx.git_ref,
        "source": ctx.desired.source,
        # Erwarteter Zielstand für C's Abschlussprüfung (Strang C).
        "imageDigest": desired_digest(ctx.desired.value),
    }
    if ctx.runbook:
        s.recorder.references["runbook"] = ctx.runbook.source


def _resume(s: RunScope) -> bool:
    """Offene Aktionen abgleichen. ``True``: Auftrag ist ohne weiteren Modelllauf entschieden."""
    reconciled = False
    for a in s.ctx.previous_actions:
        if a.kind not in MUTATING or a.status not in ("planned", "started", "unclear"):
            continue
        if a.kind == "restart":
            res = s.restart.reconcile(s.target, s.ctx.deployment, a, s.budget.deadline - VERIFY_RESERVE_SECONDS)
            if res is not None:
                s.recorder.mutation(a.action_id)
                reconciled = True
        else:
            wr = s.dispatcher.reconcile(s.target, a, s.budget.deadline)
            if wr is None:
                continue
            s.recorder.mutation(a.action_id)
            wr = s.dispatcher.wait(wr, s.budget.deadline - s.settings.smoke_timeout_seconds - VERIFY_RESERVE_SECONDS)
            if wr.state not in ("success", "failure"):
                s.recorder.references["openRun"] = wr.run
                raise OpenRun(f"Workflow-Lauf {wr.run['id'] if wr.run else '?'} noch nicht abgeschlossen")
            s.recorder.mutation(a.action_id)
            reconciled = True
        s.recorder.finding(f"Aktion {a.action_id} ({a.kind}) nach Wiederaufnahme abgeglichen", "code:reconcile", [])
    s.ctx.previous_actions = s.services.actions.list_for_operation(s.inv.operation_id)
    # Erst nach dem Abgleich ist bekannt, welche geplanten Maßnahmen tatsächlich liefen.
    s.budget.repair_attempts = max(s.budget.repair_attempts, _previous_repairs(s.ctx.previous_actions))
    if reconciled:
        s.runner_verify()
        return valid_verification(s.recorder, s.ctx) is not None
    return False


def _run_model(s: RunScope, chat_model: BaseChatModel) -> None:
    tools = build_tools(s)
    agent = create_deep_agent(
        model=chat_model,
        tools=tools,
        system_prompt=system_prompt(s.settings.rules_file),
        middleware=[BudgetMiddleware(s.budget, tool_names(tools))],
        checkpointer=s.services.checkpoints.checkpointer(),
        name="mmk-ops-agent",
    )
    config = {
        "configurable": {"thread_id": s.inv.resume.thread_id},
        "recursion_limit": 4 * s.inv.limits.max_tool_calls + 20,
        "callbacks": [UsageCallback(s.budget)],
    }
    try:
        agent.invoke({"messages": [HumanMessage(task_message(s.inv, s.ctx))], "files": context_files(s.inv, s.ctx)}, config=config)
    except GraphRecursionError as e:
        raise LimitReached("tool_calls", "Schrittgrenze des Graphen erreicht") from e


def _ensure_verification(s: RunScope) -> None:
    """Ohne Nachweis nach der letzten Maßnahme verifiziert der Runner selbst."""
    for a in s.services.actions.list_for_operation(s.inv.operation_id):
        if a.action_id in s.recorder.action_ids and a.kind == "workflow_dispatch" and a.status in ("planned", "started", "unclear"):
            s.recorder.references["openRun"] = a.external_refs
            raise OpenRun(f"Workflow-Aktion {a.action_id} noch nicht abgeschlossen")
    baseline = s.recorder.last_mutation_at or s.recorder.started_at
    if any(v.started_at >= baseline for v in s.recorder.verification):
        return
    s.runner_verify()


def _enrich_unrepairable(s: RunScope) -> str | None:
    """Ist == Soll und Verifikation negativ: Verweis auf den letzten Sollzustands-Commit und Logzeilen."""
    try:
        s.refresh_actual()
    except ToolError:
        return None
    drift, _ = assess_drift(s.ctx)
    if drift != "in_sync":
        return None
    try:
        if "desiredStateCommit" not in s.recorder.references:
            c = s.gh.last_commit(s.ctx.desired_path, s.ctx.git_ref)
            s.recorder.references["desiredStateCommit"] = {k: c[k] for k in ("sha", "author", "date", "url", "path")}
            s.recorder.references["desiredStateCommit"]["message"] = c["message"].splitlines()[0] if c["message"] else ""
        if not s.recorder.log_lines:
            logs, _ = s.k8s.logs(s.target, s.ctx.deployment, 100, 3600)
            for entry in logs:
                s.recorder.suspicious_log_lines(entry["lines"])
    except ToolError as e:
        s.recorder.references["enrichmentError"] = f"{e.kind}: {e}"
        return None
    c = s.recorder.references["desiredStateCommit"]
    s.recorder.references["logLines"] = s.recorder.log_lines
    s.recorder.finding(
        f"Istzustand entspricht dem Sollzustand, Verifikation negativ; letzter Commit {c['sha'][:12]} von {c['author']} am {c['date']}",
        "code:enrich_unrepairable",
        [github_ref(s.gh.repository, "commit", c["sha"])],
    )
    return (
        f"Sollzustand {s.ctx.desired_path} prüfen: letzter Commit {c['sha']} von {c['author']} am {c['date']}. "
        "Ein erneutes Deployment reproduziert den Fehler; nötig ist eine Korrektur des Sollzustands per Pull Request."
    )


def _result(
    inv: AgentInvocation,
    s: RunScope | None,
    ctx: OperationalContext | None,
    rec: RunRecorder,
    budget: RunBudget,
    stop: Stop | None,
) -> AgentResult:
    proposal: ResultProposal
    required_change = None
    valid = valid_verification(rec, ctx) if ctx else None
    if stop is not None and stop.kind == "error":
        proposal, reason = "failed", stop.reason
    elif valid is not None and (stop is None or stop.kind == "limit"):
        proposal, reason = "resolved", f"Positiver Smoke-Test {valid.job_name} nach der letzten Maßnahme, Kubernetes-Zustand bereit"
    else:
        proposal = "needs_attention"
        if stop is not None:
            reason = stop.reason
        elif verification_failed(rec):
            reason = "Verifikation nach erlaubten Maßnahmen negativ"
        else:
            reason = "Keine bestätigte Lösung"
        if s is not None and (stop is None or stop.kind == "limit") and verification_failed(rec):
            required_change = _enrich_unrepairable(s)
    return AgentResult(
        job_id=inv.job_id,
        proposal=proposal,
        reason=reason,
        limit_reached=stop.limit if stop else None,
        findings=rec.findings,
        actions=rec.action_ids,
        tool_calls=rec.tool_calls,
        usage=budget.usage(),
        verification=rec.verification,
        required_change=required_change,
        references=rec.references,
    )


def _comment_result(s: RunScope, result: AgentResult) -> None:
    confirmed = [f.statement for f in result.findings if f.certainty == "confirmed"][:8]
    lines = [f"**Ergebnis:** `{result.proposal}` – {result.reason}"]
    if confirmed:
        lines += ["", "**Befunde:**", *[f"- {x}" for x in confirmed]]
    if result.required_change:
        lines += ["", f"**Nötige Änderung:** {result.required_change}"]
    try:
        issue = s.inv.context.issue
        action = s.services.actions.record_planned(s.inv.operation_id, "issue_comment", f"{issue.repository}#{issue.number}", {})
        c = s.gh.comment_issue(issue, s.inv.job_id, "\n".join(lines))
        s.services.actions.update(action.action_id, "succeeded", {"commentId": str(c["id"])})
        result.actions.append(action.action_id)
        result.references["issueComment"] = c
    except ToolError as e:
        result.references["issueCommentError"] = f"{e.kind}: {e}"
