"""Platzhalter, solange D's Agentenausführung nicht konfiguriert ist.

``OPS_AGENT_EXECUTOR=worker.placeholder:execute`` lässt den Worker Aufträge übernehmen
und sichtbar in ``needs_attention`` beenden, ohne Modell und ohne Änderung.
"""

from contracts import AgentInvocation, AgentResult, OperationsServices, Usage


def execute(invocation: AgentInvocation, services: OperationsServices) -> AgentResult:
    return AgentResult(
        job_id=invocation.job_id,
        proposal="needs_attention",
        reason="keine Agentenausführung konfiguriert (Platzhalter)",
        usage=Usage(provider="none", model="none"),
    )
