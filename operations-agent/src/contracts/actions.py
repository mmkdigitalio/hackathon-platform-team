"""Aktions- und Sperrvertrag v0.1 (C liefert an B und D)."""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ActionStatus(StrEnum):
    # Vor dem externen Aufruf gespeichert, Ergebnis noch unbekannt.
    PENDING = "pending"
    # Vom externen System angenommen (z. B. Workflow-Lauf gestartet), Ausgang offen.
    STARTED = "started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    # Ausgang ausdrücklich unbekannt; blockiert potenziell doppelte Änderungen bis zur Klärung.
    UNCLEAR = "unclear"


OPEN_ACTION_STATUSES = frozenset({ActionStatus.PENDING, ActionStatus.STARTED, ActionStatus.UNCLEAR})

# Art einer Aktion, mit der der Agent einen Deploy-Workflow startet. Für sie darf die
# eigene Operation keine Ziel-Sperre halten; die Sperre übernimmt der Workflow.
WORKFLOW_DISPATCH = "workflow-dispatch"


class ExternalRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    system: str = Field(description="z. B. github-actions, kubernetes")
    ref: str = Field(description="z. B. Workflow-Lauf-URL oder Objektpfad")


class ActionSnapshot(BaseModel):
    """Gespeicherter Stand einer Aktion, z. B. für Wiederaufnahme und Abgleich."""

    model_config = ConfigDict(extra="forbid")

    action_id: str
    operation_id: str
    job_id: str | None
    app_id: str
    environment: str
    kind: str
    target: str
    mutating: bool
    status: ActionStatus
    correlation: str | None = None
    external_refs: list[ExternalRef] = []
    detail: dict = {}
    created_at: datetime
    updated_at: datetime


class ReconcileStatus(StrEnum):
    # Der externe Vorgang läuft noch; der Agent verfolgt ihn über die bestehende Referenz weiter.
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    # Nachweislich nie beim externen System angekommen; erneuter Start ist zulässig.
    NOT_STARTED = "not_started"
    # Nicht aufklärbar: Auftrag endet in needs_attention.
    AMBIGUOUS = "ambiguous"


class ReconcileResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ReconcileStatus
    external_refs: list[ExternalRef] = []
    reason: str


class LockHandle(BaseModel):
    """Gültiger Sperrbesitz je App/Umgebung. Unterschiedlich vom Auftrags-Lease."""

    model_config = ConfigDict(extra="forbid")

    app_id: str
    environment: str
    operation_id: str
    owner_ref: str
    lock_version: int
    expires_at: datetime


# Konfliktregel: ein Deploy-Workflow wartet höchstens so lange, der Agent gar nicht.
WORKFLOW_LOCK_WAIT_SECONDS = 15 * 60
