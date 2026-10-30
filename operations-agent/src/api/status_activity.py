"""Allowlisted read-only admin history. Never publish payloads, reasons or internal IDs."""

import re
from datetime import datetime

RUN_URL = re.compile(r"https://github\.com/mmkdigitalio/hackathon-platform-team/actions/runs/[0-9]+$")
SHA = re.compile(r"[a-f0-9]{40}$")
DIGEST = re.compile(r"sha256:[a-f0-9]{64}$")
VERSION = re.compile(r"[a-zA-Z0-9.+_-]{1,60}$")
ACTION_NAMES = {
    "deploy": "Deployment", "restart": "Neustart", "redeploy": "Erneutes Deployment",
    "restart_workload": "Neustart", "workflow_dispatch": "Workflow gestartet",
    "dispatch": "Workflow gestartet", "git_commit": "Codeänderung vorbereitet",
    "git_push": "Agenten-Branch aktualisiert", "git_commit_push": "Agenten-Branch aktualisiert",
    "pull_request": "Pull Request vorbereitet", "issue_comment": "Support-Rückmeldung",
    "smoke_test": "Smoke-Test", "workspace_command": "Workspace-Prüfung",
}
SOURCES = {"azure-monitor": "Azure Monitor", "github": "GitHub", "maintenance": "Wartungsplanung"}


def iso(value):
    return value.isoformat() if isinstance(value, datetime) else None


def safe_value(value, pattern):
    return value if isinstance(value, str) and pattern.fullmatch(value) else None


def activity_item(row, app_ids):
    """Convert database enums to fixed descriptions, without forwarding any free text."""
    app = row["app_id"] if row["app_id"] in app_ids else None
    state, kind = row["state"], row["kind"]
    item = {"appId": app, "at": iso(row["at"]), "verified": False}
    if kind == "event":
        source = SOURCES.get(row["source"], "Systemsignal")
        cleared = state in ("resolved", "closed")
        title = "Entwarnung empfangen" if cleared else "Störungssignal empfangen" if row["source"] == "azure-monitor" else "Support-Meldung empfangen" if row["source"] == "github" else "Prüfung angefordert"
        return {**item, "category": "detected", "result": "closed" if cleared else "detected", "source": source, "title": title}
    if kind == "finding":
        return {**item, "category": "agent", "result": "detected", "source": "Agent",
                "title": "Befund bestätigt" if state == "confirmed" else "Vermutung dokumentiert"}
    if kind == "job":
        verified = state == "resolved" and row["verified"] is True
        title = ("Monitoring-Störung verifiziert behoben" if row["source"] == "azure-monitor" else "Support-Fall verifiziert abgeschlossen" if row["source"] == "github" else "Auftrag verifiziert abgeschlossen") if verified else "Auftrag abgeschlossen" if state == "resolved" else "Prüfung durch Admin erforderlich" if state == "needs_attention" else "Auftrag fehlgeschlagen"
        return {**item, "category": "resolved" if verified else "agent", "source": "Agent",
                "result": "success" if verified else "closed" if state == "resolved" else "attention" if state == "needs_attention" else "failure",
                "title": title, "verified": verified}
    result = {"planned": "running", "started": "running", "succeeded": "success", "failed": "failure", "unclear": "attention"}.get(state, "unknown")
    category = "system" if row["source"] in ("deploy", "break-glass") else "agent"
    return {**item, "category": category, "source": "System" if category == "system" else "Agent",
            "result": result, "title": ACTION_NAMES.get(row["action_kind"], "Betriebsaktion")}


