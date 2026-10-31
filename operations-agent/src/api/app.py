"""FastAPI-Ereigniseingang, Gesundheitsendpunkt und Laufnachweise."""

import logging
import time
import uuid
from contextlib import asynccontextmanager

import psycopg
from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from persistence import intake, runs
from persistence.db import conninfo

from . import normalize
from .catalog import Catalog
from .normalize import Ignored, InvalidPayload
from .portal import install_portal
from .security import github_signature_valid, key_matches, mask_query
from .settings import Settings
from .status import install_status

log = logging.getLogger("ops.api")


def make_pool(settings: Settings) -> ConnectionPool:
    return ConnectionPool(
        conninfo(settings.database_url),
        min_size=1,
        max_size=10,
        open=False,
        kwargs={"autocommit": True, "row_factory": dict_row},
        timeout=5,
    )


def create_app(settings: Settings | None = None, pool: ConnectionPool | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    pool = pool or make_pool(settings)
    catalog = Catalog(settings.platform_root)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Nicht auf die Datenbank warten: /healthz soll auch ohne sie 503 liefern können.
        pool.open(wait=False)
        try:
            yield
        finally:
            pool.close()

    app = FastAPI(title="Operations-Agent-API", version="0.1", lifespan=lifespan)
    app.state.pool = pool
    app.state.settings = settings
    install_status(app, settings, pool)
    install_portal(app, settings, pool)

    @app.middleware("http")
    async def request_log(request: Request, call_next):
        start = time.monotonic()
        response = await call_next(request)
        query = mask_query(request.url.query)
        log.info(
            "%s %s%s -> %s (%.0f ms)",
            request.method,
            request.url.path,
            f"?{query}" if query else "",
            response.status_code,
            (time.monotonic() - start) * 1000,
        )
        return response

    def unauthorized() -> JSONResponse:
        return JSONResponse({"status": "unauthorized"}, status_code=401)

    def accept(event: dict, payload: dict) -> dict:
        with pool.connection() as conn:
            return intake.store_event(conn, event, payload).as_response()

    async def persist(event: dict | Ignored, payload: dict) -> JSONResponse:
        if isinstance(event, Ignored):
            log.info("Ereignis ignoriert: %s", event.reason)
            return JSONResponse({"status": "ignored", "reason": event.reason}, status_code=202)
        try:
            result = await run_in_threadpool(accept, event, payload)
        except Exception as exc:  # Datenbank- oder Pool-Fehler: keine Annahme vortäuschen
            log.error("Ereignis nicht gespeichert (%s): %s", event["source"], exc.__class__.__name__)
            return JSONResponse(
                {"status": "rejected", "reason": "Ereignis konnte nicht gespeichert werden"},
                status_code=503,
            )
        if result["mappingError"]:
            log.warning("Zuordnungsfehler für Ereignis %s: %s", result["eventId"], result["mappingError"])
        return JSONResponse(result, status_code=200 if result["status"] == "duplicate" else 202)

    def bad_request(exc: Exception) -> JSONResponse:
        return JSONResponse({"status": "invalid", "reason": str(exc)}, status_code=400)

    @app.post("/events/azure-monitor")
    async def azure_monitor(request: Request):
        if not key_matches(request.query_params.get("key"), settings.alert_webhook_key):
            return unauthorized()
        raw = await request.body()
        try:
            body = normalize.parse_json(raw)
            event = normalize.azure_monitor(body, raw, catalog)
        except InvalidPayload as exc:
            return bad_request(exc)
        return await persist(event, body)

    @app.post("/events/github")
    async def github(request: Request):
        raw = await request.body()
        if not github_signature_valid(
            raw, request.headers.get("x-hub-signature-256"), settings.github_webhook_secret
        ):
            return unauthorized()
        try:
            body = normalize.parse_json(raw)
            event = normalize.github(
                request.headers.get("x-github-event", ""),
                request.headers.get("x-github-delivery", ""),
                body,
                raw,
                catalog,
                set(settings.repo_allowlist),
            )
        except InvalidPayload as exc:
            return bad_request(exc)
        except PermissionError as exc:
            log.warning("GitHub-Ereignis abgelehnt: %s", exc)
            return JSONResponse({"status": "forbidden", "reason": str(exc)}, status_code=403)
        return await persist(event, body)

    @app.post("/events/maintenance")
    async def maintenance(request: Request):
        if not key_matches(request.headers.get("x-api-key"), settings.maintenance_api_key):
            return unauthorized()
        raw = await request.body()
        try:
            body = normalize.parse_json(raw)
            event = normalize.maintenance(body, raw, catalog)
        except InvalidPayload as exc:
            return bad_request(exc)
        return await persist(event, body)

    def read_health() -> tuple[int, dict]:
        try:
            with pool.connection(timeout=3) as conn:
                row = conn.execute(
                    """
                    SELECT extract(epoch FROM now() - max(beat_at)) AS age, count(*) AS workers
                      FROM worker_heartbeats
                    """
                ).fetchone()
        except Exception as exc:
            log.error("healthz: Datenbank nicht erreichbar (%s)", exc.__class__.__name__)
            return 503, {"status": "unhealthy", "database": "unreachable"}
        age = None if row["age"] is None else float(row["age"])
        body = {"status": "ok", "database": "ok", "lastHeartbeatAgeSeconds": age}
        if age is None:
            return 503, {**body, "status": "unhealthy", "worker": "kein Heartbeat"}
        if age > settings.healthz_max_heartbeat_age_seconds:
            return 503, {**body, "status": "unhealthy", "worker": "Heartbeat zu alt"}
        return 200, {**body, "worker": "ok"}

    @app.get("/healthz")
    async def healthz():
        code, body = await run_in_threadpool(read_health)
        return JSONResponse(body, status_code=code)

    @app.get("/runs/{job_id}")
    async def run_evidence(job_id: str, request: Request):
        if not key_matches(request.headers.get("x-api-key"), settings.maintenance_api_key):
            return unauthorized()
        try:
            uuid.UUID(job_id)
        except ValueError:
            return JSONResponse({"status": "not_found"}, status_code=404)

        def load():
            with pool.connection() as conn:
                return runs.get_run(conn, job_id, secrets=settings.secrets)

        try:
            run = await run_in_threadpool(load)
        except psycopg.Error:
            return JSONResponse({"status": "unavailable"}, status_code=503)
        if run is None:
            return JSONResponse({"status": "not_found"}, status_code=404)
        return JSONResponse(run)

    return app
