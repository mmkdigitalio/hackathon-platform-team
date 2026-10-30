"""Tasks 2.1 bis 2.8, 3.4 und 5.5: Ereigniseingang, /healthz und /runs gegen PostgreSQL."""

import copy
import hashlib
import hmac
import json
import logging

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.settings import Settings
from persistence.db import connect

from .conftest import REPO_ROOT

PAYLOADS = REPO_ROOT / "platform" / "schemas" / "events" / "examples" / "payloads"
ALERT_KEY = "alert-key-0123456789abcdef"
MAINT_KEY = "maint-key-0123456789abcdef"
GH_SECRET = "gh-secret-0123456789abcdef"


def payload(name: str) -> dict:
    return json.loads((PAYLOADS / name).read_text())


def make_client(url: str, platform_root) -> TestClient:
    settings = Settings(
        database_url=url,
        alert_webhook_key=ALERT_KEY,
        maintenance_api_key=MAINT_KEY,
        github_webhook_secret=GH_SECRET,
        platform_root=platform_root,
    )
    return TestClient(create_app(settings))


@pytest.fixture
def client(db_url, platform_root):
    with make_client(db_url, platform_root) as c:
        yield c


@pytest.fixture
def db(db_url):
    with connect(db_url) as conn:
        yield conn


def post_alert(client, body, key=ALERT_KEY):
    params = {"key": key} if key is not None else {}
    return client.post("/events/azure-monitor", params=params, json=body)


def post_github(client, event, body, delivery, secret=GH_SECRET, signature=None):
    raw = json.dumps(body).encode()
    sig = signature or "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return client.post(
        "/events/github",
        content=raw,
        headers={"X-GitHub-Event": event, "X-GitHub-Delivery": delivery,
                 "X-Hub-Signature-256": sig, "Content-Type": "application/json"},
    )


def server_log(caplog) -> str:
    """Logzeilen der API; der httpx-Testclient protokolliert seine eigene Request-URL."""
    return "\n".join(r.getMessage() for r in caplog.records if not r.name.startswith("httpx"))


def count(db, table, where="true", params=()):
    return db.execute(f"SELECT count(*) AS n FROM {table} WHERE {where}", params).fetchone()["n"]


# --- 2.1 Transaktionale Annahme ------------------------------------------------------


def test_accepted_event_survives_api_restart(db_url, platform_root, db):
    with make_client(db_url, platform_root) as c:
        response = post_alert(c, payload("azure-monitor-fired.json"))
    assert response.status_code == 202
    job_id = response.json()["jobId"]
    # Neue API-Instanz, die Quelle sendet nichts erneut.
    with make_client(db_url, platform_root):
        job = db.execute("SELECT * FROM jobs WHERE id = %s", (job_id,)).fetchone()
    assert job["state"] == "queued"
    assert (job["app_id"], job["environment"]) == ("affine", "staging")


def test_database_failure_is_not_acknowledged(unreachable_db_url, platform_root):
    with make_client(unreachable_db_url, platform_root) as c:
        response = post_alert(c, payload("azure-monitor-fired.json"))
    assert response.status_code == 503
    assert response.json()["status"] == "rejected"
    assert "jobId" not in response.json()


# --- 2.2 Azure Monitor -----------------------------------------------------------------


def test_alert_with_valid_key(client, db):
    response = post_alert(client, payload("azure-monitor-fired.json"))
    assert response.status_code == 202
    event = db.execute("SELECT * FROM events").fetchone()
    assert event["source"] == "azure-monitor"
    assert event["external_ref"].endswith("/alerts/11111111-1111-1111-1111-111111111111")
    assert event["severity"] == "error"
    assert event["occurred_at"] is not None


@pytest.mark.parametrize("key", [None, "", "falsch-aber-lang-genug"])
def test_alert_without_or_wrong_key(client, db, key, caplog):
    caplog.set_level(logging.DEBUG)
    response = post_alert(client, payload("azure-monitor-fired.json"), key=key)
    assert response.status_code == 401
    assert count(db, "events") == 0 and count(db, "jobs") == 0
    if key:
        assert key not in server_log(caplog)
        assert "key=***" in server_log(caplog)


