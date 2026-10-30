"""Normalisierung der Quellformate auf den Ereignisvertrag v0.1 (platform/schemas/events/)."""

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime

from .catalog import ENVIRONMENTS, Catalog

SCHEMA_VERSION = "0.1"
AGENT_MARKER = "<!-- mmk-ops-agent -->"

_RULE_NAME = re.compile(
    r"^alert-(?P<app>[a-z][a-z0-9-]{1,30}?)-(?P<env>" + "|".join(ENVIRONMENTS) + r")-(?P<check>[a-z0-9][a-z0-9-]*)$"
)
_SEVERITY = {
    "sev0": "critical",
    "sev1": "error",
    "sev2": "warning",
    "sev3": "informational",
    "sev4": "verbose",
}


class InvalidPayload(ValueError):
    pass


@dataclass(frozen=True)
class Ignored:
    reason: str


def payload_sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _event(*, catalog: Catalog, raw: bytes, app_id, environment, mapping_error=None, **fields) -> dict:
    if mapping_error is None:
        mapping_error = catalog.problem(app_id, environment)
    event = {
        "schemaVersion": SCHEMA_VERSION,
        **fields,
        "appId": app_id or None,
        "environment": environment or None,
        "receivedAt": _now(),
        "mappingError": mapping_error,
    }
    event["sourceRef"] = {**event["sourceRef"], "payloadSha256": payload_sha256(raw)}
    if mapping_error:
        # Nur konventionskonforme Werte bleiben stehen; der Rest steht in den Quelldaten.
        if event["appId"] and not re.match(r"^[a-z][a-z0-9-]{1,30}$", event["appId"]):
            event["appId"] = None
        if event["environment"] not in ENVIRONMENTS:
            event["environment"] = None
    return event


# --- Azure Monitor -----------------------------------------------------------------


def alert_mapping(data: dict) -> tuple[str | None, str | None, str]:
    """App und Umgebung aus Custom Properties, sonst aus dem Regelnamen."""
    props = data.get("customProperties") or {}
    if props.get("app-id") and props.get("environment"):
        return props["app-id"], props["environment"], "customProperties"
    rule = (data.get("essentials") or {}).get("alertRule") or ""
    match = _RULE_NAME.match(rule)
    if match:
        return match["app"], match["env"], "alertRule"
    return None, None, "none"


def azure_monitor(body: dict, raw: bytes, catalog: Catalog) -> dict:
    if body.get("schemaId") != "azureMonitorCommonAlertSchema":
        raise InvalidPayload("schemaId muss azureMonitorCommonAlertSchema sein")
    data = body.get("data") or {}
    essentials = data.get("essentials") or {}
    alert_id = essentials.get("alertId")
    condition = essentials.get("monitorCondition")
    if not alert_id:
        raise InvalidPayload("data.essentials.alertId fehlt")
    if condition not in ("Fired", "Resolved"):
        raise InvalidPayload("data.essentials.monitorCondition muss Fired oder Resolved sein")
    app_id, environment, via = alert_mapping(data)
    mapping_error = None
    if via == "none":
        mapping_error = (
            "weder Custom Properties app-id/environment noch Regelname "
            f"alert-<app-id>-<env>-<check> (Regel {essentials.get('alertRule')!r})"
        )
    occurred = essentials.get("resolvedDateTime") if condition == "Resolved" else essentials.get("firedDateTime")
    return _event(
        catalog=catalog,
        raw=raw,
        app_id=app_id,
        environment=environment,
        mapping_error=mapping_error,
        source="azure-monitor",
        deliveryId=f"{alert_id}:{condition}",
        externalRef=alert_id,
        eventType="alert",
        severity=_SEVERITY.get(str(essentials.get("severity", "")).lower(), "unknown"),
        state=condition.lower(),
        occurredAt=occurred or None,
        sourceRef={"type": "azure-alert"},
    )


# --- GitHub ------------------------------------------------------------------------


