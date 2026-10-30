"""Laufnachweise je Auftrag als JSON, gemeinsam für GET /runs/{id} und ops runs show."""

import json
import os
import re
from datetime import datetime
from decimal import Decimal
from uuid import UUID

import psycopg

# Umgebungsvariablen, deren Werte nie in Nachweisen erscheinen dürfen.
SECRET_ENV_VARS = (
    "ALERT_WEBHOOK_KEY",
    "MAINTENANCE_API_KEY",
    "GITHUB_WEBHOOK_SECRET",
    "OPS_GITHUB_TOKEN",
    "OPS_GITHUB_APP_PRIVATE_KEY",
    "OPS_MODEL_API_KEYS",
    "PGPASSWORD",
    "POSTGRES_PASSWORD",
    "ANTHROPIC_API_KEY",
    "AZURE_OPENAI_API_KEY",
)
_SECRET_KEY_NAMES = re.compile(r"(pass(word)?|secret|token|api[-_]?key|authorization|signature)$", re.I)
_URL_PASSWORD = re.compile(r"(\w+://[^:/@\s]+:)[^@\s]+@")
# Private Schlüssel (z. B. der GitHub App) auch dann, wenn sie in anderer Formatierung auftauchen.
_PEM = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S)
REDACTED = "***"


def known_secrets(extra: list[str] | None = None) -> list[str]:
    values = [os.environ.get(name, "") for name in SECRET_ENV_VARS] + list(extra or [])
    db_url = os.environ.get("OPS_DATABASE_URL", "")
    match = re.search(r"://[^:/@]+:([^@]+)@", db_url)
    if match:
        values.append(match[1])
    # Kurze Werte würden beliebige Texte zerstören; echte Secrets sind lang.
    return sorted({v for v in values if v and len(v) >= 8}, key=len, reverse=True)


def redact(value, secrets: list[str]):
    if isinstance(value, dict):
        return {
            k: (REDACTED if _SECRET_KEY_NAMES.search(str(k)) and v not in (None, "") else redact(v, secrets))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(v, secrets) for v in value]
    if isinstance(value, str):
        for secret in secrets:
            value = value.replace(secret, REDACTED)
        value = _PEM.sub(REDACTED, value)
        return _URL_PASSWORD.sub(r"\1" + REDACTED + "@", value)
    return value


def _plain(value):
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (UUID, Decimal)):
        return str(value)
    return value


def get_run(conn: psycopg.Connection, job_id: str, *, secrets: list[str] | None = None) -> dict | None:
    job = conn.execute("SELECT * FROM jobs WHERE id = %s", (job_id,)).fetchone()
    if job is None:
        return None
    q = lambda sql: conn.execute(sql, {"job": job_id}).fetchall()  # noqa: E731

    incident = conn.execute("SELECT * FROM incidents WHERE id = %s", (job["incident_id"],)).fetchone()
    events = q(
        """
        SELECT id, schema_version, source, delivery_id, external_ref, app_id, environment,
               event_type, severity, state, occurred_at, received_at, source_ref,
               mapping_error, payload
          FROM events WHERE job_id = %(job)s ORDER BY received_at
        """
    )
    transitions = q(
        """
        SELECT from_state, to_state, accepted, reason, requested_by, created_at
          FROM job_transitions WHERE job_id = %(job)s ORDER BY id
        """
    )
    executions = q("SELECT * FROM executions WHERE job_id = %(job)s ORDER BY started_at")
    operations = q(
        """
        SELECT * FROM operations
         WHERE job_id = %(job)s
            OR id IN (SELECT operation_id FROM actions WHERE job_id = %(job)s)
         ORDER BY started_at
        """
    )
    actions = q(
        """
        SELECT * FROM actions
         WHERE job_id = %(job)s
            OR operation_id IN (SELECT id FROM operations WHERE job_id = %(job)s)
         ORDER BY created_at
        """
    )
    verifications = q("SELECT * FROM verifications WHERE job_id = %(job)s ORDER BY started_at")
    findings = q("SELECT * FROM findings WHERE job_id = %(job)s ORDER BY id")
    tool_calls = q("SELECT * FROM tool_calls WHERE job_id = %(job)s ORDER BY started_at, id")

    run = {
        "runVersion": "0.1",
        "job": {
            "id": job["id"],
            "state": job["state"],
            "stateReason": job["state_reason"],
            "appId": job["app_id"],
            "environment": job["environment"],
            "attempts": job["attempts"],
            "createdAt": job["created_at"],
            "updatedAt": job["updated_at"],
        },
        "incident": incident,
        "events": events,
        "transitions": transitions,
        "executions": [
            {
                "id": e["id"],
                "workerId": e["worker_id"],
                "resumed": e["resumed"],
                "startedAt": e["started_at"],
                "finishedAt": e["finished_at"],
                "proposedState": e["proposed_state"],
                "reason": e["reason"],
                "limitReached": e["limit_reached"],
                "requiredChange": e["required_change"],
                "codeVersion": e["code_version"],
                "configRef": e["config_ref"],
                "modelSettings": e["model_settings"],
                "usage": e["usage"],
                "references": e["references"],
                "error": e["error"],
            }
            for e in executions
        ],
        "operations": operations,
        "actions": actions,
        "toolCalls": tool_calls,
        "verifications": verifications,
        "findings": {
            "confirmed": [f for f in findings if f["confirmed"]],
            "suspected": [f for f in findings if not f["confirmed"]],
        },
    }
    plain = json.loads(json.dumps(_plain(run), default=str))
    return redact(plain, known_secrets(secrets))
