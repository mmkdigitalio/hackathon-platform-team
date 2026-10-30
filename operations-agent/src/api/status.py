"""Read-only, deliberately small public platform status projection.

No incident text, identifiers, credentials, logs or model output leave this endpoint.
Checks use configured catalog targets only, never a target supplied by a request.
"""

import asyncio
import hashlib
import os
import re
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
import yaml
from fastapi import FastAPI
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .catalog import APP_ID, ENVIRONMENTS, Catalog
from .settings import Settings
from .status_activity import admin_projection

STATIC = Path(__file__).with_name("static")
ACTIVE_STATES = ("received", "queued", "investigating", "acting", "verifying")
DOMAIN = re.compile(r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9-]{2,63}$")


def database_status(pool, environment: str, max_age: int) -> dict:
    unknown = {"database": "unknown", "worker": "unknown", "heartbeatAgeSeconds": None,
               "activeJobs": None, "attentionJobs": None, "resolved24h": None, "recentJobs": []}
    if pool is None:
        return unknown
    try:
        with pool.connection(timeout=3) as conn, conn.transaction():
            conn.execute("SET TRANSACTION READ ONLY")
            conn.execute("SET LOCAL statement_timeout = '2500ms'")
            heartbeat = conn.execute(
                "SELECT extract(epoch FROM now() - max(beat_at)) AS age FROM worker_heartbeats"
            ).fetchone()
            counts = conn.execute(
                """SELECT count(*) FILTER (WHERE state = ANY(%s)) AS active,
                          count(*) FILTER (WHERE state IN ('failed', 'needs_attention')
                            AND updated_at >= now() - interval '24 hours') AS attention,
                          count(*) FILTER (WHERE state = 'resolved'
                            AND updated_at >= now() - interval '24 hours') AS resolved
                     FROM jobs WHERE environment = %s""", (list(ACTIVE_STATES), environment),
            ).fetchone()
            recent = conn.execute(
                """SELECT app_id, state, updated_at FROM jobs
                     WHERE environment = %s ORDER BY updated_at DESC, id DESC LIMIT 6""", (environment,),
            ).fetchall()
        age = None if heartbeat["age"] is None else max(0, float(heartbeat["age"]))
        return {
            "database": "healthy", "worker": "healthy" if age is not None and age <= max_age else "unavailable",
            "heartbeatAgeSeconds": age, "activeJobs": counts["active"],
            "attentionJobs": counts["attention"], "resolved24h": counts["resolved"],
            "recentJobs": [{"appId": row["app_id"], "state": row["state"],
                            "updatedAt": row["updated_at"].isoformat()} for row in recent],
        }
    except Exception:
        # Operational exceptions can contain DSNs and SQL. Expose only the failed component.
        return {**unknown, "database": "unavailable"}


