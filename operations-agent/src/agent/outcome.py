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
    after = [v for v in rec.verification if v.started_at >= baseline]
    if not after:
        return None
    v = max(after, key=lambda x: x.started_at)
    k = v.kubernetes
    if not v.success or k is None or k.captured_at < v.started_at or not k.rollout_complete or k.ready_replicas < 1:
        return None
    if want and want not in k.image_digests:
        return None
    return v


def verification_failed(rec: RunRecorder) -> bool:
    baseline = rec.last_mutation_at or rec.started_at
    return any(not v.success and v.started_at >= baseline for v in rec.verification)
