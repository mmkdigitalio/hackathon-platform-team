"""Auswertung der Alarm-Payload (Azure Monitor Common Alert Schema) ohne Azure-Zugriff."""

from __future__ import annotations

from typing import Any


def summarize_alert(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not payload:
        return None
    data = payload.get("data", payload)
    ess = data.get("essentials", {})
    ctx = data.get("alertContext") or {}
    custom = data.get("customProperties") or {}
    conditions = (ctx.get("condition") or {}).get("allOf") or []
    results = [
        {
            "metric": c.get("metricName"),
            "operator": c.get("operator"),
            "threshold": c.get("threshold"),
            "value": c.get("metricValue"),
            "webTest": c.get("webTestName"),
            "failedLocations": c.get("failedLocationCount"),
        }
        for c in conditions
    ]
    return {
        "alertId": ess.get("alertId"),
        "rule": ess.get("alertRule"),
        "severity": ess.get("severity"),
        "condition": ess.get("monitorCondition"),
        "firedAt": ess.get("firedDateTime"),
        "resolvedAt": ess.get("resolvedDateTime"),
        "appId": custom.get("app-id"),
        "environment": custom.get("environment"),
        "window": {
            "start": (ctx.get("condition") or {}).get("windowStartTime"),
            "end": (ctx.get("condition") or {}).get("windowEndTime"),
        },
        "testResults": results,
    }
