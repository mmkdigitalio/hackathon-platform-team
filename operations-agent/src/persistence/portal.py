"""Zugangscodes, transaktionaler Chat-Eingang und öffentliche Verlaufsprojektion."""

import hashlib
import json
import re
import secrets
import uuid
from datetime import UTC, datetime

import psycopg

from . import intake

CLIENT_MESSAGE_ID = re.compile(r"[A-Za-z0-9._-]{1,100}")
_CODE_FIELDS = "id, label, created_at, expires_at, revoked_at, last_used_at"


class RateLimited(Exception):
    """Der Code hat bereits 20 Nachrichten innerhalb von zehn Minuten gesendet."""


def create_code(conn: psycopg.Connection, label: str, expires_days: int | None = None) -> tuple[str, str]:
    if not isinstance(label, str) or not label.strip():
        raise ValueError("Bezeichnung fehlt")
    if expires_days is not None and expires_days <= 0:
        raise ValueError("Ablauf muss mindestens einen Tag betragen")
    code = secrets.token_urlsafe(18)
    row = conn.execute(
        """INSERT INTO portal_access_codes (label, code_hash, expires_at)
           VALUES (%s, %s, CASE WHEN %s::integer IS NULL THEN NULL
                               ELSE now() + make_interval(days => %s) END) RETURNING id""",
        (label, hashlib.sha256(code.encode()).hexdigest(), expires_days, expires_days),
    ).fetchone()
    return str(row["id"]), code


def list_codes(conn: psycopg.Connection) -> list[dict]:
    return conn.execute(f"SELECT {_CODE_FIELDS} FROM portal_access_codes ORDER BY created_at, id").fetchall()


def revoke_code(conn: psycopg.Connection, code_id: str) -> bool:
    uuid.UUID(code_id)
    return conn.execute(
        "UPDATE portal_access_codes SET revoked_at = coalesce(revoked_at, now()) WHERE id = %s RETURNING id",
        (code_id,),
    ).fetchone() is not None


def verify_code(conn: psycopg.Connection, code: str) -> str | None:
    if not code:
        return None
    # Hash-Lookup statt Klartextvergleich; die Zeilensperre serialisiert das Nachrichtenlimit.
    row = conn.execute(
        """UPDATE portal_access_codes SET last_used_at = now()
             WHERE code_hash = %s AND revoked_at IS NULL
               AND (expires_at IS NULL OR expires_at > now()) RETURNING id""",
        (hashlib.sha256(code.encode()).hexdigest(),),
    ).fetchone()
    return str(row["id"]) if row else None


def check_rate_limit(conn: psycopg.Connection, code_id: str) -> None:
    conn.execute("SELECT id FROM portal_access_codes WHERE id = %s FOR UPDATE", (code_id,))
    row = conn.execute(
        """SELECT count(*) AS n FROM conversation_messages
             WHERE access_code_id = %s AND role = 'reporter'
               AND created_at > now() - interval '10 minutes'""", (code_id,),
    ).fetchone()
    if row["n"] >= 20:
        raise RateLimited()


def validate_message(reporter: str, text: str, client_message_id: str) -> None:
    for name, value, limit in (("reporter", reporter, 80), ("text", text, 4000)):
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            raise ValueError(f"{name} muss 1 bis {limit} Zeichen enthalten")
    if not isinstance(client_message_id, str) or not CLIENT_MESSAGE_ID.fullmatch(client_message_id):
        raise ValueError("clientMessageId muss 1 bis 100 Zeichen aus A-Z, a-z, 0-9, Punkt, _ oder - enthalten")


def _incident(conn, conversation_id: str) -> dict | None:
    return conn.execute(
        "SELECT id, incident_key, app_id, environment FROM incidents WHERE source = 'portal' AND incident_key = %s",
        (conversation_id,),
    ).fetchone()


