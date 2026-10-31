"""Geplanter Gesundheitscheck (``ops health-check``), aufgerufen von health-check.yml.

Prüft deterministisch den Verfügbarkeits-Prüfpfad jeder Katalog-App. Nur bei
Fehlschlag entsteht ein Wartungsauftrag; ein gesunder Lauf ruft kein Modell auf.
Der Betriebsagent selbst ist ausgenommen; ihn überwacht A's Test auf /healthz.
"""

import os
import sys
from datetime import UTC, datetime

import httpx
import yaml

from api.catalog import Catalog, catalog_source, find_platform_root

EXCLUDED_APPS = {"operations-agent"}


def availability_path(app_yaml: dict) -> str:
    path = (app_yaml.get("availability") or {}).get("path")
    if not path or not str(path).startswith("/"):
        raise ValueError("availability.path fehlt im Katalogeintrag")
    return path


def check_app(client: httpx.Client, url: str) -> tuple[bool, str]:
    try:
        response = client.get(url)
    except httpx.HTTPError as exc:
        return False, f"{exc.__class__.__name__}"
    return response.status_code == 200, f"HTTP {response.status_code}"


def run_health_check(args, client: httpx.Client | None = None) -> int:
    root = find_platform_root()
    catalog = Catalog(root)
    # Im Workflow ist das ausgecheckte Repository die Quelle; im Cluster gegebenenfalls der Live-Katalog.
    source = catalog_source(root)
    now = datetime.now(UTC).replace(microsecond=0).isoformat()
    run_id = os.environ.get("GITHUB_RUN_ID", "local")
    attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "1")
    run_url = None
    if os.environ.get("GITHUB_RUN_ID"):
        run_url = (
            f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/"
            f"{os.environ.get('GITHUB_REPOSITORY', '')}/actions/runs/{run_id}"
        )
    key = os.environ.get("MAINTENANCE_API_KEY", "")
    client = client or httpx.Client(timeout=args.timeout, follow_redirects=False)

    rows, failed = [], []
    for app_id in source.app_ids():
        if app_id in EXCLUDED_APPS or catalog.problem(app_id, args.env):
            continue
        app_yaml = yaml.safe_load(source.catalog_path(app_id).read_text(encoding="utf-8")) or {}
        try:
            url = f"{args.scheme}://{app_id}.{args.base_domain}{availability_path(app_yaml)}"
        except ValueError as exc:
            rows.append((app_id, "-", False, str(exc)))
            failed.append((app_id, None, str(exc)))
            continue
        ok, detail = check_app(client, url)
        rows.append((app_id, url, ok, detail))
        if not ok:
            failed.append((app_id, url, detail))

    lines = [
        f"## Gesundheitscheck {args.env} ({now})",
        "",
        "| App | Umgebung | Prüfung | Ergebnis | Zeitpunkt |",
        "| --- | --- | --- | --- | --- |",
        *[f"| {a} | {args.env} | {u} | {'ok' if ok else 'FEHLER'}: {d} | {now} |" for a, u, ok, d in rows],
    ]
    if not rows:
        lines.append(f"| – | {args.env} | keine Katalog-Apps gefunden | – | {now} |")

    exit_code = 0
    for app_id, url, detail in failed:
        if args.dry_run:
            lines.append(f"\nWartungsauftrag für {app_id} nicht gesendet (--dry-run).")
            continue
        body = {
            "deliveryId": f"health-check:{run_id}:{attempt}:{app_id}:{args.env}",
            "externalRef": f"health-check:{app_id}:{args.env}:availability",
            "appId": app_id,
            "environment": args.env,
            "eventType": "health-check-failed",
            "severity": "error",
            "occurredAt": now,
            "details": {"check": "availability", "url": url, "result": detail, "workflowRun": run_url},
        }
        try:
            response = client.post(
                f"{args.scheme}://{args.api_host}/events/maintenance",
                json=body,
                headers={"X-Api-Key": key},
            )
            result = f"HTTP {response.status_code}"
            if response.status_code not in (200, 202):
                exit_code = 1
        except httpx.HTTPError as exc:
            result, exit_code = exc.__class__.__name__, 1
        lines.append(f"\nWartungsauftrag für {app_id} gesendet: {result}")

    summary = "\n".join(lines)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(summary + "\n")
    print(summary)
    if root is None:
        print("ops: platform/ nicht gefunden", file=sys.stderr)
        return 1
    return exit_code