def test_valid_key_is_masked_in_request_log(client, caplog):
    caplog.set_level(logging.INFO)
    post_alert(client, payload("azure-monitor-fired.json"))
    assert ALERT_KEY not in server_log(caplog)
    assert "/events/azure-monitor?key=***" in server_log(caplog)


def test_resolved_updates_alert_state_only(client, db):
    fired = post_alert(client, payload("azure-monitor-fired.json")).json()
    resolved = post_alert(client, payload("azure-monitor-resolved.json"))
    assert resolved.status_code == 202
    assert resolved.json()["incidentId"] == fired["incidentId"]
    assert resolved.json()["jobId"] == fired["jobId"]
    incident = db.execute("SELECT * FROM incidents").fetchone()
    assert incident["alert_state"] == "resolved"
    job = db.execute("SELECT * FROM jobs").fetchone()
    assert job["state"] == "queued"  # kein Vorfallabschluss ohne Verifikation
    assert count(db, "jobs") == 1


# --- 2.3 App-Zuordnung -------------------------------------------------------------------


def test_mapping_via_custom_properties_wins_over_rule_name(client, db):
    body = payload("azure-monitor-fired.json")
    body["data"]["essentials"]["alertRule"] = "alert-other-staging-availability"
    response = post_alert(client, body).json()
    job = db.execute("SELECT * FROM jobs WHERE id = %s", (response["jobId"],)).fetchone()
    assert (job["app_id"], job["environment"], job["state"]) == ("affine", "staging", "queued")


def test_mapping_via_rule_name(client, db):
    response = post_alert(client, payload("azure-monitor-fired-rule-name-only.json")).json()
    job = db.execute("SELECT * FROM jobs WHERE id = %s", (response["jobId"],)).fetchone()
    assert (job["app_id"], job["environment"]) == ("affine", "staging")


def test_rule_name_with_hyphenated_app_id(client, db):
    body = payload("azure-monitor-fired-rule-name-only.json")
    body["data"]["essentials"]["alertRule"] = "alert-operations-agent-staging-healthz"
    response = post_alert(client, body).json()
    assert response["mappingError"] is None
    job = db.execute("SELECT * FROM jobs WHERE id = %s", (response["jobId"],)).fetchone()
    assert job["app_id"] == "operations-agent"


def test_unmapped_alert_is_stored_as_mapping_error(client, db):
    response = post_alert(client, payload("azure-monitor-fired-unmapped.json"))
    assert response.status_code == 202
    assert "Regelname" in response.json()["mappingError"]
    job = db.execute("SELECT * FROM jobs").fetchone()
    assert job["state"] == "needs_attention" and job["app_id"] is None
    assert "Zuordnungsfehler" in job["state_reason"]
    event = db.execute("SELECT payload, mapping_error FROM events").fetchone()
    assert event["payload"]["data"]["essentials"]["alertRule"] == "cpu-high-somewhere"


def test_app_missing_from_catalog(client, db):
    body = payload("azure-monitor-fired.json")
    body["data"]["customProperties"]["app-id"] = "crm"
    response = post_alert(client, body).json()
    assert "nicht im Katalog" in response["mappingError"]
    assert count(db, "jobs", "state = 'queued'") == 0


# --- 2.4 bis 2.6 GitHub ------------------------------------------------------------------


def test_github_issue_from_form(client, db):
    response = post_github(client, "issues", payload("github-issue-opened.json"), "d-1")
    assert response.status_code == 202
    body = response.json()
    assert body["mappingError"] is None
    job = db.execute("SELECT * FROM jobs WHERE id = %s", (body["jobId"],)).fetchone()
    assert (job["app_id"], job["environment"], job["state"]) == ("affine", "staging", "queued")
    event = db.execute("SELECT * FROM events").fetchone()
    assert event["external_ref"] == "mmkdigitalio/hackathon-platform-team#42"
    assert event["source_ref"]["url"].endswith("/issues/42")