def store_message(
    conn: psycopg.Connection, *, code_id: str, reporter: str, text: str, client_message_id: str,
    conversation_id: str | None = None, app_id: str | None = None, environment: str | None = None,
) -> dict | None:
    """Speichert Ereignis und Nachricht atomar; None bedeutet unbekannte Unterhaltung.

    Zustellungs-IDs gelten wie bei allen Quellen global je Quelle. Ein Retry der
    ersten Nachricht liefert deshalb auch ohne bekannte Unterhaltungs-ID dieselbe Antwort.
    """
    validate_message(reporter, text, client_message_id)
    with conn.transaction():
        conn.execute("SELECT id FROM portal_access_codes WHERE id = %s FOR UPDATE", (code_id,))
        # Auch bei zwei verschiedenen Codes dieselbe Zustellung nur einmal annehmen.
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"portal:{client_message_id}",))
        incident = _incident(conn, conversation_id) if conversation_id else None
        if conversation_id and incident is None:
            return None
        existing = conn.execute(
            """SELECT m.id, m.job_id, i.incident_key FROM events e
               JOIN incidents i ON i.id = e.incident_id
               JOIN conversation_messages m ON m.incident_id = e.incident_id
                                            AND m.client_message_id = e.delivery_id
               WHERE e.source = 'portal' AND e.delivery_id = %s""", (client_message_id,),
        ).fetchone()
        if existing:
            if conversation_id and existing["incident_key"] != conversation_id:
                raise ValueError("clientMessageId gehört zu einer anderen Unterhaltung")
            return _receipt(existing["incident_key"], existing["id"], existing["job_id"], True)
        check_rate_limit(conn, code_id)
        if incident:
            app_id, environment = incident["app_id"], incident["environment"]
        elif not app_id or not environment:
            raise ValueError("App und Umgebung fehlen")
        conversation_id = conversation_id or str(uuid.uuid4())
        payload = {"reporter": reporter, "text": text, "conversationId": conversation_id}
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        now = datetime.now(UTC).isoformat()
        event = {
            "schemaVersion": "0.1", "source": "portal", "deliveryId": client_message_id,
            "externalRef": conversation_id, "appId": app_id, "environment": environment,
            "eventType": "chat-message", "severity": "unknown", "state": "reported",
            "occurredAt": now, "receivedAt": now,
            "sourceRef": {"type": "portal-message", "reporter": reporter,
                          "payloadSha256": hashlib.sha256(raw).hexdigest()},
        }
        result = intake.store_event(conn, event, payload)
        row = conn.execute(
            """INSERT INTO conversation_messages
                   (incident_id, job_id, role, author, body, client_message_id, access_code_id)
               VALUES (%s, %s, 'reporter', %s, %s, %s, %s) RETURNING id""",
            (result.incident_id, result.job_id, reporter, text, client_message_id, code_id),
        ).fetchone()
        return _receipt(conversation_id, row["id"], result.job_id, False)


def _receipt(conversation_id, message_id, job_id, duplicate) -> dict:
    return {"conversationId": conversation_id, "messageId": str(message_id),
            "jobId": str(job_id), "status": "duplicate" if duplicate else "accepted"}


def conversation_messages(conn: psycopg.Connection, conversation_id: str) -> list[dict]:
    return conn.execute(
        """SELECT m.id, m.role, m.author, m.kind, m.body, m.created_at, m.job_id
             FROM conversation_messages m JOIN incidents i ON i.id = m.incident_id
            WHERE i.source = 'portal' AND i.incident_key = %s ORDER BY m.created_at, m.id""",
        (conversation_id,),
    ).fetchall()


def read_conversation(conn: psycopg.Connection, conversation_id: str) -> dict | None:
    incident = _incident(conn, conversation_id)
    if incident is None:
        return None
    messages = conversation_messages(conn, conversation_id)
    rows = conn.execute(
        """SELECT j.id, j.state, j.created_at, j.updated_at,
                  (SELECT max(t.created_at) FROM job_transitions t
                   WHERE t.job_id = j.id AND t.accepted) AS last_transition_at
             FROM jobs j WHERE j.incident_id = %s ORDER BY j.created_at, j.id""", (incident["id"],),
    ).fetchall()
    return {
        "conversationId": conversation_id, "appId": incident["app_id"], "environment": incident["environment"],
        "messages": [{"id": str(m["id"]), "role": m["role"], "author": m["author"], "kind": m["kind"],
                      "body": m["body"], "createdAt": m["created_at"].isoformat(),
                      "jobId": str(m["job_id"]) if m["job_id"] else None} for m in messages],
        "jobs": [{"id": str(j["id"]), "state": j["state"], "createdAt": j["created_at"].isoformat(),
                  "updatedAt": j["updated_at"].isoformat(),
                  "lastTransitionAt": j["last_transition_at"].isoformat() if j["last_transition_at"] else None}
                 for j in rows],
    }
