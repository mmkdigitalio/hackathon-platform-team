"""Mitarbeiter-Chat: Zugang, atomare Annahme, Wiederholung, Grenzen und Projektion."""

import hashlib
import json
import logging
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.security import mask_headers
from api.settings import Settings
from persistence import cli, intake, portal
from persistence.db import check_schema, connect
from persistence.event_schema import errors_for
from persistence.migrate import expected_version


@pytest.fixture
def chat(db_url, platform_root):
    with connect(db_url) as conn:
        code_id, code = portal.create_code(conn, "Team", 30)
    with TestClient(create_app(Settings(database_url=db_url, platform_root=platform_root,
                                        base_domain="example.org"))) as client:
        client.headers["X-Portal-Code"] = code
        yield client, code_id, code


def send(client, client_id="m-1", **values):
    return client.post("/portal/conversations", json={
        "appId": "affine", "reporter": "Anna", "text": "Upload geht nicht", "clientMessageId": client_id, **values,
    })


def follow(client, conversation_id, client_id="m-2", **values):
    return client.post(f"/portal/conversations/{conversation_id}/messages", json={
        "reporter": "Ben", "text": "Geht wieder nicht", "clientMessageId": client_id, **values,
    })


def test_code_lifecycle(db_url):
    with connect(db_url) as conn:
        code_id, code = portal.create_code(conn, "Support", 1)
        assert len(code) == 24
        assert portal.verify_code(conn, code) == code_id
        assert portal.verify_code(conn, "falsch") is None
        row = portal.list_codes(conn)[0]
        assert set(row) == {"id", "label", "created_at", "expires_at", "revoked_at", "last_used_at"}
        assert row["label"] == "Support" and row["last_used_at"] is not None
        assert code not in str(row)
        stored = conn.execute("SELECT code_hash FROM portal_access_codes WHERE id = %s", (code_id,)).fetchone()
        assert stored["code_hash"] == hashlib.sha256(code.encode()).hexdigest()
        conn.execute("UPDATE portal_access_codes SET expires_at = now() - interval '1 second' WHERE id = %s", (code_id,))
        assert portal.verify_code(conn, code) is None
        conn.execute("UPDATE portal_access_codes SET expires_at = NULL WHERE id = %s", (code_id,))
        assert portal.verify_code(conn, code) == code_id
        assert portal.revoke_code(conn, code_id)
        assert portal.verify_code(conn, code) is None
        assert portal.list_codes(conn)[0]["revoked_at"] is not None
        assert not portal.revoke_code(conn, str(uuid.uuid4()))


def test_code_cli_and_schema_version(db_url, capsys):
    assert cli.main(["portal-code", "create", "--label", "CLI", "--expires-days", "2"]) == 0
    created = json.loads(capsys.readouterr().out)
    assert cli.main(["portal-code", "list"]) == 0
    output = capsys.readouterr().out
    assert created["code"] not in output and "code_hash" not in output
    assert json.loads(output)[0]["id"] == created["id"]
    assert cli.main(["portal-code", "revoke", created["id"]]) == 0
    capsys.readouterr()
    with connect(db_url) as conn:
        assert portal.verify_code(conn, created["code"]) is None
        assert check_schema(conn, include_sdk=True) == expected_version() == 2
    assert cli.main(["portal-code", "create", "--label", "CLI", "--expires-days", "0"]) == 1


def test_session_and_revocation(chat, db_url):
    client, code_id, _ = chat
    response = client.get("/portal/session")
    assert response.status_code == 200 and response.json() == {"ok": True, "label": "Team"}
    with connect(db_url) as conn:
        portal.revoke_code(conn, code_id)
    assert client.get("/portal/session").json() == {"status": "unauthorized"}
    assert client.get("/portal/session").status_code == 401


@pytest.mark.parametrize("method,path", [
    ("GET", "/portal/session"), ("POST", "/portal/conversations"),
    ("GET", "/portal/conversations/unknown"), ("POST", "/portal/conversations/unknown/messages"),
])
@pytest.mark.parametrize("code", [None, "wrong-code"])
def test_all_routes_require_code(chat, method, path, code):
    client, _, _ = chat
    client.headers.pop("X-Portal-Code")
    response = client.request(method, path, headers={"X-Portal-Code": code} if code else {}, content=b"not json")
    assert response.status_code == 401 and response.json() == {"status": "unauthorized"}


