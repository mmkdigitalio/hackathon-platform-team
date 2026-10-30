"""Gemeinsames Ergebnis- und Fehlerformat aller Betriebswerkzeuge."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

ErrorKind = Literal[
    "validation",
    "permission",
    "timeout",
    "not_found",
    "conflict",
    "upstream",
    "not_allowed",
]


_SECRET_PATTERNS = [
    (re.compile(r"(://[^:/@\s]+:)[^@\s]+@"), r"\1***@"),
    (re.compile(r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key)(\s*[=:]\s*)[^\s,;'\"]+"), r"\1\2***"),
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+"), r"\1***"),
]
_KNOWN_SECRETS: set[str] = set()


def register_secret(value: str | None) -> None:
    """Bekannte Secret-Werte, die in keinem Ergebnis und keinem Modellkontext erscheinen dürfen."""
    if value and len(value) >= 8:
        _KNOWN_SECRETS.add(value)


def redact(text: str) -> str:
    for secret in _KNOWN_SECRETS:
        text = text.replace(secret, "***")
    for pattern, repl in _SECRET_PATTERNS:
        text = pattern.sub(repl, text)
    return text


def utcnow() -> datetime:
    return datetime.now(UTC)


class ToolOutcome(BaseModel):
    """Strukturiertes Werkzeugergebnis mit Ziel, Status, Zeitbezug und Evidenz."""

    tool: str
    status: Literal["ok", "error"]
    target: str | None = None
    observed_at: datetime = Field(default_factory=utcnow)
    error_kind: ErrorKind | None = None
    message: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)
    operation_id: str | None = None
    action_id: str | None = None

    @classmethod
    def ok(cls, tool: str, target: str | None, data: dict[str, Any], refs: list[str] | None = None, **kw: Any) -> ToolOutcome:
        return cls(tool=tool, status="ok", target=target, data=data, evidence_refs=refs or [], **kw)

    @classmethod
    def error(cls, tool: str, target: str | None, err: ToolError, **kw: Any) -> ToolOutcome:
        return cls(
            tool=tool,
            status="error",
            target=target,
            error_kind=err.kind,
            message=str(err),
            data=err.details,
            **kw,
        )


class ToolError(Exception):
    """Fachlicher Werkzeugfehler; wird dem Modell strukturiert zurückgegeben."""

    kind: ErrorKind = "upstream"

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.details = details


class ValidationFailed(ToolError):
    kind = "validation"


class PermissionDenied(ToolError):
    kind = "permission"


class TimedOut(ToolError):
    kind = "timeout"


class NotFound(ToolError):
    kind = "not_found"


class Conflict(ToolError):
    kind = "conflict"


class UpstreamError(ToolError):
    kind = "upstream"


class NotAllowed(ToolError):
    kind = "not_allowed"


class LockBusy(Exception):
    """Operations-Sperre gehört einer anderen Operation; der Agent wartet nicht."""

    def __init__(self, holder_operation_id: str) -> None:
        super().__init__(f"gesperrt durch Operation {holder_operation_id}")
        self.holder_operation_id = holder_operation_id


class UnclearOutcome(Exception):
    """Externer Ausgang lässt sich nicht eindeutig klären; keine Wiederholung."""

    def __init__(self, message: str, action_id: str, candidates: list[str] | None = None) -> None:
        super().__init__(message)
        self.action_id = action_id
        self.candidates = candidates or []


def k8s_ref(namespace: str, kind: str, name: str, at: datetime) -> str:
    return f"k8s://{namespace}/{kind}/{name}@{at.isoformat()}"


def github_ref(repository: str, kind: str, ident: str) -> str:
    return f"github://{repository}/{kind}/{ident}"
