"""Auftragsbesitz (Lease) und Zustandsübergänge.

Jeder Schreibzugriff eines Workers prüft Besitzer, Besitzversion und Ablaufzeit gegen
die Datenbankzeit. Abgelehnte Übergänge werden protokolliert, der Zustand bleibt.
"""

from dataclasses import dataclass
from datetime import datetime

import psycopg

from contracts.errors import LeaseLost, TransitionRejected
from contracts.states import CLAIMABLE_STATES, TERMINAL_STATES, JobState, is_allowed

_CLAIMABLE = [s.value for s in CLAIMABLE_STATES]


@dataclass(frozen=True)
class JobLease:
    job_id: str
    owner: str
    version: int

    def verify(self, conn: psycopg.Connection) -> dict:
        """Sperrt die Auftragszeile für die laufende Transaktion; wirft LeaseLost."""
        row = conn.execute(
            """
            SELECT * FROM jobs
            WHERE id = %s AND lease_owner = %s AND lease_version = %s
              AND lease_expires_at > now()
            FOR UPDATE
            """,
            (self.job_id, self.owner, self.version),
        ).fetchone()
        if row is None:
            raise LeaseLost(
                f"Besitz an Auftrag {self.job_id} (Version {self.version}) gilt nicht mehr"
            )
        return row

    @property
    def lock_owner_ref(self) -> str:
        """Besitzerreferenz, unter der ein Auftrag die Ziel-Sperre hält."""
        return f"job:{self.job_id}"


@dataclass(frozen=True)
class Claim:
    job: dict
    lease: JobLease
    expires_at: datetime

    @property
    def resumed(self) -> bool:
        return self.job["state"] != JobState.QUEUED or self.job["attempts"] > 1


def claim_next(conn: psycopg.Connection, worker_id: str, lease_seconds: int) -> Claim | None:
    """Übernimmt atomar den ältesten wartenden oder verwaisten Auftrag."""
    with conn.transaction():
        row = conn.execute(
            """
            UPDATE jobs
               SET lease_owner = %(worker)s,
                   lease_expires_at = now() + make_interval(secs => %(secs)s),
                   lease_version = lease_version + 1,
                   attempts = attempts + 1,
                   updated_at = now()
             WHERE id = (
                   SELECT id FROM jobs
                    WHERE state = ANY(%(states)s)
                      AND (lease_expires_at IS NULL OR lease_expires_at <= now())
                    ORDER BY created_at
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1)
            RETURNING *
            """,
            {"worker": worker_id, "secs": lease_seconds, "states": _CLAIMABLE},
        ).fetchone()
    if row is None:
        return None
    lease = JobLease(str(row["id"]), worker_id, row["lease_version"])
    return Claim(job=row, lease=lease, expires_at=row["lease_expires_at"])


def renew(conn: psycopg.Connection, lease: JobLease, lease_seconds: int) -> datetime:
    with conn.transaction():
        row = conn.execute(
            """
            UPDATE jobs
               SET lease_expires_at = now() + make_interval(secs => %s), updated_at = now()
             WHERE id = %s AND lease_owner = %s AND lease_version = %s
               AND lease_expires_at > now()
            RETURNING lease_expires_at
            """,
            (lease_seconds, lease.job_id, lease.owner, lease.version),
        ).fetchone()
    if row is None:
        raise LeaseLost(f"Erneuerung von Auftrag {lease.job_id} abgewiesen")
    return row["lease_expires_at"]


def release_lease(conn: psycopg.Connection, lease: JobLease) -> None:
    """Gibt den Auftrag sofort frei (kontrolliertes Beenden); Zustand bleibt erhalten."""
    with conn.transaction():
        conn.execute(
            """
            UPDATE jobs SET lease_expires_at = now(), updated_at = now()
             WHERE id = %s AND lease_owner = %s AND lease_version = %s
            """,
            (lease.job_id, lease.owner, lease.version),
        )


def get_job(conn: psycopg.Connection, job_id: str) -> dict | None:
    return conn.execute("SELECT * FROM jobs WHERE id = %s", (job_id,)).fetchone()


def request_transition(
    conn: psycopg.Connection,
    lease: JobLease,
    to_state: JobState | str,
    reason: str,
    *,
    requested_by: str,
    expected_release: dict | None = None,
) -> None:
    """Prüft und speichert einen Übergang; wirft TransitionRejected nach Protokollierung."""
    to_state = JobState(to_state)
    with conn.transaction():
        job = lease.verify(conn)
        problem = _check_transition(conn, job, to_state, expected_release)
        _apply(conn, job, to_state, reason, problem, requested_by)
    if problem:
        raise TransitionRejected(job["state"], to_state, problem)


def transition_unleased(
    conn: psycopg.Connection, job_id: str, to_state: JobState | str, reason: str, *, requested_by: str
) -> None:
    """Übergang ohne Worker-Besitz, nur für Eingang und Abbruch (nie resolved)."""
    to_state = JobState(to_state)
    if to_state == JobState.RESOLVED:
        raise ValueError("resolved nur über request_transition mit Nachweisen")
    with conn.transaction():
        job = conn.execute("SELECT * FROM jobs WHERE id = %s FOR UPDATE", (job_id,)).fetchone()
        problem = _check_transition(conn, job, to_state, None)
        _apply(conn, job, to_state, reason, problem, requested_by)
    if problem:
        raise TransitionRejected(job["state"], to_state, problem)


