"""Deterministischer Ergebnisvorschlag aus Nachweisen; Modelltext entscheidet nie über ``resolved``."""

from __future__ import annotations

from dataclasses import dataclass

from agent.context import OperationalContext, desired_digest
from agent.recorder import RunRecorder
from contracts import VerificationEvidence


@dataclass
class Stop:
    """Grund, aus dem der Lauf vorzeitig endete."""

    kind: str
    reason: str
    limit: str | None = None


def valid_verification(rec: RunRecorder, ctx: OperationalContext) -> VerificationEvidence | None:
    """Positiver Test nach der letzten Maßnahme mit danach erfasstem, passendem Kubernetes-Zustand."""
    baseline = rec.last_mutation_at or rec.started_at
    want = desired_digest(ctx.desired.value)
    for v in reversed(rec.verification):
        k = v.kubernetes
        if not (v.success and v.started_at >= baseline and k is not None):
            continue
        if k.captured_at < v.started_at or not k.rollout_complete or k.ready_replicas < 1:
            continue
        if want and want not in k.image_digests:
            continue
        return v
    return None


def verification_failed(rec: RunRecorder) -> bool:
    baseline = rec.last_mutation_at or rec.started_at
    return any(not v.success and v.started_at >= baseline for v in rec.verification)
