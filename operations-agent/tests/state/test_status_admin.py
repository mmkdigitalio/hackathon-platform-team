"""Admin projection: privacy, source attribution, verification, bounds and routes."""
import json
import runpy
import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from api.settings import Settings
from api.status import install_status
from api.status_activity import admin_projection

PRIVATE = "never-publish-private-value"
APPS = {"affine", "operations-agent"}


@pytest.fixture
def pool(db_url):
    with ConnectionPool(db_url, kwargs={"autocommit": True, "row_factory": dict_row}) as p:
        yield p


def job(conn):
    incident = conn.execute("INSERT INTO incidents(source,incident_key) VALUES ('maintenance',%s) RETURNING id", (str(uuid.uuid4()),)).fetchone()["id"]
    return conn.execute("INSERT INTO jobs(incident_id,app_id,environment,state,state_reason) VALUES (%s,'affine','staging','resolved',%s) RETURNING id", (incident, PRIVATE)).fetchone()["id"]


def verification(conn, job_id, success, age="0 seconds"):
    conn.execute("""INSERT INTO verifications(job_id,kind,app_id,environment,success,release,started_at,recorded_at,evidence)
                    VALUES (%s,'smoke-test','affine','staging',%s,'{}',now(),now() - %s::interval,%s)""", (job_id, success, age, Jsonb({"private": PRIVATE})))


def event(conn, env="staging", age="0 seconds", app="affine"):
    incident = conn.execute("INSERT INTO incidents(source,incident_key) VALUES ('azure-monitor',%s) RETURNING id", (str(uuid.uuid4()),)).fetchone()["id"]
    conn.execute("""INSERT INTO events(schema_version,source,delivery_id,external_ref,app_id,environment,event_type,severity,state,received_at,source_ref,payload,incident_id)
                    VALUES ('0.1','azure-monitor',%s,%s,%s,%s,%s,'error','fired',now() - %s::interval,%s,%s,%s)""",
                 (str(uuid.uuid4()), PRIVATE, app, env, PRIVATE, age, Jsonb({"private": PRIVATE}), Jsonb({"private": PRIVATE}), incident))


def action(conn, op_kind="agent", result=None, run_url=None):
    op = "op-" + uuid.uuid4().hex
    conn.execute("""INSERT INTO operations(id,app_id,environment,kind,owner_ref,status,workflow_run,finished_at)
                    VALUES (%s,'affine','staging',%s,%s,'completed',%s,now())""", (op, op_kind, PRIVATE, run_url))
    conn.execute("""INSERT INTO actions(id,operation_id,app_id,environment,kind,target,mutating,status,detail,status_reason)
                    VALUES (%s,%s,'affine','staging',%s,%s,true,'succeeded',%s,%s)""",
                 (uuid.uuid4().hex, op, "deploy" if op_kind == "deploy" else "restart", PRIVATE, Jsonb({"result": result, "private": PRIVATE}), PRIVATE))


def test_projection_distinguishes_sources_and_excludes_private_fields(pool):
    with pool.connection() as conn:
        event(conn)
        resolved = job(conn)
        verification(conn, resolved, True)
        conn.execute("INSERT INTO findings(job_id,confirmed,statement) VALUES (%s,true,%s)", (resolved, PRIVATE))
        action(conn)
        action(conn, "deploy")
    data = admin_projection(pool, "staging", APPS)
    assert data["available"] is True
    assert {row["category"] for row in data["activity"]} == {"detected", "resolved", "agent", "system"}
    assert data["verifiedResolved24h"] == 1
    assert any(row["title"] == "Befund bestätigt" for row in data["activity"])
    assert data["perApp"]["affine"]["lastMonitorSignalAt"]
    assert PRIVATE not in json.dumps(data)
    assert all(set(item) == {"appId", "at", "verified", "category", "result", "source", "title"} for item in data["activity"])


def test_only_latest_successful_verification_counts_as_fixed(pool):
    with pool.connection() as conn:
        job(conn)
        first = job(conn)
        verification(conn, first, True, "2 minutes")
        verification(conn, first, False, "1 minute")
        second = job(conn)
        verification(conn, second, False, "2 minutes")
        verification(conn, second, True, "1 minute")
    data = admin_projection(pool, "staging", APPS)
    assert data["verifiedResolved24h"] == 1
    assert len([a for a in data["activity"] if a["category"] == "resolved"]) == 1
    assert len([a for a in data["activity"] if a["title"] == "Auftrag abgeschlossen"]) == 2


