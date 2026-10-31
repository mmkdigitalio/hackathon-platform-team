"""Portal-Kontext, Besitzprüfung und idempotente Ergebnisnachrichten."""

import pytest

from contracts import ConversationRef, OperationsServices, OwnershipLost
from persistence import jobs, migrate, portal
from persistence.db import check_schema, connect
from worker.runner import Worker, WorkerConfig
from worker.services import ConversationPostService, build_services

from . import fakes
from .conftest import connector
from .test_worker_and_states import enqueue


def seed(db_url):
    with connect(db_url) as conn:
        code_id, _ = portal.create_code(conn, "Worker")
        receipt = portal.store_message(conn, code_id=code_id, app_id="affine", environment="staging",
                                       reporter="Anna", text="Upload defekt", client_message_id="worker-1")
    return code_id, receipt


def claim(db_url):
    with connect(db_url) as conn:
        return jobs.claim_next(conn, "portal-worker", 60)


def test_post_has_result_truncation_and_lost_lease(db_url):
    _, receipt = seed(db_url)
    owned = claim(db_url)
    service = ConversationPostService(connector(db_url), owned.lease)
    assert not service.has_result(receipt["jobId"])
    message_id = service.post(receipt["jobId"], "x" * 4100, "reply")
    result_id = service.post(receipt["jobId"], "Bitte Support kontaktieren", "result")
    assert service.has_result(receipt["jobId"])
    assert service.post(receipt["jobId"], "Erneutes Ergebnis", "result") == result_id
    with connect(db_url) as conn:
        message = conn.execute("SELECT * FROM conversation_messages WHERE id = %s", (message_id,)).fetchone()
        assert message["role"] == "agent" and message["author"] == "Betriebsagent"
        assert message["kind"] == "reply" and len(message["body"]) == 4000 and message["body"].endswith("… [gekürzt]")
        assert conn.execute("SELECT count(*) AS n FROM conversation_messages WHERE kind = 'result'").fetchone()["n"] == 1
        conn.execute("UPDATE jobs SET lease_version = lease_version + 1 WHERE id = %s", (receipt["jobId"],))
    with pytest.raises(OwnershipLost):
        service.post(receipt["jobId"], "zu spät", "reply")
    with pytest.raises(OwnershipLost):
        service.has_result(receipt["jobId"])
    with connect(db_url) as conn:
        assert conn.execute("SELECT count(*) AS n FROM conversation_messages").fetchone()["n"] == 3


def test_expired_lease_and_wrong_job(db_url):
    _, receipt = seed(db_url)
    owned = claim(db_url)
    service = ConversationPostService(connector(db_url), owned.lease)
    with pytest.raises(OwnershipLost):
        service.post("another-job", "falsch", "reply")
    with pytest.raises(OwnershipLost):
        service.has_result("another-job")
    with connect(db_url) as conn:
        conn.execute("UPDATE jobs SET lease_expires_at = now() - interval '1 second' WHERE id = %s", (receipt["jobId"],))
    with pytest.raises(OwnershipLost):
        service.post(receipt["jobId"], "zu spät", "reply")


def test_worker_passes_history_across_jobs(db_url):
    code_id, first = seed(db_url)
    invocations = []

    def execute(inv, services):
        invocations.append(inv)
        assert services.conversation is not None
        services.conversation.post(inv.job_id, "Ich prüfe den Upload", "reply")
        services.conversation.post(inv.job_id, "Bitte weiter prüfen", "result")
        return fakes.result(inv)

    worker = Worker(connector(db_url), execute, WorkerConfig(context_git_ref="abc123"))
    assert worker.run_once() == first["jobId"]
    assert invocations[0].context.conversation == ConversationRef(id=first["conversationId"], reporter="Anna")
    assert "reporter (Anna): Upload defekt" in invocations[0].context.issue_text
    with connect(db_url) as conn:
        second = portal.store_message(conn, code_id=code_id, conversation_id=first["conversationId"],
                                      reporter="Ben", text="Upload weiterhin defekt", client_message_id="worker-2")
    assert second["jobId"] != first["jobId"]
    assert worker.run_once() == second["jobId"]
    context = invocations[-1].context
    assert context.conversation.reporter == "Ben" and context.conversation.id == first["conversationId"]
    text = context.issue_text
    assert text.index("Upload defekt") < text.index("agent (Betriebsagent): Ich prüfe") < text.index("Upload weiterhin defekt")
    assert "[20" in text and len(text) <= 4000


def test_transcript_keeps_last_4000_characters(db_url):
    code_id, receipt = seed(db_url)
    with connect(db_url) as conn:
        portal.store_message(conn, code_id=code_id, conversation_id=receipt["conversationId"],
                             reporter="Ben", text="x" * 3990 + "ENDE", client_message_id="long-2")
        events = conn.execute("SELECT source, external_ref, event_type, payload FROM events ORDER BY received_at").fetchall()
    context = Worker(connector(db_url), None)._context(events)
    assert len(context.issue_text) == 4000 and context.issue_text.endswith("ENDE")
    assert "Upload defekt" not in context.issue_text and context.conversation.reporter == "Ben"


def test_service_only_for_portal_jobs(db_url, platform_root):
    enqueue(db_url, platform_root)
    owned = claim(db_url)
    services, checkpoints = build_services(connector(db_url), owned.lease, owned.job, lock_ttl_seconds=60)
    assert services.conversation is None
    checkpoints.close()
    with pytest.raises(ValueError, match="Portal"):
        ConversationPostService(connector(db_url), owned.lease).post(owned.lease.job_id, "falsch", "reply")


def test_optional_contract_is_compatible():
    services = OperationsServices(actions=None, lock=None, state=None, ownership=None, checkpoints=None)
    assert services.conversation is None
    assert ConversationRef(id="conversation").reporter is None


def test_upgrade_from_initial_schema(empty_db_url):
    initial = migrate.load_migrations()[0]
    with connect(empty_db_url) as conn:
        with conn.transaction():
            conn.execute(initial.sql)
            conn.execute("CREATE TABLE schema_migrations (version integer PRIMARY KEY, name text, checksum text)")
            conn.execute("INSERT INTO schema_migrations VALUES (%s, %s, %s)",
                         (initial.version, initial.name, initial.checksum))
            incident = conn.execute("INSERT INTO incidents (source, incident_key) VALUES ('maintenance', 'keep') RETURNING id").fetchone()
        report = migrate.migrate(conn)
        assert report.applied == [2] and check_schema(conn, include_sdk=True) == 2
        assert conn.execute("SELECT id FROM incidents").fetchone()["id"] == incident["id"]
