"""Platzhalter, solange D's Agentenausführung nicht eingebunden ist.

``OPS_AGENT_EXECUTOR=worker.placeholder:build_executor`` lässt den Worker Aufträge
übernehmen und sichtbar in ``needs_attention`` beenden, ohne Modell und ohne Änderung.
"""

from contracts.actions import ActionSnapshot, ReconcileResult, ReconcileStatus
from contracts.agent import AgentInvocation, AgentResult, OperationServices


class PlaceholderExecutor:
    def run(self, invocation: AgentInvocation, services: OperationServices) -> AgentResult:
        return AgentResult(
            proposed_state="needs_attention",
            reason="keine Agentenausführung konfiguriert (Platzhalter)",
        )


class PlaceholderReconciler:
    def reconcile(self, action: ActionSnapshot) -> ReconcileResult:
        return ReconcileResult(status=ReconcileStatus.AMBIGUOUS, reason="kein Abgleich konfiguriert")


def build_executor() -> PlaceholderExecutor:
    return PlaceholderExecutor()


def build_reconciler() -> PlaceholderReconciler:
    return PlaceholderReconciler()