class StatusCollector:
    def __init__(self, settings: Settings, pool, *, base_domain: str | None = None,
                 scheme: str | None = None, environment: str | None = None, transport=None):
        self.settings, self.pool = settings, pool
        self.environment = environment or os.environ.get("OPS_STATUS_ENVIRONMENT", "staging")
        if self.environment not in ENVIRONMENTS:
            raise ValueError("OPS_STATUS_ENVIRONMENT must be staging or production")
        self.domain = (base_domain if base_domain is not None else os.environ.get("OPS_BASE_DOMAIN", "")).lower()
        self.scheme = scheme or os.environ.get("OPS_PUBLIC_SCHEME", os.environ.get("OPS_HTTP_SCHEME", "https"))
        self.transport = transport
        self._cached = None
        self._expires = 0.0
        self._lock = asyncio.Lock()

    def targets(self) -> tuple[list[dict], bool]:
        apps = []
        root = self.settings.platform_root
        complete = root is not None and (root / "catalog").is_dir() and (root / "environments" / self.environment).is_dir()
        for app_id in Catalog(root).app_ids():
            if not APP_ID.fullmatch(app_id) or Catalog(root).problem(app_id, self.environment):
                continue
            try:
                catalog = yaml.safe_load((root / "catalog" / app_id / "app.yaml").read_text())
                desired = yaml.safe_load((root / "environments" / self.environment / f"{app_id}.yaml").read_text())
                path = catalog.get("availability", {}).get("path", "/")
                if not isinstance(path, str) or not path.startswith("/") or path.startswith("//") or "?" in path or "#" in path:
                    raise ValueError("invalid availability path")
                version = desired.get("upstreamRelease") or desired.get("chart", {}).get("version")
                configured = bool(DOMAIN.fullmatch(self.domain)) and self.scheme in ("http", "https")
                url = f"{self.scheme}://{app_id}.{self.domain}" if configured else None
                apps.append({"id": app_id, "name": "AFFiNE" if app_id == "affine" else
                             "Operations-Agent" if app_id == "operations-agent" else "Status-UI" if app_id == "platform-status" else app_id,
                             "version": str(version) if version else None, "url": url, "checkPath": path,
                             "description": str(catalog.get("description", ""))[:240],
                             "monitoring": {"external": {"source": "Foundation-IaC", "activation": "not-verified",
                                 "intervalSeconds": 300, "locations": 5, "failedLocations": 2,
                                 "notificationTarget": "ops-humans" if app_id == "operations-agent" else "ops-intake"},
                                 "smokeConfigured": bool(catalog.get("smokeTest")), "tls": self.scheme == "https"}})
            except (OSError, ValueError, TypeError, AttributeError, yaml.YAMLError):
                complete = False
        return apps, bool(complete and apps)

    async def probe(self, client: httpx.AsyncClient, app: dict) -> dict:
        result = {**app, "status": "unknown", "latencyMs": None, "httpStatus": None, "detail": "Kein Prüfziel konfiguriert"}
        if not app["url"]:
            return result
        start = time.monotonic()
        try:
            # Do not follow redirects: a redirect does not prove that the health endpoint works.
            async with client.stream("GET", app["url"] + app["checkPath"]) as response:
                result.update(status="healthy" if response.status_code == 200 else "unavailable",
                              latencyMs=round((time.monotonic() - start) * 1000), httpStatus=response.status_code,
                              detail="HTTP 200" if response.status_code == 200 else f"HTTP {response.status_code}")
        except httpx.TimeoutException:
            result.update(status="unavailable", detail="Zeitüberschreitung")
        except httpx.HTTPError:
            result.update(status="unavailable", detail="Verbindung oder TLS fehlgeschlagen")
        return result

    async def snapshot(self) -> dict:
        async with self._lock:
            if self._cached is not None and time.monotonic() < self._expires:
                return self._cached
            apps, catalog_ok = await run_in_threadpool(self.targets)
            async with httpx.AsyncClient(timeout=4, follow_redirects=False, transport=self.transport, trust_env=False) as client:
                results, operations, admin = await asyncio.gather(
                    asyncio.gather(*(self.probe(client, app) for app in apps)),
                    run_in_threadpool(database_status, self.pool, self.environment,
                                      self.settings.healthz_max_heartbeat_age_seconds),
                    run_in_threadpool(admin_projection, self.pool, self.environment, {a["id"] for a in apps}),
                )
            for job in operations["recentJobs"]:
                if job["appId"] not in {a["id"] for a in apps}:
                    job["appId"] = None
            states = [a["status"] for a in results] + [operations["database"], operations["worker"]]
            overall = "degraded" if "unavailable" in states else "unknown" if "unknown" in states or not catalog_ok else "healthy"
            self._cached = {
                "checkedAt": datetime.now(UTC).isoformat(), "refreshAfterSeconds": 30,
                "environment": self.environment, "status": overall, "catalogAvailable": catalog_ok,
                "services": list(results), "operations": operations, "admin": admin,
            }
            self._expires = time.monotonic() + 10
            return self._cached


def install_status(app: FastAPI, settings: Settings, pool, **collector_options) -> StatusCollector:
    collector = StatusCollector(settings, pool, **collector_options)
    app.mount("/status-assets", StaticFiles(directory=STATIC), name="status-assets")

    def page_html(filename):
        # A changed asset must get a new URL even in an already-open browser.
        def version(match):
            digest = hashlib.sha256((STATIC / match[2]).read_bytes()).hexdigest()[:16]
            return f"{match[1]}?v={digest}"

        return re.sub(r"(/status-assets/([\w.-]+\.(?:css|js)))", version,
                      (STATIC / filename).read_text(encoding="utf-8"))

    index_html = page_html("index.html")
    missing_html = page_html("not-found.html")

    @app.get("/", include_in_schema=False)
    async def status_page():
        return HTMLResponse(index_html, headers={"Cache-Control": "no-cache", "X-Robots-Tag": "noindex"})

    @app.get("/applications", include_in_schema=False)
    @app.get("/activity", include_in_schema=False)
    async def analysis_page():
        return await status_page()

    @app.get("/applications/{app_id}", include_in_schema=False)
    async def app_page(app_id: str):
        if not APP_ID.fullmatch(app_id) or Catalog(settings.platform_root).problem(app_id, collector.environment):
            return HTMLResponse(missing_html, status_code=404, headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex"})
        return await status_page()

    @app.get("/status", tags=["status"])
    async def platform_status():
        return JSONResponse(await collector.snapshot(), headers={"Cache-Control": "no-store"})

    return collector
