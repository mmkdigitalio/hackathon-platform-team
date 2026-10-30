"""Transaktionale Ereignisannahme: Ereignis, Vorfall und Auftrag in einer Transaktion.

Erst nach dem Commit darf die API die Annahme bestätigen. Dieselbe Zustellung
(``source`` + ``deliveryId``) verweist auf den bereits gespeicherten Vorgang.
"""

from dataclasses import dataclass

import psycopg
from psycopg.types.json import Jsonb

from contracts.states import JobState

from .jobs import transition_unleased


@dataclass(frozen=True)
class IntakeResult:
    event_id: str
    incident_id: str
    job_id: str | None
    duplicate: bool
    mapping_error: str | None = None

    def as_response(self) -> dict:
        return {
            "status": "duplicate" if self.duplicate else "accepted",
            "eventId": self.event_id,
            "incidentId": self.incident_id,
            "jobId": self.job_id,
            "mappingError": self.mapping_error,
        }


class _Duplicate(Exception):
    pass


def incident_key(event: dict) -> str:
    # Azure: alertId; GitHub: Issue-Referenz; Wartung: vom Aufrufer gesetzte externalRef.
    return event["externalRef"]


def store_event(conn: psycopg.Connection, event: dict, payload: dict) -> IntakeResult:
    """Speichert ein normalisiertes Ereignis (Vertrag platform/schemas/events/ v0.1)."""
    existing = _find(conn, event)
    if existing:
        return existing
    try:
        with conn.transaction():
            return _store(conn, event, payload)
    except _Duplicate:
        # Gleichzeitige Zustellung hat gewonnen; deren Ergebnis zählt.
        return _find(conn, event)


def _find(conn, event) -> IntakeResult | None:
    row = conn.execute(
        """
        SELECT id, incident_id, job_id, mapping_error FROM events
         WHERE source = %s AND delivery_id = %s
        """,
        (event["source"], event["deliveryId"]),
    ).fetchone()
    if row is None:
        return None
    return IntakeResult(
        event_id=str(row["id"]),
        incident_id=str(row["incident_id"]),
        job_id=str(row["job_id"]) if row["job_id"] else None,
        duplicate=True,
        mapping_error=row["mapping_error"],
    )


def _store(conn, event, payload) -> IntakeResult:
    mapping_error = event.get("mappingError")
    alert_state = event["state"] if event["source"] == "azure-monitor" else None
    incident = conn.execute(
        """
        INSERT INTO incidents (source, incident_key, app_id, environment, alert_state)
        VALUES (%(source)s, %(key)s, %(app)s, %(env)s, %(alert)s)
        ON CONFLICT (source, incident_key) DO UPDATE
           SET alert_state = coalesce(EXCLUDED.alert_state, incidents.alert_state),
               app_id = coalesce(incidents.app_id, EXCLUDED.app_id),
               environment = coalesce(incidents.environment, EXCLUDED.environment),
               updated_at = now()
        RETURNING *
        """,
        {
            "source": event["source"],
            "key": incident_key(event),
            "app": None if mapping_error else event["appId"],
            "env": None if mapping_error else event["environment"],
            "alert": alert_state,
        },
    ).fetchone()

    job_id = _job_for(conn, incident, event, mapping_error)

    row = conn.execute(
        """
        INSERT INTO events (schema_version, source, delivery_id, external_ref, app_id,
                            environment, event_type, severity, state, occurred_at,
                            received_at, source_ref, payload, mapping_error,
                            incident_id, job_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (source, delivery_id) DO NOTHING
        RETURNING id
        """,
        (
            event["schemaVersion"], event["source"], event["deliveryId"], event["externalRef"],
            event["appId"], event["environment"], event["eventType"], event["severity"],
            event["state"], event["occurredAt"], event["receivedAt"], Jsonb(event["sourceRef"]),
            Jsonb(payload), mapping_error, incident["id"], job_id,
        ),
    ).fetchone()
    if row is None:
        raise _Duplicate()
    return IntakeResult(
        event_id=str(row["id"]),
        incident_id=str(incident["id"]),
        job_id=job_id,
        duplicate=False,
        mapping_error=mapping_error,
    )


def _job_for(conn, incident, event, mapping_error) -> str | None:
    """Hängt das Ereignis an den aktiven Auftrag des Vorfalls oder legt einen an."""
    active = conn.execute(
        """
        SELECT id FROM jobs
         WHERE incident_id = %s
           AND state IN ('received', 'queued', 'investigating', 'acting', 'verifying')
        """,
        (incident["id"],),
    ).fetchone()
    if active:
        return str(active["id"])

    if event["source"] == "azure-monitor" and event["state"] == "resolved":
        # Resolved aktualisiert nur den Alarmzustand; kein neuer Auftrag, kein Abschluss.
        return _latest_job(conn, incident["id"])

    if mapping_error:
        latest = _latest_job(conn, incident["id"])
        if latest:
            return latest
        job_id = _new_job(conn, incident, None, None)
        transition_unleased(
            conn, job_id, JobState.NEEDS_ATTENTION, f"Zuordnungsfehler: {mapping_error}",
            requested_by="intake",
        )
        return job_id

    job_id = _new_job(conn, incident, event["appId"], event["environment"])
    transition_unleased(conn, job_id, JobState.QUEUED, "Ereignis angenommen", requested_by="intake")
    return job_id


def _latest_job(conn, incident_id) -> str | None:
    row = conn.execute(
        "SELECT id FROM jobs WHERE incident_id = %s ORDER BY created_at DESC LIMIT 1",
        (incident_id,),
    ).fetchone()
    return str(row["id"]) if row else None


def _new_job(conn, incident, app_id, environment) -> str:
    row = conn.execute(
        """
        INSERT INTO jobs (incident_id, app_id, environment, state)
        VALUES (%s, %s, %s, 'received')
        RETURNING id
        """,
        (incident["id"], app_id, environment),
    ).fetchone()
    return str(row["id"])