def test_history_is_bounded_scoped_and_ordered(pool):
    with pool.connection() as conn:
        event(conn, env="production", app="operations-agent")
        event(conn, age="8 days")
        for minute in range(105):
            event(conn, age=f"{minute} minutes")
    data = admin_projection(pool, "staging", APPS)
    assert len(data["activity"]) == 100
    assert all(item["appId"] == "affine" for item in data["activity"])
    assert data["activityLimit"] == 100 and data["windowDays"] == 7
    times = [row["at"] for row in data["activity"]]
    assert times == sorted(times, reverse=True)


def test_unknown_app_values_are_not_published(pool):
    with pool.connection() as conn:
        event(conn, app=PRIVATE)
    data = admin_projection(pool, "staging", APPS)
    assert data["activity"][0]["appId"] is None
    assert PRIVATE not in json.dumps(data)


def test_release_verification_and_links_are_allowlisted(pool):
    result = {"status": "succeeded", "release": {"gitSha": "a" * 40, "imageDigest": "sha256:" + "b" * 64, "chartVersion": "0.1.22"},
              "verification": {"success": True, "releaseMatchesDesired": True}, "private": PRIVATE}
    with pool.connection() as conn:
        action(conn, "deploy", result, "https://github.com/mmkdigitalio/hackathon-platform-team/actions/runs/123")
    data = admin_projection(pool, "staging", APPS)
    release = data["deployments"]["affine"]
    assert release["verified"] is True and release["chartVersion"] == "0.1.22" and release["runUrl"].endswith("/123")
    assert PRIVATE not in json.dumps(data)
    with pool.connection() as conn:
        conn.execute("UPDATE operations SET workflow_run=%s", ("https://example.com/?token=" + PRIVATE,))
        conn.execute("UPDATE actions SET detail=%s", (Jsonb({"result": {**result, "verification": {"success": True, "releaseMatchesDesired": False}}}),))
    release = admin_projection(pool, "staging", APPS)["deployments"]["affine"]
    assert release["verified"] is False and release["runUrl"] is None


def test_unavailable_history_is_not_empty_success():
    class BrokenPool:
        def connection(self, **kwargs):
            raise RuntimeError(PRIVATE)
    data = admin_projection(BrokenPool(), "staging", APPS)
    assert data["available"] is False and data["verifiedResolved24h"] is None
    assert PRIVATE not in json.dumps(data)


def test_analysis_routes_direct_links_and_unknown_app(platform_root):
    app = FastAPI()
    install_status(app, Settings(platform_root=platform_root), None, base_domain="")
    with TestClient(app) as client:
        for path in ["/", "/applications?q=affine&state=healthy", "/applications/affine", "/activity?app=affine&category=resolved"]:
            assert client.get(path).status_code == 200
        missing = client.get("/applications/not-catalogued")
        assert missing.status_code == 404 and "Anwendung nicht gefunden" in missing.text
        assert client.post("/applications/affine").status_code == 405


def test_standalone_read_only_and_no_documentation(platform_root):
    root = Path(__file__).resolve().parents[3]
    scope = runpy.run_path(str(root / "platform/infra/status-ui/files/serve.py"), run_name="test_status_bootstrap")
    client = TestClient(scope["app"])
    try:
        for path in ["/", "/status", "/healthz", "/applications", "/applications/affine", "/activity"]:
            assert client.get(path).status_code == 200
            for method in ["POST", "PUT", "PATCH", "DELETE"]:
                assert client.request(method, path).status_code == 405
        for path in ["/docs", "/redoc", "/openapi.json", "/runs/example", "/events/maintenance"]:
            assert client.get(path).status_code == 404
    finally:
        scope["pool"].close()


def test_events_outside_seven_day_window_are_not_shown(pool):
    with pool.connection() as conn:
        event(conn, age="8 days")
    assert admin_projection(pool, "staging", APPS)["activity"] == []


@pytest.mark.parametrize("source,title", [("azure-monitor", "Monitoring-Störung verifiziert behoben"), ("github", "Support-Fall verifiziert abgeschlossen"), ("maintenance", "Auftrag verifiziert abgeschlossen")])
def test_verified_closures_keep_problem_and_task_types_distinct(pool, source, title):
    with pool.connection() as conn:
        verification(conn, job(conn), True)
        conn.execute("UPDATE incidents SET source=%s", (source,))
    rows = admin_projection(pool, "staging", APPS)["activity"]
    assert rows[0]["title"] == title and rows[0]["verified"] is True
