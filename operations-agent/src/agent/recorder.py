"""Sammelt Befunde, Werkzeugaufrufe, Aktionen und Nachweise eines Laufs außerhalb des Modellzustands."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from contracts import Finding, ToolCallRecord, VerificationEvidence
from tools.common import ToolOutcome, utcnow


class RunRecorder:
    def __init__(self) -> None:
        self.started_at: datetime = utcnow()
        self.findings: list[Finding] = []
        self.tool_calls: list[ToolCallRecord] = []
        self.action_ids: list[str] = []
        self.verification: list[VerificationEvidence] = []
        self.references: dict[str, Any] = {}
        self.last_mutation_at: datetime | None = None
        self.seen_refs: set[str] = set()
        self.log_lines: list[str] = []

    def tool_call(self, outcome: ToolOutcome, started: datetime) -> None:
        self.tool_calls.append(
            ToolCallRecord(
                name=outcome.tool,
                started_at=started,
                duration_ms=int((utcnow() - started).total_seconds() * 1000),
                status=outcome.status,
                error_kind=outcome.error_kind,
                target=outcome.target,
                action_id=outcome.action_id,
            )
        )
        self.seen_refs.update(outcome.evidence_refs)
        if outcome.action_id and outcome.action_id not in self.action_ids:
            self.action_ids.append(outcome.action_id)

    def finding(
        self,
        statement: str,
        provenance: str,
        refs: list[str],
        certainty: Literal["confirmed", "hypothesis"] = "confirmed",
    ) -> Finding:
        f = Finding(statement=statement, certainty=certainty, provenance=provenance, evidence_refs=refs, observed_at=utcnow())
        self.findings.append(f)
        self.seen_refs.update(refs)
        return f

    def model_finding(self, statement: str, certainty: Literal["confirmed", "hypothesis"], refs: list[str]) -> Finding:
        """Bestätigt nur mit Evidenz, die ein Werkzeug in diesem Lauf geliefert hat."""
        known = [r for r in refs if r in self.seen_refs]
        if certainty == "confirmed" and not known:
            certainty = "hypothesis"
        f = Finding(statement=statement, certainty=certainty, provenance="model", evidence_refs=known, observed_at=utcnow())
        self.findings.append(f)
        return f

    def mutation(self, action_id: str) -> None:
        if action_id not in self.action_ids:
            self.action_ids.append(action_id)
        self.last_mutation_at = utcnow()

    def verified(self, evidence: VerificationEvidence) -> None:
        self.verification.append(evidence)
        if evidence.action_id not in self.action_ids:
            self.action_ids.append(evidence.action_id)
        self.seen_refs.update(evidence.evidence_refs)

    def suspicious_log_lines(self, lines: list[str]) -> None:
        keys = ("error", "fatal", "invalid", "exception", "panic", "failed")
        for line in lines:
            if any(k in line.lower() for k in keys) and line not in self.log_lines:
                self.log_lines.append(line[:500])
        del self.log_lines[:-20]