def issue_form_field(body: str | None, heading: str) -> str | None:
    """Liest den Wert unter ``### <heading>`` aus einem über die Issue-Form erzeugten Text."""
    if not body:
        return None
    lines = body.replace("\r\n", "\n").split("\n")
    for i, line in enumerate(lines):
        if line.strip().lower() == f"### {heading}".lower():
            for value in lines[i + 1:]:
                value = value.strip()
                if value.startswith("### "):
                    return None
                if value:
                    return None if value == "_No response_" else value
    return None


def github(event_name: str, delivery_id: str, body: dict, raw: bytes, catalog: Catalog,
           allowlist: set[str]) -> dict | Ignored:
    if event_name == "ping":
        return Ignored("ping")
    repo = ((body.get("repository") or {}).get("full_name") or "")
    if repo.lower() not in allowlist:
        raise PermissionError(f"Repository {repo!r} nicht in der Allowlist")
    action = body.get("action")
    issue = body.get("issue") or {}
    if event_name == "issues" and action == "opened":
        text, state, event_type, ref_type = issue.get("body"), "opened", "issue-opened", "github-issue"
        url, occurred = issue.get("html_url"), issue.get("created_at")
    elif event_name == "issue_comment" and action == "created":
        if issue.get("pull_request"):
            return Ignored("Kommentar zu einem Pull Request")
        comment = body.get("comment") or {}
        text, state, event_type, ref_type = comment.get("body"), "commented", "issue-commented", "github-comment"
        url, occurred = comment.get("html_url"), comment.get("created_at")
    else:
        return Ignored(f"Ereignis {event_name}.{action} wird nicht verarbeitet")
    # Schleifenschutz: nur die Markierung zählt, nicht der Autor (E7).
    if text and AGENT_MARKER in text:
        return Ignored("Agentenkommentar (Markierung mmk-ops-agent)")
    if not issue.get("number"):
        raise InvalidPayload("issue.number fehlt")
    if not delivery_id:
        raise InvalidPayload("Header X-GitHub-Delivery fehlt")

    app_id = issue_form_field(issue.get("body"), "App")
    environment = issue_form_field(issue.get("body"), "Umgebung")
    mapping_error = None
    if not app_id or not environment:
        mapping_error = "Issue enthält die Abschnitte ### App und ### Umgebung der Support-Form nicht"
    return _event(
        catalog=catalog,
        raw=raw,
        app_id=app_id,
        environment=environment,
        mapping_error=mapping_error,
        source="github",
        deliveryId=delivery_id,
        externalRef=f"{repo}#{issue['number']}",
        eventType=event_type,
        severity="unknown",
        state=state,
        occurredAt=occurred,
        sourceRef={"type": ref_type, **({"url": url} if url else {})},
    )


# --- Wartung -----------------------------------------------------------------------


def maintenance(body: dict, raw: bytes, catalog: Catalog) -> dict:
    for field in ("deliveryId", "externalRef"):
        if not isinstance(body.get(field), str) or not body[field]:
            raise InvalidPayload(f"{field} fehlt")
    event_type = body.get("eventType") or "maintenance"
    if not re.match(r"^[a-z][a-z0-9-]{0,62}$", event_type):
        raise InvalidPayload("eventType ungültig")
    severity = body.get("severity") or "warning"
    if severity not in ("critical", "error", "warning", "informational", "verbose", "unknown"):
        raise InvalidPayload("severity ungültig")
    return _event(
        catalog=catalog,
        raw=raw,
        app_id=body.get("appId"),
        environment=body.get("environment"),
        source="maintenance",
        deliveryId=body["deliveryId"],
        externalRef=body["externalRef"],
        eventType=event_type,
        severity=severity,
        state="requested",
        occurredAt=body.get("occurredAt"),
        sourceRef={"type": "maintenance-request"},
    )


def parse_json(raw: bytes) -> dict:
    try:
        body = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise InvalidPayload("Body ist kein gültiges JSON") from exc
    if not isinstance(body, dict):
        raise InvalidPayload("Body muss ein JSON-Objekt sein")
    return body
