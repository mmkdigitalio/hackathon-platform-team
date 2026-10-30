"""P1-02: echter Modellaufruf mit typisiertem Diagnosewerkzeug und protokolliertem Ergebnis ohne Secrets.

Aufruf: ``uv run ops-agent-prototype --url https://example.com`` (Anbieter über ``OPS_MODEL_*``).
Mit ``--checkpoint-dsn`` wird der Thread in PostgreSQL gespeichert; ein zweiter Aufruf mit
derselben ``--thread-id`` setzt ihn fort.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from importlib.metadata import version
from typing import Any

import httpx
from deepagents import create_deep_agent
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import BaseModel, Field, HttpUrl

from agent.config import model_settings
from agent.limits import BudgetMiddleware, RunBudget, UsageCallback
from agent.model import build_chat_model
from contracts import RunLimits
from tools.common import utcnow

PINNED = (
    "deepagents",
    "langchain",
    "langchain-anthropic",
    "langchain-openai",
    "langgraph",
    "langgraph-checkpoint-postgres",
    "psycopg",
)


class ProbeInput(BaseModel):
    url: HttpUrl = Field(description="Absolute HTTPS-URL, die geprüft werden soll")


class ProbeResult(BaseModel):
    url: str
    status_code: int | None
    duration_ms: int
    checked_at: str
    error_type: str | None


def http_probe(url: HttpUrl) -> str:
    """Prüft eine HTTPS-URL und liefert Status, Dauer und Zeitpunkt."""
    if url.scheme != "https":
        return ProbeResult(url=str(url), status_code=None, duration_ms=0, checked_at=utcnow().isoformat(), error_type="nur https").model_dump_json()
    started = time.monotonic()
    at = utcnow().isoformat()
    try:
        r = httpx.get(str(url), timeout=10, follow_redirects=False)
        res = ProbeResult(url=str(url), status_code=r.status_code, duration_ms=0, checked_at=at, error_type=None)
    except httpx.HTTPError as e:
        res = ProbeResult(url=str(url), status_code=None, duration_ms=0, checked_at=at, error_type=type(e).__name__)
    res.duration_ms = int((time.monotonic() - started) * 1000)
    return res.model_dump_json()


def _checkpointer(dsn: str | None) -> tuple[Any, dict[str, Any]]:
    if not dsn:
        return InMemorySaver(), {"type": "memory"}
    from agent.persistence import SDK_SCHEMA_VERSION, checkpointer, connect

    return checkpointer(connect(dsn)), {"type": "postgres", "sdkSchemaVersion": SDK_SCHEMA_VERSION}


def run(url: str, thread_id: str, dsn: str | None) -> dict[str, Any]:
    ms = model_settings()
    budget = RunBudget(RunLimits(max_tool_calls=3, max_cost_usd=0.2, max_runtime_seconds=120), ms)
    tool = StructuredTool.from_function(http_probe, name="http_probe", args_schema=ProbeInput)
    saver, cp_info = _checkpointer(dsn)
    agent = create_deep_agent(
        model=build_chat_model(ms),
        tools=[tool],
        system_prompt=(
            "Du bist ein Diagnoseagent. Rufe das Werkzeug http_probe genau einmal für die genannte URL auf "
            "und fasse Status und Dauer in einem Satz zusammen."
        ),
        middleware=[BudgetMiddleware(budget, frozenset({"http_probe"}))],
        checkpointer=saver,
        name="p1-02-prototype",
    )
    config = {"configurable": {"thread_id": thread_id}, "callbacks": [UsageCallback(budget)]}
    before = agent.get_state(config).values.get("messages", [])
    out = agent.invoke({"messages": [HumanMessage(f"Prüfe {url}")]}, config=config)
    messages = out["messages"][len(before):]
    calls = [{"name": c["name"], "args": c["args"]} for m in messages if isinstance(m, AIMessage) for c in m.tool_calls]
    results = [json.loads(m.content) for m in messages if isinstance(m, ToolMessage) and m.name == "http_probe"]
    final = next((m.text for m in reversed(messages) if isinstance(m, AIMessage) and not m.tool_calls), "")
    return {
        "recordedAt": utcnow().isoformat(),
        "packages": {p: version(p) for p in PINNED},
        "model": ms.public(),
        "checkpoint": {**cp_info, "threadId": thread_id, "messagesBefore": len(before), "messagesAfter": len(out["messages"])},
        "toolCalls": calls,
        "toolResults": results,
        "answer": final,
        "usage": budget.usage().model_dump(),
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--url", default="https://example.com")
    p.add_argument("--thread-id", default="p1-02")
    p.add_argument("--checkpoint-dsn", default=None)
    args = p.parse_args(argv)
    protocol = run(args.url, args.thread_id, args.checkpoint_dsn)
    print(json.dumps(protocol, ensure_ascii=False, indent=2, default=str))
    return 0 if protocol["toolResults"] else 1


if __name__ == "__main__":
    sys.exit(main())