def admin_projection(pool, environment, app_ids):
    empty = {"available": False, "activity": [], "perApp": {}, "deployments": {},
             "verifiedResolved24h": None, "activityLimit": 100, "windowDays": 7}
    if pool is None:
        return empty
    try:
        with pool.connection(timeout=3) as conn, conn.transaction():
            conn.execute("SET TRANSACTION READ ONLY")
            conn.execute("SET LOCAL statement_timeout = '2500ms'")
            rows = conn.execute(
                """SELECT * FROM (
                     (SELECT 'event' AS kind, app_id, received_at AS at, source, state,
                             '' AS action_kind, false AS verified, 'e' || id::text AS sort_key
                        FROM events WHERE environment = %(env)s AND received_at >= now() - interval '7 days'
                        ORDER BY received_at DESC, id DESC LIMIT 100)
                     UNION ALL
                     (SELECT 'action', a.app_id, a.updated_at, o.kind, a.status, a.kind, false, 'a' || a.id
                        FROM actions a JOIN operations o ON o.id = a.operation_id
                        WHERE a.environment = %(env)s AND a.updated_at >= now() - interval '7 days'
                        ORDER BY a.updated_at DESC, a.id DESC LIMIT 100)
                     UNION ALL
                     (SELECT 'finding', j.app_id, f.created_at, 'agent',
                             CASE WHEN f.confirmed THEN 'confirmed' ELSE 'suspected' END, '', false, 'f' || f.id::text
                        FROM findings f JOIN jobs j ON j.id=f.job_id
                        WHERE j.environment=%(env)s AND f.created_at >= now() - interval '7 days'
                        ORDER BY f.created_at DESC,f.id DESC LIMIT 100)
                     UNION ALL
                     (SELECT 'job', j.app_id, j.updated_at, i.source, j.state, '',
                             coalesce(v.success, false), 'j' || j.id::text
                        FROM jobs j JOIN incidents i ON i.id=j.incident_id LEFT JOIN LATERAL
                          (SELECT success FROM verifications WHERE job_id = j.id
                           ORDER BY recorded_at DESC, id DESC LIMIT 1) v ON true
                        WHERE j.environment = %(env)s AND j.state IN ('resolved','failed','needs_attention')
                          AND j.updated_at >= now() - interval '7 days'
                        ORDER BY j.updated_at DESC, j.id DESC LIMIT 100)
                     ) entries ORDER BY at DESC, sort_key DESC LIMIT 100""", {"env": environment},
            ).fetchall()
            stats = conn.execute(
                """WITH stats AS (
                     SELECT j.app_id,
                       count(*) FILTER (WHERE j.state IN ('received','queued','investigating','acting','verifying')) AS active,
                       count(*) FILTER (WHERE j.state IN ('failed','needs_attention') AND j.updated_at >= now() - interval '24 hours') AS attention,
                       count(*) FILTER (WHERE j.state = 'resolved' AND v.success AND j.updated_at >= now() - interval '24 hours') AS fixed
                     FROM jobs j LEFT JOIN LATERAL
                       (SELECT success FROM verifications WHERE job_id=j.id ORDER BY recorded_at DESC,id DESC LIMIT 1) v ON true
                     WHERE j.environment=%(env)s AND (j.state IN ('received','queued','investigating','acting','verifying') OR j.updated_at >= now() - interval '24 hours') GROUP BY j.app_id
                   ), signals AS (
                     SELECT app_id,max(received_at) AS signal_at FROM events
                     WHERE environment=%(env)s AND source='azure-monitor' GROUP BY app_id
                   ), checks AS (
                     SELECT DISTINCT ON (app_id) app_id,success,kind,finished_at,started_at FROM verifications
                     WHERE environment=%(env)s ORDER BY app_id,recorded_at DESC,id DESC
                   ) SELECT coalesce(s.app_id,g.app_id,c.app_id) AS app_id,
                     coalesce(s.active,0) AS active,coalesce(s.attention,0) AS attention,coalesce(s.fixed,0) AS fixed,
                     g.signal_at,c.success,c.kind,coalesce(c.finished_at,c.started_at) AS check_at
                   FROM stats s FULL JOIN signals g ON g.app_id=s.app_id
                     FULL JOIN checks c ON c.app_id=coalesce(s.app_id,g.app_id)""", {"env": environment},
            ).fetchall()
            deployments = conn.execute(
                """SELECT DISTINCT ON (o.app_id) o.app_id,o.status,o.started_at,o.finished_at,o.workflow_run,
                          a.detail->'result' AS result
                     FROM operations o LEFT JOIN LATERAL
                       (SELECT detail FROM actions WHERE operation_id=o.id AND kind='deploy'
                        ORDER BY updated_at DESC,id DESC LIMIT 1) a ON true
                     WHERE o.environment=%s AND o.kind='deploy'
                     ORDER BY o.app_id,o.started_at DESC,o.id DESC""", (environment,),
            ).fetchall()
        per_app = {app: {"activeJobs": 0, "attentionJobs": 0, "verifiedResolved24h": 0,
                         "lastMonitorSignalAt": None, "lastVerification": None} for app in app_ids}
        for row in stats:
            if row["app_id"] not in app_ids:
                continue
            per_app[row["app_id"]] = {
                "activeJobs": row["active"], "attentionJobs": row["attention"], "verifiedResolved24h": row["fixed"],
                "lastMonitorSignalAt": iso(row["signal_at"]),
                "lastVerification": {"success": row["success"], "kind": row["kind"], "at": iso(row["check_at"])} if row["check_at"] else None,
            }
        releases = {}
        for row in deployments:
            if row["app_id"] not in app_ids:
                continue
            result = row["result"] if isinstance(row["result"], dict) else {}
            release = result.get("release") if isinstance(result.get("release"), dict) else {}
            verification = result.get("verification") if isinstance(result.get("verification"), dict) else {}
            releases[row["app_id"]] = {
                "status": row["status"], "startedAt": iso(row["started_at"]), "finishedAt": iso(row["finished_at"]),
                "verified": result.get("status") == "succeeded" and verification.get("success") is True and verification.get("releaseMatchesDesired") is True,
                "chartVersion": safe_value(release.get("chartVersion"), VERSION),
                "gitSha": safe_value(release.get("gitSha"), SHA),
                "imageDigest": safe_value(release.get("imageDigest"), DIGEST),
                "runUrl": safe_value(row["workflow_run"], RUN_URL),
            }
        return {**empty, "available": True, "activity": [activity_item(row, app_ids) for row in rows],
                "perApp": per_app, "deployments": releases,
                "verifiedResolved24h": sum(s["verifiedResolved24h"] for s in per_app.values())}
    except Exception:
        return empty
