"""Checkpoint-Persistenz gegen echtes PostgreSQL (``OPS_TEST_POSTGRES_DSN``), sonst übersprungen."""

from __future__ import annotations

import os
import uuid

import pytest
from deepagents import create_deep_agent
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg import Connection, sql

from agent.persistence import SDK_SCHEMA_VERSION, CheckpointSchemaMissing, apply_sdk_migrations, checkpointer, connect

from .fakes import ScriptedModel, say

ADMIN_DSN = os.environ.get("OPS_TEST_POSTGRES_DSN")
pytestmark = pytest.mark.skipif(not ADMIN_DSN, reason="OPS_TEST_POSTGRES_DSN nicht gesetzt")


@pytest.fixture
def dsn():
    name = f"ops_test_{uuid.uuid4().hex[:8]}"
    with Connection.connect(ADMIN_DSN, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    base = ADMIN_DSN.rsplit("/", 1)[0]
    yield f"{base}/{name}"
    with Connection.connect(ADMIN_DSN, autocommit=True) as admin:
        admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


def graph(saver: PostgresSaver):
    model = ScriptedModel(responder=lambda msgs, i: say(f"Antwort {i + 1}"))
    return create_deep_agent(model=model, tools=[], system_prompt="Test", checkpointer=saver)


def test_checkpoint_survives_process_restart_without_own_initialisation(dsn: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(PostgresSaver, "setup", lambda self: pytest.fail("D darf setup() nicht aufrufen"))
    with connect(dsn) as conn, pytest.raises(CheckpointSchemaMissing):
        checkpointer(conn)

    with connect(dsn) as conn:  # Schritt von C's ops migrate, zweimal ohne Änderung
        assert apply_sdk_migrations(conn) == SDK_SCHEMA_VERSION
        assert apply_sdk_migrations(conn) == SDK_SCHEMA_VERSION

    config = {"configurable": {"thread_id": "job-42"}}
    with connect(dsn) as conn:
        graph(checkpointer(conn)).invoke({"messages": [HumanMessage("Erster Lauf")]}, config=config)

    with connect(dsn) as conn:  # neuer Prozess: neue Verbindung, neuer Saver
        agent = graph(checkpointer(conn))
        before = agent.get_state(config).values["messages"]
        assert [m.content for m in before] == ["Erster Lauf", "Antwort 1"]
        agent.invoke({"messages": [HumanMessage("Fortsetzung")]}, config=config)
        assert len(agent.get_state(config).values["messages"]) == 4
