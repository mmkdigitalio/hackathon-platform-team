"""Dispatch vorgegebener Workflows mit ``operationId``/``actionId`` und eindeutiger Laufzuordnung."""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Literal

from contracts import ActionRecord, OperationsServices
from tools.common import LockBusy, NotAllowed, ToolError, UnclearOutcome, utcnow
from tools.github import GitHubClient
from tools.targets import Target

RunState = Literal["unassigned", "queued", "in_progress", "success", "failure"]
# Nach dieser Zeit ohne auffindbaren Lauf gilt eine nur geplante Aktion als nicht ausgeführt.
PLANNED_GRACE = timedelta(minutes=3)


@dataclass
class WorkflowRun:
    action: ActionRecord
    state: RunState
    run: dict[str, Any] | None


def mentions(title: str, ident: str) -> bool:
    """``ident`` als ganzes Token im ``run-name``, nicht als Teil einer längeren ID."""
    return re.search(rf"(?<![\w-]){re.escape(ident)}(?![\w-])", title) is not None


def run_state(run: dict[str, Any] | None) -> RunState:
    if run is None:
        return "unassigned"
    if run["status"] != "completed":
        return "queued" if run["status"] in ("queued", "requested", "waiting", "pending") else "in_progress"
    return "success" if run["conclusion"] == "success" else "failure"


class WorkflowDispatcher:
    def __init__(
        self,
        github: GitHubClient,
        services: OperationsServices,
        allowed_workflows: frozenset[str],
        ref: str = "main",
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        poll_seconds: float = 10.0,
    ) -> None:
        self.gh = github
        self.s = services
        self.allowed = allowed_workflows
        self.ref = ref
        self.sleep = sleep
        self.clock = clock
        self.poll = poll_seconds

    def check_allowed(self, workflow: str) -> None:
        if workflow not in self.allowed:
            raise NotAllowed(f"Workflow '{workflow}' ist nicht freigegeben", allowed=sorted(self.allowed))

    def dispatch(self, t: Target, workflow: str, operation_id: str, deadline: float) -> WorkflowRun:
        self.check_allowed(workflow)
        self.s.ownership.assert_valid()
        # Sperre nur prüfen, nicht halten: der Deploy-Workflow übernimmt sie.
        holder = self.s.lock.holder(t.app_id, t.environment)
        if holder and holder != operation_id:
            raise LockBusy(holder)
        action = self.s.actions.record_planned(
            operation_id, "workflow_dispatch", f"{workflow}:{t}", {"workflow": workflow, "operationId": operation_id}
        )
        inputs = {
            "appId": t.app_id,
            "environment": t.environment,
            "operationId": operation_id,
            "actionId": action.action_id,
            "force": "false",
        }
        try:
            run_id = self.gh.dispatch(workflow, self.ref, inputs)
        except ToolError as e:
            if e.kind in ("timeout", "upstream"):
                action = self.s.actions.update(action.action_id, "unclear", {"workflow": workflow, "error": str(e)})
                return self._correlate_or_fail(t, workflow, action, deadline)
            self.s.actions.update(action.action_id, "failed", {"workflow": workflow, "error": str(e)})
            raise
        action = self.s.actions.update(action.action_id, "started", {"workflow": workflow})
        if run_id is not None:
            return self._adopt(action, workflow, self.gh.get_run(run_id))
        return self._correlate_or_fail(t, workflow, action, deadline)

    def wait(self, wr: WorkflowRun, deadline: float) -> WorkflowRun:
        """Wartet auf den echten Abschluss; ohne Abschluss bleibt der Status laufend."""
        run = wr.run
        if run is None:
            return wr
        while run_state(run) in ("queued", "in_progress"):
            if self.clock() + self.poll > deadline:
                return WorkflowRun(wr.action, run_state(run), run)
            self.sleep(self.poll)
            run = self.gh.get_run(run["id"])
        state = run_state(run)
        action = self.s.actions.update(
            wr.action.action_id,
            "succeeded" if state == "success" else "failed",
            {"runId": str(run["id"]), "conclusion": str(run["conclusion"])},
        )
        return WorkflowRun(action, state, run)

    def status(self, action: ActionRecord) -> WorkflowRun:
        run_id = action.external_refs.get("runId")
        if not run_id:
            return WorkflowRun(action, "unassigned", None)
        run = self.gh.get_run(int(run_id))
        return WorkflowRun(action, run_state(run), run)

    def reconcile(self, t: Target, action: ActionRecord, deadline: float) -> WorkflowRun | None:
        """Wiederaufnahme: bekannte Lauf-ID weiterverfolgen, sonst nur über ``actionId`` zuordnen.

        ``None`` heißt: sicher nicht ausgeführt, ein neuer Dispatch mit neuer ``actionId`` ist zulässig.
        """
        if action.external_refs.get("runId"):
            return self.status(action)
        workflow = action.correlation.get("workflow") or action.external_refs.get("workflow")
        self.check_allowed(workflow)
        runs, complete = self.gh.dispatch_runs(workflow, action.created_at - timedelta(minutes=1))
        mine = [r for r in runs if mentions(r["title"], action.action_id)]
        if len(mine) == 1:
            return self._adopt(action, workflow, mine[0])
        if len(mine) > 1:
            self._unclear(action, "mehrere Läufe tragen dieselbe actionId", [str(r["id"]) for r in mine])
        known = {a.action_id for a in self.s.actions.list_for_operation(action.operation_id)}
        orphans = [
            r for r in runs if mentions(r["title"], action.operation_id) and not any(mentions(r["title"], k) for k in known)
        ]
        if orphans:
            self._unclear(action, "nicht zugeordnete Läufe derselben operationId", [str(r["id"]) for r in orphans])
        if complete and action.status == "planned" and utcnow() - action.created_at > PLANNED_GRACE:
            self.s.actions.update(action.action_id, "failed", {"reconciled": "kein Lauf gefunden, nicht ausgeführt"})
            return None
        self._unclear(action, "kein zuordenbarer Lauf gefunden", [])

    def _correlate_or_fail(self, t: Target, workflow: str, action: ActionRecord, deadline: float) -> WorkflowRun:
        since = action.created_at - timedelta(minutes=1)
        while True:
            runs, _ = self.gh.dispatch_runs(workflow, since)
            mine = [r for r in runs if mentions(r["title"], action.action_id)]
            if len(mine) == 1:
                return self._adopt(action, workflow, mine[0])
            if len(mine) > 1:
                self._unclear(action, "mehrere Läufe tragen dieselbe actionId", [str(r["id"]) for r in mine])
            if self.clock() + self.poll > deadline:
                if action.status == "unclear":
                    self._unclear(action, "Dispatch-Antwort verloren, kein Lauf zuordenbar", [])
                return WorkflowRun(action, "unassigned", None)
            self.sleep(self.poll)

    def _adopt(self, action: ActionRecord, workflow: str, run: dict[str, Any]) -> WorkflowRun:
        action = self.s.actions.update(
            action.action_id, "started", {"workflow": workflow, "runId": str(run["id"]), "runUrl": run.get("url") or ""}
        )
        return WorkflowRun(action, run_state(run), run)

    def _unclear(self, action: ActionRecord, why: str, candidates: list[str]) -> None:
        self.s.actions.update(action.action_id, "unclear", {"reason": why, "candidates": ",".join(candidates)})
        raise UnclearOutcome(f"Workflow-Lauf für {action.action_id} unklar: {why}", action.action_id, candidates)
