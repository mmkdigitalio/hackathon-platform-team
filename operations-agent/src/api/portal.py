"""Chat-Endpunkte mit Zugangscode und auf /portal/ begrenztem CORS."""

import logging
import re
import uuid

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from starlette.middleware.cors import CORSMiddleware

from persistence import portal

from .catalog import ENVIRONMENTS, Catalog
from .normalize import parse_json
from .settings import Settings

log = logging.getLogger("ops.api.portal")
_DOMAIN = re.compile(r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9-]{2,63}$")


class PortalCORS:
    """Andere Endpunkte erhalten weder Preflight-Antworten noch CORS-Header."""

    def __init__(self, app, allowed_origins: list[str]):
        self.app = app
        self.cors = CORSMiddleware(app, allow_origins=allowed_origins, allow_methods=["GET", "POST", "OPTIONS"],
                                   allow_headers=["X-Portal-Code", "Content-Type"], allow_credentials=False)

    async def __call__(self, scope, receive, send):
        handler = self.cors if scope["type"] == "http" and scope["path"].startswith("/portal/") else self.app
        await handler(scope, receive, send)


def allowed_origins(settings: Settings) -> list[str]:
    if settings.portal_allowed_origins is not None:
        return list(settings.portal_allowed_origins)
    if not _DOMAIN.fullmatch(settings.base_domain) or settings.public_scheme not in ("http", "https"):
        return []
    return [f"{settings.public_scheme}://{host}.{settings.base_domain}"
            for host in ("platform-status", "operations-agent")]


def install_portal(app: FastAPI, settings: Settings, pool) -> None:
    catalog = Catalog(settings.platform_root)
    app.add_middleware(PortalCORS, allowed_origins=allowed_origins(settings))

    def handle(code: str, action: str, raw: bytes, conversation_id: str | None) -> tuple[int, dict]:
        # Authentifizierung, Limit und Annahme nutzen dieselbe Verbindung und Transaktion.
        with pool.connection() as conn, conn.transaction():
            code_id = portal.verify_code(conn, code)
            if code_id is None:
                return 401, {"status": "unauthorized"}
            if action == "session":
                label = conn.execute("SELECT label FROM portal_access_codes WHERE id = %s", (code_id,)).fetchone()["label"]
                return 200, {"ok": True, "label": label}
            if conversation_id is not None:
                try:
                    conversation_id = str(uuid.UUID(conversation_id))
                except ValueError:
                    return 404, {"status": "not_found"}
            if action == "read":
                result = portal.read_conversation(conn, conversation_id)
                return (200, result) if result else (404, {"status": "not_found"})
            body = parse_json(raw)
            fields = {"reporter", "text", "clientMessageId"}
            if action == "create":
                fields |= {"appId", "environment"}
            if set(body) - fields:
                raise ValueError("Unbekannte Felder im Body")
            app_id, environment = None, None
            if action == "create":
                app_id = body.get("appId")
                if not isinstance(app_id, str):
                    raise ValueError("appId muss eine Zeichenkette sein")
                environment = body.get("environment")
                if environment is None:
                    configured = [env for env in ENVIRONMENTS if catalog.problem(app_id, env) is None]
                    environment = configured[0] if len(configured) == 1 else "staging"
                if not isinstance(environment, str):
                    raise ValueError("environment muss eine Zeichenkette sein")
                if problem := catalog.problem(app_id, environment):
                    raise ValueError(problem)
            result = portal.store_message(
                conn, code_id=code_id, conversation_id=conversation_id, app_id=app_id, environment=environment,
                reporter=body.get("reporter"), text=body.get("text"), client_message_id=body.get("clientMessageId"),
            )
            return (202, result) if result else (404, {"status": "not_found"})

    async def respond(request: Request, action: str, conversation_id: str | None = None):
        code = request.headers.get("x-portal-code", "")
        if not code:
            return JSONResponse({"status": "unauthorized"}, status_code=401)
        raw = await request.body() if request.method == "POST" else b""
        try:
            status, body = await run_in_threadpool(handle, code, action, raw, conversation_id)
        except portal.RateLimited:
            status, body = 429, {"status": "rate_limited"}
        except ValueError as exc:
            status, body = 400, {"status": "invalid", "reason": str(exc)}
        except Exception as exc:
            log.error("Portal-Anfrage nicht verarbeitet (%s)", exc.__class__.__name__)
            status, body = 503, {"status": "rejected", "reason": "Datenbank nicht verfügbar"}
        return JSONResponse(body, status_code=status, headers={"Cache-Control": "no-store"})

    @app.get("/portal/session")
    async def session(request: Request):
        return await respond(request, "session")

    @app.post("/portal/conversations")
    async def create_conversation(request: Request):
        return await respond(request, "create")

    @app.post("/portal/conversations/{conversation_id}/messages")
    async def append_message(conversation_id: str, request: Request):
        return await respond(request, "append", conversation_id)

    @app.get("/portal/conversations/{conversation_id}")
    async def read_conversation(conversation_id: str, request: Request):
        return await respond(request, "read", conversation_id)
