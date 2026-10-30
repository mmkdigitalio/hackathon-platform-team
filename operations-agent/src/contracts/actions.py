"""Aktions- und Sperrvertrag für Deploy-Workflows (B) und die Persistenz (C).

Die Schnittstelle zur Agentenausführung (D) steht in ``contracts.v0_1``; Status und
Aktionsarten hier sind damit deckungsgleich.
"""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class ActionStatus(StrEnum):
    # Vor dem externen Aufruf gespeichert, Ergebnis noch unbekannt.
    PLANNED = "planned"
    # Vom externen System angenommen (z. B. Workflow-Lauf gestartet), Ausgang offen.
    STARTED = "started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    # Ausgang ausdrücklich unbekannt; blockiert potenziell doppelte Änderungen bis zur Klärung.
    UNCLEAR = "unclear"


OPEN_ACTION_STATUSES = frozenset({ActionStatus.PLANNED, ActionStatus.STARTED, ActionStatus.UNCLEAR})

# Aktion, mit der der Agent einen Deploy-Workflow startet. Für sie darf die eigene
# Operation keine Ziel-Sperre halten; die Sperre übernimmt der Workflow.
WORKFLOW_DISPATCH = "workflow_dispatch"

# Agentenaktionen, die das Ziel verändern (vgl. agent.runner.MUTATING).
MUTATING_AGENT_KINDS = frozenset({"restart", WORKFLOW_DISPATCH})


class ActionSnapshot(BaseModel):
    """Gespeicherter Stand einer Aktion."""

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
    correlation: dict[str, str] = {}
    external_refs: dict[str, str] = {}
    created_at: datetime
    updated_at: datetime


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
