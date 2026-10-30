"""Auftragszustände und zulässige Übergänge (Vertrag v0.1, Eigentümer: Strang C)."""

from enum import StrEnum


class JobState(StrEnum):
    RECEIVED = "received"
    QUEUED = "queued"
    INVESTIGATING = "investigating"
    ACTING = "acting"
    VERIFYING = "verifying"
    RESOLVED = "resolved"
    FAILED = "failed"
    NEEDS_ATTENTION = "needs_attention"


TERMINAL_STATES = frozenset({JobState.RESOLVED, JobState.FAILED, JobState.NEEDS_ATTENTION})

# Zustände, in denen ein Worker den Auftrag übernehmen oder wiederaufnehmen darf.
CLAIMABLE_STATES = frozenset(
    {JobState.QUEUED, JobState.INVESTIGATING, JobState.ACTING, JobState.VERIFYING}
)

_ABORT = {JobState.FAILED, JobState.NEEDS_ATTENTION}

TRANSITIONS: dict[JobState, frozenset[JobState]] = {
    JobState.RECEIVED: frozenset({JobState.QUEUED, *_ABORT}),
    JobState.QUEUED: frozenset({JobState.INVESTIGATING, *_ABORT}),
    # Untersuchung ohne Maßnahme darf direkt verifizieren.
    JobState.INVESTIGATING: frozenset({JobState.ACTING, JobState.VERIFYING, *_ABORT}),
    JobState.ACTING: frozenset({JobState.VERIFYING, *_ABORT}),
    # Ein erneuter begrenzter Versuch führt zurück in die Untersuchung.
    JobState.VERIFYING: frozenset({JobState.RESOLVED, JobState.INVESTIGATING, *_ABORT}),
    JobState.RESOLVED: frozenset(),
    JobState.FAILED: frozenset(),
    JobState.NEEDS_ATTENTION: frozenset(),
}


def is_allowed(from_state: JobState | str, to_state: JobState | str) -> bool:
    return JobState(to_state) in TRANSITIONS[JobState(from_state)]