def test_create_conversation_and_projection(chat, db_url):
    client, code_id, _ = chat
    response = send(client)
    assert response.status_code == 202
    receipt = response.json()
    uuid.UUID(receipt["conversationId"])
    assert receipt["status"] == "accepted"
    with connect(db_url) as conn:
        incident = conn.execute("SELECT * FROM incidents").fetchone()
        event = conn.execute("SELECT * FROM events").fetchone()
        message = conn.execute("SELECT * FROM conversation_messages").fetchone()
        assert incident["source"] == "portal" and incident["incident_key"] == receipt["conversationId"]
        assert str(event["job_id"]) == receipt["jobId"] == str(message["job_id"])
        assert event["event_type"] == "chat-message" and event["state"] == "reported"
        assert event["payload"] == {"reporter": "Anna", "text": "Upload geht nicht", "conversationId": receipt["conversationId"]}
        assert str(message["access_code_id"]) == code_id
        assert message["body"] == "Upload geht nicht" and message["role"] == "reporter" and message["kind"] is None
    response = client.get(f"/portal/conversations/{receipt['conversationId']}")
    assert response.status_code == 200
    view = response.json()
    assert set(view) == {"conversationId", "appId", "environment", "messages", "jobs"}
    assert (view["appId"], view["environment"]) == ("affine", "staging")
    assert set(view["messages"][0]) == {"id", "role", "author", "kind", "body", "createdAt", "jobId"}
    assert set(view["jobs"][0]) == {"id", "state", "createdAt", "updatedAt", "lastTransitionAt"}
    assert view["jobs"][0]["state"] == "queued" and view["jobs"][0]["lastTransitionAt"]
    for internal in ("lease_owner", "findings", "state_reason", "payload", "code_hash", "access_code_id"):
        assert internal not in response.text


def test_retries_and_follow_up_after_resolution(chat, db_url):
    client, _, _ = chat
    first = send(client).json()
    duplicate = send(client)
    assert duplicate.status_code == 202
    assert duplicate.json() == {**first, "status": "duplicate"}
    second = follow(client, first["conversationId"]).json()
    assert second["jobId"] == first["jobId"]
    assert follow(client, first["conversationId"]).json() == {**second, "status": "duplicate"}
    with connect(db_url) as conn:
        # Abschlusszustand als Fixture; Nachweisprüfung wird in test_worker_and_states geprüft.
        conn.execute("UPDATE jobs SET state = 'resolved' WHERE id = %s", (first["jobId"],))
    third = follow(client, first["conversationId"], "m-3").json()
    assert third["jobId"] != first["jobId"] and third["conversationId"] == first["conversationId"]
    with connect(db_url) as conn:
        for table, expected in (("incidents", 1), ("events", 3), ("jobs", 2), ("conversation_messages", 3)):
            assert conn.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"] == expected


def test_concurrent_retries(chat, db_url):
    client, _, _ = chat
    with ThreadPoolExecutor(max_workers=5) as executor:
        responses = list(executor.map(lambda _: send(client), range(5)))
    assert all(r.status_code == 202 for r in responses)
    assert len({r.json()["conversationId"] for r in responses}) == 1
    assert sum(r.json()["status"] == "accepted" for r in responses) == 1
    with connect(db_url) as conn:
        assert conn.execute("SELECT count(*) AS n FROM events").fetchone()["n"] == 1


def test_delivery_id_cannot_attach_to_another_conversation(chat):
    client, _, _ = chat
    first = send(client).json()
    second = send(client, "other").json()
    assert follow(client, second["conversationId"], "m-1").status_code == 400
    assert client.get(f"/portal/conversations/{first['conversationId']}").json()["messages"][0]["body"] == "Upload geht nicht"


@pytest.mark.parametrize("values", [
    {"appId": "unknown"}, {"appId": []}, {"environment": "elsewhere"}, {"environment": 7},
    {"reporter": ""}, {"reporter": " "}, {"reporter": "x" * 81}, {"text": ""},
    {"text": "x" * 4001}, {"text": []}, {"clientMessageId": "x" * 101},
    {"clientMessageId": "has spaces"}, {"clientMessageId": None},
])
def test_invalid_payload(chat, values):
    assert send(chat[0], **values).status_code == 400


def test_unknown_conversation_and_invalid_json(chat):
    client, _, _ = chat
    for conversation_id in (str(uuid.uuid4()), "invalid"):
        assert client.get(f"/portal/conversations/{conversation_id}").status_code == 404
        assert follow(client, conversation_id).status_code == 404
    assert client.post("/portal/conversations", content=b"[]").status_code == 400
    assert client.post("/portal/conversations", content=b"bad json").status_code == 400


def test_single_configured_environment(chat, platform_root):
    client, _, _ = chat
    staging = platform_root / "environments" / "staging" / "affine.yaml"
    production = platform_root / "environments" / "production" / "affine.yaml"
    production.parent.mkdir()
    staging.rename(production)
    receipt = send(client).json()
    assert client.get(f"/portal/conversations/{receipt['conversationId']}").json()["environment"] == "production"


def test_rate_limit_and_retry_at_limit(chat, db_url):
    client, code_id, _ = chat
    first = send(client).json()
    for number in range(2, 21):
        assert follow(client, first["conversationId"], f"m-{number}").status_code == 202
    assert follow(client, first["conversationId"], "m-21").status_code == 429
    assert send(client).json()["status"] == "duplicate"
    with connect(db_url) as conn:
        conn.execute("UPDATE conversation_messages SET created_at = now() - interval '11 minutes' WHERE access_code_id = %s",
                     (code_id,))
    assert follow(client, first["conversationId"], "m-21").status_code == 202


