"""Kontrollierter Neustart über die Annotation ``ops.mmk/action-id`` am Pod-Template."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from contracts import ActionRecord, OperationsServices, OwnershipLost
from tools.common import LockBusy, ToolError, UnclearOutcome
from tools.kubernetes import KubernetesAdapter
from tools.targets import Target


@dataclass
class RestartResult:
    action: ActionRecord
    rollout: Literal["complete", "in_progress", "failed"]
    state: dict[str, Any]


class RestartAction:
    def __init__(
        self,
        k8s: KubernetesAdapter,
        services: OperationsServices,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        poll_seconds: float = 5.0,
        rollout_timeout_seconds: float = 180.0,
    ) -> None:
        self.k8s = k8s
        self.rollout_timeout = rollout_timeout_seconds
        self.s = services
        self.sleep = sleep
        self.clock = clock
        self.poll = poll_seconds

    def run(self, t: Target, deployment: str, operation_id: str, deadline: float) -> RestartResult:
        self.s.ownership.assert_valid()
        acq = self.s.lock.try_acquire(t.app_id, t.environment, operation_id)
        if not acq.acquired:
            raise LockBusy(acq.holder_operation_id or "unbekannt")
        try:
            action = self.s.actions.record_planned(
                operation_id, "restart", f"{t.namespace}/deployment/{deployment}", {"annotation": "ops.mmk/action-id"}
            )
            # Besitz unmittelbar vor der Änderung erneut prüfen.
            self.s.ownership.assert_valid()
            if not self.s.lock.holds(t.app_id, t.environment, operation_id):
                self.s.actions.update(action.action_id, "failed", {"reason": "Sperrbesitz verloren vor Patch"})
                raise OwnershipLost(f"Sperre für {t} nicht mehr im Besitz von {operation_id}")
            try:
                generation = self.k8s.patch_restart(t, deployment, action.action_id)
            except ToolError as e:
                if e.kind not in ("timeout", "upstream"):
                    self.s.actions.update(action.action_id, "failed", {"error": str(e)})
                    raise
                # Antwort verloren: am Template ablesen, ob der Patch angekommen ist.
                action = self.s.actions.update(action.action_id, "unclear", {"error": str(e)})
                try:
                    applied = self.k8s.template_action_id(t, deployment) == action.action_id
                except ToolError as e2:
                    raise UnclearOutcome(f"Neustart {action.action_id} unklar: {e2}", action.action_id) from e
                if not applied:
                    self.s.actions.update(action.action_id, "failed", {"reconciled": "annotation fehlt, nicht ausgeführt"})
                    raise
                action = self.s.actions.update(action.action_id, "started", {"deployment": deployment, "reconciled": "annotation gesetzt"})
                return self._observe(t, deployment, action, deadline)
            action = self.s.actions.update(
                action.action_id, "started", {"deployment": deployment, "generation": str(generation)}
            )
            return self._observe(t, deployment, action, deadline)
        finally:
            if self.s.lock.holds(t.app_id, t.environment, operation_id):
                self.s.lock.release(t.app_id, t.environment, operation_id)

    def reconcile(self, t: Target, deployment: str, action: ActionRecord, deadline: float) -> RestartResult | None:
        """``None``: Annotation fehlt, der Neustart lief nicht. Sonst nur den Rollout beobachten."""
        if self.k8s.template_action_id(t, deployment) != action.action_id:
            self.s.actions.update(action.action_id, "failed", {"reconciled": "annotation fehlt, nicht ausgeführt"})
            return None
        return self._observe(t, deployment, action, deadline)

    def _observe(self, t: Target, deployment: str, action: ActionRecord, deadline: float) -> RestartResult:
        deadline = min(deadline, self.clock() + self.rollout_timeout)
        while True:
            state, extra, refs = self.k8s.workload_state(t, deployment)
            view = {"kubernetes": state.model_dump(mode="json"), **extra, "evidenceRefs": refs}
            if state.rollout_complete and state.ready_replicas >= state.desired_replicas:
                return RestartResult(self.s.actions.update(action.action_id, "succeeded", {"rollout": "complete"}), "complete", view)
            if any(c["type"] == "Progressing" and c["reason"] == "ProgressDeadlineExceeded" for c in extra["conditions"]):
                return RestartResult(self.s.actions.update(action.action_id, "failed", {"rollout": "ProgressDeadlineExceeded"}), "failed", view)
            if self.clock() + self.poll > deadline:
                return RestartResult(action, "in_progress", view)
            self.sleep(self.poll)
