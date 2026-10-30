"""Public status projection: real DB aggregation, failure semantics and no private text."""

import asyncio
import json

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from psycopg_pool import ConnectionPool
from psycopg.rows import dict_row

from api.settings import Settings
from api.status import StatusCollector, database_status, install_status


def test_probe_tls_failure_is_not_reported_healthy_and_requests_are_cached(platform_root):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        if request.url.host.startswith("affine."):
            raise httpx.ConnectError("private exception text", request=request)
        return httpx.Response(200)

    collector = StatusCollector(Settings(platform_root=platform_root), None,
                                base_domain="example.com", transport=httpx.MockTransport(handler))

    async def run():
        first, second = await asyncio.gather(collector.snapshot(), collector.snapshot())
        assert first == second
        return first

    data = asyncio.run(run())
    assert len(calls) == 2
    assert data["status"] == "degraded"
    assert data["services"][0]["status"] == "unavailable"
    assert data["operations"]["database"] == "unknown"
    assert "private exception text" not in json.dumps(data)


def test_missing_configuration_is_unknown_and_does_not_call_network(platform_root):
    def unexpected(request):
        raise AssertionError("no configured target should be requested")

    collector = StatusCollector(Settings(platform_root=platform_root), None,
                                base_domain="", transport=httpx.MockTransport(unexpected))
    data = asyncio.run(collector.snapshot())
    assert data["status"] == "unknown"
    assert all(s["status"] == "unknown" for s in data["services"])
    assert data["operations"]["activeJobs"] is None


def test_redirect_is_not_a_success(platform_root):
    collector = StatusCollector(Settings(platform_root=platform_root), None, base_domain="example.com",
                                transport=httpx.MockTransport(lambda r: httpx.Response(302, headers={"Location": "http://127.0.0.1/private"})))
    data = asyncio.run(collector.snapshot())
    assert all(s["status"] == "unavailable" and s["httpStatus"] == 302 for s in data["services"])


def test_catalog_failure_cannot_be_all_healthy(tmp_path):
    collector = StatusCollector(Settings(platform_root=tmp_path), None, base_domain="example.com")
    data = asyncio.run(collector.snapshot())
    assert data["status"] == "unknown"
    assert data["catalogAvailable"] is False


def test_database_projection_is_scoped_and_excludes_private_fields(db_url, platform_root):
    with ConnectionPool(db_url, kwargs={"autocommit": True, "row_factory": dict_row}) as pool:
        with pool.connection() as conn:
            for app, env, state in [("affine", "staging", "acting"), ("affine", "staging", "resolved"),
                                    ("affine", "staging", "needs_attention"), ("other", "production", "acting")]:
                incident = conn.execute(
                    "INSERT INTO incidents (source, incident_key) VALUES ('maintenance', %s) RETURNING id",
                    (f"private incident {env} {state}",),
                ).fetchone()["id"]
                conn.execute(
                    "INSERT INTO jobs (incident_id, app_id, environment, state, state_reason) VALUES (%s,%s,%s,%s,'secret reason')",
                    (incident, app, env, state),
                )
            conn.execute("INSERT INTO worker_heartbeats (worker_id, beat_at) VALUES ('private-worker', now())")
        data = database_status(pool, "staging", 600)
        assert data["database"] == data["worker"] == "healthy"
        assert (data["activeJobs"], data["resolved24h"], data["attentionJobs"]) == (1, 1, 1)
        assert len(data["recentJobs"]) == 3
        assert all(set(job) == {"appId", "state", "updatedAt"} for job in data["recentJobs"])
        assert "private" not in json.dumps(data) and "secret" not in json.dumps(data)
        collector = StatusCollector(Settings(platform_root=platform_root), pool, base_domain="example.com",
                                    transport=httpx.MockTransport(lambda r: httpx.Response(200)))
        assert asyncio.run(collector.snapshot())["status"] == "healthy"
        with pool.connection() as conn:
            conn.execute("UPDATE worker_heartbeats SET beat_at = now() - interval '11 minutes'")
        assert database_status(pool, "staging", 600)["worker"] == "unavailable"


def test_database_outage_does_not_become_zero_jobs():
    class BrokenPool:
        def connection(self, **kwargs):
            raise RuntimeError("postgresql://secret:password@private/db")

    data = database_status(BrokenPool(), "staging", 600)
    assert data["database"] == "unavailable"
    assert data["worker"] == "unknown"
    assert data["activeJobs"] is None
    assert "password" not in json.dumps(data)


def test_dashboard_and_assets_are_served_and_status_is_not_cached(platform_root):
    app = FastAPI()
    install_status(app, Settings(platform_root=platform_root), None, base_domain="")
    with TestClient(app) as client:
        page = client.get("/")
        assert page.status_code == 200
        assert "Plattformstatus" in page.text
        assert client.get("/status-assets/status.js").status_code == 200
        assert client.get("/status-assets/fonts/bricolage-latin.woff2").status_code == 200
        status = client.get("/status")
        assert status.status_code == 200
        assert status.headers["cache-control"] == "no-store"
        assert status.json()["status"] == "unknown"