def test_event_and_message_rollback_together(chat, db_url, monkeypatch):
    original = intake.store_event

    def fail_after_event(conn, event, payload):
        original(conn, event, payload)
        raise RuntimeError("Nachricht konnte nicht gespeichert werden")

    monkeypatch.setattr(intake, "store_event", fail_after_event)
    assert send(chat[0]).status_code == 503
    with connect(db_url) as conn:
        for table in ("incidents", "events", "jobs", "conversation_messages"):
            assert conn.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"] == 0


def test_no_message_or_code_in_logs(chat, caplog):
    client, _, code = chat
    caplog.set_level(logging.INFO)
    send(client, text="Vertrauliche Fehlermeldung")
    assert code not in caplog.text and "Vertrauliche Fehlermeldung" not in caplog.text
    assert mask_headers({"X-Portal-Code": code}) == {"X-Portal-Code": "***"}


def test_portal_preflight_is_scoped(platform_root):
    with TestClient(create_app(Settings(platform_root=platform_root, base_domain="example.org"))) as client:
        headers = {"Origin": "https://platform-status.example.org", "Access-Control-Request-Method": "POST",
                   "Access-Control-Request-Headers": "X-Portal-Code, Content-Type"}
        response = client.options("/portal/conversations", headers=headers)
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == headers["Origin"]
        assert "access-control-allow-credentials" not in response.headers
        headers["Origin"] = "https://foreign.example.org"
        response = client.options("/portal/conversations", headers=headers)
        assert response.status_code == 400 and "access-control-allow-origin" not in response.headers
        headers["Origin"] = "https://platform-status.example.org"
        assert "access-control-allow-origin" not in client.options("/events/maintenance", headers=headers).headers
        response = client.get("/portal/session", headers={"Origin": headers["Origin"]})
        assert response.status_code == 401 and response.headers["access-control-allow-origin"] == headers["Origin"]


def test_cors_override_and_scheme(monkeypatch):
    from api.portal import allowed_origins

    monkeypatch.setenv("OPS_BASE_DOMAIN", "Example.org")
    monkeypatch.setenv("OPS_PUBLIC_SCHEME", "http")
    monkeypatch.delenv("OPS_PORTAL_ALLOWED_ORIGINS", raising=False)
    assert allowed_origins(Settings.from_env()) == ["http://platform-status.example.org", "http://operations-agent.example.org"]
    monkeypatch.setenv("OPS_PORTAL_ALLOWED_ORIGINS", "https://custom.example.org, https://second.example.org")
    assert allowed_origins(Settings.from_env()) == ["https://custom.example.org", "https://second.example.org"]
    monkeypatch.setenv("OPS_PORTAL_ALLOWED_ORIGINS", "")
    assert allowed_origins(Settings.from_env()) == []


def test_actual_portal_event_matches_schema(db_url, platform_root, monkeypatch):
    seen = []
    original = intake.store_event

    def capture(conn, event, payload):
        seen.append(event)
        return original(conn, event, payload)

    monkeypatch.setattr(intake, "store_event", capture)
    with connect(db_url) as conn:
        code_id, _ = portal.create_code(conn, "Schema")
        portal.store_message(conn, code_id=code_id, app_id="affine", environment="staging",
                             reporter="Anna", text="Fehler", client_message_id="schema-1")
    assert errors_for(seen[0]) == []


@pytest.mark.parametrize("method,path", [
    ("GET", "/portal/session"), ("POST", "/portal/conversations"),
    ("GET", "/portal/conversations/76459c02-bc72-4449-808e-fb9c50a7105c"),
    ("POST", "/portal/conversations/76459c02-bc72-4449-808e-fb9c50a7105c/messages"),
])
def test_database_failure_returns_503(platform_root, method, path):
    class UnavailablePool:
        def open(self, **kwargs):
            pass

        def close(self):
            pass

        def connection(self):
            raise RuntimeError("Verbindung abgebrochen")

    with TestClient(create_app(Settings(platform_root=platform_root), UnavailablePool())) as client:
        response = client.request(method, path, headers={"X-Portal-Code": "not-logged"}, json={})
    assert response.status_code == 503 and "jobId" not in response.json()


def test_concurrent_rate_limit_across_conversations(chat, db_url):
    client, _, _ = chat
    with ThreadPoolExecutor(max_workers=5) as executor:
        responses = list(executor.map(lambda number: send(client, f"rate-{number}"), range(25)))
    assert sum(r.status_code == 202 for r in responses) == 20
    assert sum(r.status_code == 429 for r in responses) == 5
    with connect(db_url) as conn:
        assert conn.execute("SELECT count(*) AS n FROM conversation_messages").fetchone()["n"] == 20
    with connect(db_url) as conn:
        _, second_code = portal.create_code(conn, "Anderes Team")
    client.headers["X-Portal-Code"] = second_code
    assert send(client, "another-code").status_code == 202