def _apply(conn, job, to_state, reason, problem, requested_by) -> None:
    conn.execute(
        """
        INSERT INTO job_transitions (job_id, from_state, to_state, accepted, reason, requested_by)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (job["id"], job["state"], to_state.value, problem is None, problem or reason, requested_by),
    )
    if problem is None:
        conn.execute(
            "UPDATE jobs SET state = %s, state_reason = %s, updated_at = now() WHERE id = %s",
            (to_state.value, reason, job["id"]),
        )


def _check_transition(conn, job, to_state: JobState, expected_release: dict | None) -> str | None:
    from_state = JobState(job["state"])
    if from_state in TERMINAL_STATES:
        return f"Auftrag ist bereits abgeschlossen ({from_state})"
    if not is_allowed(from_state, to_state):
        return f"Übergang {from_state} -> {to_state} ist nicht zulässig"
    if to_state == JobState.RESOLVED:
        return check_resolution(conn, job, expected_release)
    return None


# --- Abschlussprüfung -------------------------------------------------------------


def reference_time(conn: psycopg.Connection, job: dict) -> tuple[datetime, str]:
    """Zeitpunkt der letzten Maßnahme, ohne Maßnahme der Beginn der Untersuchung."""
    row = conn.execute(
        """
        SELECT max(coalesce(a.finished_at, a.updated_at)) AS t
          FROM actions a
         WHERE a.mutating
           AND (a.job_id = %(job)s
                OR a.operation_id IN (SELECT id FROM operations WHERE job_id = %(job)s))
        """,
        {"job": job["id"]},
    ).fetchone()
    if row["t"] is not None:
        return row["t"], "der letzten Maßnahme"
    row = conn.execute(
        """
        SELECT min(created_at) AS t FROM job_transitions
         WHERE job_id = %s AND to_state = 'investigating' AND accepted
        """,
        (job["id"],),
    ).fetchone()
    if row["t"] is not None:
        return row["t"], "dem Beginn der Untersuchung"
    return job["created_at"], "der Auftragsanlage"


def release_matches(expected: dict, actual: dict | None) -> bool:
    if not actual:
        return False
    if expected.get("image_digest") != actual.get("image_digest"):
        return False
    if expected.get("chart_version") != actual.get("chart_version"):
        return False
    if expected.get("git_sha") and actual.get("git_sha") and expected["git_sha"] != actual["git_sha"]:
        return False
    return True


def _fmt_release(release: dict | None) -> str:
    if not release:
        return "unbekannt"
    return f"{release.get('image_digest')} / Chart {release.get('chart_version')}"


def check_resolution(conn: psycopg.Connection, job: dict, expected_release: dict | None) -> str | None:
    """Liefert den Ablehnungsgrund für resolved oder None, wenn alle Nachweise passen."""
    if not expected_release:
        return "erwarteter Zielstand (expected_release) fehlt"
    since, label = reference_time(conn, job)
    params = {"job": job["id"], "app": job["app_id"], "env": job["environment"], "since": since}

    smoke = conn.execute(
        """
        SELECT * FROM verifications
         WHERE job_id = %(job)s AND kind = 'smoke-test' AND success
           AND app_id = %(app)s AND environment = %(env)s AND started_at > %(since)s
         ORDER BY started_at DESC LIMIT 1
        """,
        params,
    ).fetchone()
    if smoke is None:
        stale = conn.execute(
            """
            SELECT 1 FROM verifications
             WHERE job_id = %(job)s AND kind = 'smoke-test' AND success LIMIT 1
            """,
            params,
        ).fetchone()
        if stale:
            return (
                f"positiver Test liegt nur vor {label} ({since.isoformat()}); "
                "neue passende Verifikation erforderlich"
            )
        return f"kein positiver fachlicher Test nach {label} für {job['app_id']}/{job['environment']}"
    if not release_matches(expected_release, smoke["release"]):
        return (
            f"Test lief gegen Release {_fmt_release(smoke['release'])}, "
            f"erwartet {_fmt_release(expected_release)}"
        )

    k8s = conn.execute(
        """
        SELECT * FROM verifications
         WHERE job_id = %(job)s AND kind = 'k8s-state'
           AND app_id = %(app)s AND environment = %(env)s AND started_at > %(since)s
         ORDER BY started_at DESC LIMIT 1
        """,
        params,
    ).fetchone()
    if k8s is None:
        return f"kein nach {label} erfasster Kubernetes-Zustand des Ziels"
    if not k8s["success"]:
        return "erfasster Kubernetes-Zustand des Ziels ist nicht bereit"
    if not release_matches(expected_release, k8s["release"]):
        return (
            f"Kubernetes-Zustand zeigt Release {_fmt_release(k8s['release'])}, "
            f"erwartet {_fmt_release(expected_release)}"
        )
    return None
