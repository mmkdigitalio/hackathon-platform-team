"""Aufruf-/Ergebnis-, Aktions- und Sperrvertrag v0.1 (Entwurf, Besitz: Strang C)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol

from langgraph.checkpoint.base import BaseCheckpointSaver
from pydantic import BaseModel, ConfigDict, Field

CONTRACT_VERSION = "0.1"

ResultProposal = Literal["resolved", "needs_attention", "failed"]
ActionKind = Literal["restart", "workflow_dispatch", "smoke_test", "issue_comment"]
ActionStatus = Literal["planned", "started", "succeeded", "failed", "unclear"]
JobState = Literal["investigating", "acting", "verifying"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=False)


class RunLimits(_Model):
    """Grenzen je Lauf; Startwerte laut Design D, Entscheidung 5."""

    max_runtime_seconds: int = 600
    max_tool_calls: int = 30
    max_repair_attempts: int = 2
    max_cost_usd: float = 1.0


class IssueRef(_Model):
    repository: str
    number: int


class ContextRefs(_Model):
    """Versionsbezogene Kontextreferenzen eines Auftrags."""

    git_ref: str = Field(description="Commit-SHA, zu dem Katalog und Sollzustand gelesen werden")
    alert_payload: dict[str, Any] | None = Field(
        default=None, description="Common Alert Schema, unverändert; gilt als Daten"
    )
    issue: IssueRef | None = None
    issue_text: str | None = Field(default=None, description="Tickettext; gilt als Daten")
    event_type: str | None = None


class Ownership(_Model):
    """Gültiger Auftragsbesitz des Workers (Lease), nicht die Operations-Sperre."""

    worker_id: str
    lease_version: int


class ActionRecord(_Model):
    action_id: str
    operation_id: str
    kind: ActionKind
    target: str
    status: ActionStatus
    correlation: dict[str, str] = Field(default_factory=dict)
    external_refs: dict[str, str] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime | None = None


class ResumeInfo(_Model):
    thread_id: str
    open_actions: list[ActionRecord] = Field(default_factory=list)


class AgentInvocation(_Model):
    contract_version: Literal["0.1"] = CONTRACT_VERSION
    job_id: str
    incident_id: str
    execution_id: str
    app_id: str
    environment: str
    operation_id: str
    ownership: Ownership
    limits: RunLimits = Field(default_factory=RunLimits)
    context: ContextRefs
    resume: ResumeInfo


class Finding(_Model):
    statement: str
    certainty: Literal["confirmed", "hypothesis"]
    provenance: str = Field(description="Quelle, z. B. tool:get_workload_state oder context:alert")
    evidence_refs: list[str] = Field(default_factory=list)
    observed_at: datetime


class ToolCallRecord(_Model):
    name: str
    started_at: datetime
    duration_ms: int
    status: Literal["ok", "error"]
    error_kind: str | None = None
    target: str | None = None
    action_id: str | None = None


class Usage(_Model):
    provider: str
    model: str
    model_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


class ReleaseRef(_Model):
    git_sha: str | None = None
    image_digest: str | None = None
    chart_version: str | None = None


class KubernetesState(_Model):
    namespace: str
    deployment: str
    desired_replicas: int
    ready_replicas: int
    image_digests: list[str]
    restart_counts: dict[str, int]
    rollout_complete: bool
    captured_at: datetime


class VerificationEvidence(_Model):
    app_id: str
    environment: str
    release: ReleaseRef
    check_ref: str = Field(description="Digest des Smoke-Test-Images")
    started_at: datetime
    finished_at: datetime | None
    success: bool
    timed_out: bool = False
    exit_code: int | None = None
    job_name: str
    action_id: str
    result: dict[str, Any] | None = None
    kubernetes: KubernetesState | None = None
    evidence_refs: list[str] = Field(default_factory=list)


class AgentResult(_Model):
    contract_version: Literal["0.1"] = CONTRACT_VERSION
    job_id: str
    proposal: ResultProposal
    reason: str
    limit_reached: str | None = None
    findings: list[Finding] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list, description="actionIds dieses Laufs")
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    usage: Usage
    verification: list[VerificationEvidence] = Field(default_factory=list)
    required_change: str | None = None
    references: dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True)
class LockAcquisition:
    acquired: bool
    holder_operation_id: str | None = None


class OwnershipLost(Exception):
    """Auftragsbesitz oder Operations-Sperre ist nicht mehr gültig."""


class OwnershipGuard(Protocol):
    def assert_valid(self) -> None:
        """Wirft ``OwnershipLost``, wenn das Lease des Workers nicht mehr gilt."""


class ActionLog(Protocol):
    def record_planned(
        self,
        operation_id: str,
        kind: ActionKind,
        target: str,
        correlation: dict[str, str],
    ) -> ActionRecord:
        """Speichert die Aktion vor dem externen Aufruf und vergibt die ``actionId``."""

    def update(
        self,
        action_id: str,
        status: ActionStatus,
        external_refs: dict[str, str] | None = None,
    ) -> ActionRecord: ...

    def get(self, action_id: str) -> ActionRecord | None: ...

    def list_for_operation(self, operation_id: str) -> list[ActionRecord]: ...


class OperationsLock(Protocol):
    def try_acquire(self, app_id: str, environment: str, operation_id: str) -> LockAcquisition:
        """Erwerb ohne Warten."""

    def holder(self, app_id: str, environment: str) -> str | None: ...

    def holds(self, app_id: str, environment: str, operation_id: str) -> bool: ...

    def release(self, app_id: str, environment: str, operation_id: str) -> None: ...


class StateTransitions(Protocol):
    def request(self, job_id: str, state: JobState, reason: str) -> bool:
        """C prüft den Übergang; ``False`` bedeutet abgelehnt."""


class CheckpointProvider(Protocol):
    def checkpointer(self) -> BaseCheckpointSaver | None: ...


@dataclass(frozen=True)
class OperationsServices:
    """Dienste, die C's Worker der Agentenausführung übergibt."""

    actions: ActionLog
    lock: OperationsLock
    state: StateTransitions
    ownership: OwnershipGuard
    checkpoints: CheckpointProvider


class AgentExecution(Protocol):
    def __call__(self, invocation: AgentInvocation, services: OperationsServices) -> AgentResult: ...