@pytest.mark.parametrize("signature", [None, "sha256=" + "0" * 64, "sha1=abc"])
def test_github_invalid_signature(client, db, signature):
    body = payload("github-issue-opened.json")
    raw = json.dumps(body).encode()
    headers = {"X-GitHub-Event": "issues", "X-GitHub-Delivery": "d-2", "Content-Type": "application/json"}
    if signature:
        headers["X-Hub-Signature-256"] = signature
    assert client.post("/events/github", content=raw, headers=headers).status_code == 401
    assert count(db, "events") == 0


def test_github_signature_over_unmodified_body(client, db):
    body = payload("github-issue-opened.json")
    raw = json.dumps(body).encode()
    sig = "sha256=" + hmac.new(GH_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    tampered = raw.replace(b"affine", b"crm00x", 1)
    response = client.post("/events/github", content=tampered, headers={
        "X-GitHub-Event": "issues", "X-GitHub-Delivery": "d-3", "X-Hub-Signature-256": sig})
    assert response.status_code == 401


def test_github_foreign_repository(client, db):
    response = post_github(client, "issues", payload("github-issue-opened-foreign-repo.json"), "d-4")
    assert response.status_code == 403
    assert count(db, "events") == 0 and count(db, "jobs") == 0


def test_github_irrelevant_event_is_ignored(client, db):
    body = payload("github-issue-opened.json")
    body["action"] = "edited"
    response = post_github(client, "issues", body, "d-5")
    assert response.status_code == 202 and response.json()["status"] == "ignored"
    assert count(db, "events") == 0


def test_issue_without_form_sections(client, db):
    response = post_github(client, "issues", payload("github-issue-opened-without-form.json"), "d-6")
    assert "### App" in response.json()["mappingError"]
    assert count(db, "jobs", "state = 'needs_attention'") == 1
    assert count(db, "jobs", "state = 'queued'") == 0


def test_agent_marked_comment_creates_nothing(client, db):
    post_github(client, "issues", payload("github-issue-opened.json"), "d-7")
    before = (count(db, "events"), count(db, "jobs"))
    response = post_github(client, "issue_comment", payload("github-comment-agent-marked.json"), "d-8")
    assert response.json()["status"] == "ignored"
    assert (count(db, "events"), count(db, "jobs")) == before


def test_unmarked_comment_from_same_account_is_processed(client, db):
    opened = post_github(client, "issues", payload("github-issue-opened.json"), "d-9").json()
    comment = payload("github-comment-human-same-account.json")
    agent_login = payload("github-comment-agent-marked.json")["comment"]["user"]["login"]
    assert comment["comment"]["user"]["login"] == agent_login
    response = post_github(client, "issue_comment", comment, "d-10")
    assert response.status_code == 202 and response.json()["status"] == "accepted"
    assert response.json()["incidentId"] == opened["incidentId"]


# --- 2.7 Deduplizierung und Vorfallzuordnung ---------------------------------------------


def test_repeated_delivery_points_to_same_record(client, db):
    first = post_alert(client, payload("azure-monitor-fired.json"))
    second = post_alert(client, payload("azure-monitor-fired.json"))
    assert first.status_code == 202 and second.status_code == 200
    assert second.json()["status"] == "duplicate"
    for field in ("eventId", "incidentId", "jobId"):
        assert first.json()[field] == second.json()[field]
    assert (count(db, "events"), count(db, "jobs"), count(db, "job_transitions")) == (1, 1, 1)


def test_repeated_github_delivery(client, db):
    a = post_github(client, "issues", payload("github-issue-opened.json"), "same-delivery").json()
    b = post_github(client, "issues", payload("github-issue-opened.json"), "same-delivery").json()
    assert b["status"] == "duplicate" and a["jobId"] == b["jobId"]
    assert count(db, "jobs") == 1


def test_comment_attaches_to_issue_incident(client, db):
    opened = post_github(client, "issues", payload("github-issue-opened.json"), "d-11").json()
    comment = post_github(client, "issue_comment", payload("github-comment-human-same-account.json"), "d-12").json()
    assert comment["incidentId"] == opened["incidentId"]
    assert comment["jobId"] == opened["jobId"]
    assert count(db, "incidents") == 1


def test_alert_and_issue_for_same_app_are_separate_incidents(client, db):
    alert = post_alert(client, payload("azure-monitor-fired.json")).json()
    issue = post_github(client, "issues", payload("github-issue-opened.json"), "d-13").json()
    assert alert["incidentId"] != issue["incidentId"]
    sources = {r["source"] for r in db.execute("SELECT source FROM incidents").fetchall()}
    assert sources == {"azure-monitor", "github"}


def test_concurrent_duplicate_deliveries(db_url, platform_root, db):
    import threading

    results = []
    with make_client(db_url, platform_root) as c:
        def send():
            results.append(post_alert(c, payload("azure-monitor-fired.json")).json())
        threads = [threading.Thread(target=send) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    assert len({r["jobId"] for r in results}) == 1
    assert count(db, "events") == 1 and count(db, "jobs") == 1


# --- 2.8 Wartung -------------------------------------------------------------------------


def test_maintenance_with_key(client, db):
    response = client.post("/events/maintenance", json=payload("maintenance-health-check-failed.json"),
                           headers={"X-Api-Key": MAINT_KEY})
    assert response.status_code == 202
    job = db.execute("SELECT * FROM jobs").fetchone()
    assert (job["app_id"], job["state"]) == ("affine", "queued")


@pytest.mark.parametrize("headers", [{}, {"X-Api-Key": "wrong-key-0123456789"}])
def test_maintenance_without_key(client, db, headers, caplog):
    caplog.set_level(logging.DEBUG)
    response = client.post("/events/maintenance", json=payload("maintenance-health-check-failed.json"),
                           headers=headers)
    assert response.status_code == 401
    assert count(db, "jobs") == 0
    assert MAINT_KEY not in caplog.text and "wrong-key-0123456789" not in server_log(caplog)


def test_repeated_health_check_failure_attaches_to_open_job(client, db):
    first = payload("maintenance-health-check-failed.json")
    second = copy.deepcopy(first)
    second["deliveryId"] = "health-check:1234567891:1:affine:staging"
    a = client.post("/events/maintenance", json=first, headers={"X-Api-Key": MAINT_KEY}).json()
    b = client.post("/events/maintenance", json=second, headers={"X-Api-Key": MAINT_KEY}).json()
    assert a["jobId"] == b["jobId"] and b["status"] == "accepted"


# --- 3.4 /healthz ------------------------------------------------------------------------


def test_healthz_ok_with_fresh_heartbeat(client, db):
    db.execute("INSERT INTO worker_heartbeats (worker_id, beat_at) VALUES ('w1', now())")
    response = client.get("/healthz")
    assert response.status_code == 200 and response.json()["worker"] == "ok"


def test_healthz_503_without_heartbeat(client):
    assert client.get("/healthz").status_code == 503


def test_healthz_503_after_10_minutes(client, db):
    db.execute("INSERT INTO worker_heartbeats (worker_id, beat_at) VALUES ('w1', now() - interval '10 minutes 5 seconds')")
    response = client.get("/healthz")
    assert response.status_code == 503 and response.json()["worker"] == "Heartbeat zu alt"


def test_healthz_503_when_database_unreachable(unreachable_db_url, platform_root):
    with make_client(unreachable_db_url, platform_root) as c:
        response = c.get("/healthz")
    assert response.status_code == 503 and response.json()["database"] == "unreachable"


# --- 5.5 /runs -----------------------------------------------------------------------------


def test_runs_requires_key(client):
    job_id = post_alert(client, payload("azure-monitor-fired.json")).json()["jobId"]
    assert client.get(f"/runs/{job_id}").status_code == 401
    assert client.get(f"/runs/{job_id}", headers={"X-Api-Key": "wrong-key-0123456789"}).status_code == 401
    response = client.get(f"/runs/{job_id}", headers={"X-Api-Key": MAINT_KEY})
    assert response.status_code == 200
    run = response.json()
    assert run["job"]["id"] == job_id and run["events"][0]["source"] == "azure-monitor"
    for secret in (ALERT_KEY, MAINT_KEY, GH_SECRET):
        assert secret not in response.text


def test_runs_unknown_job(client):
    assert client.get("/runs/00000000-0000-0000-0000-000000000000", headers={"X-Api-Key": MAINT_KEY}).status_code == 404
