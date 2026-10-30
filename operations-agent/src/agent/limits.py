"""Codebasierte Laufgrenzen: Laufzeit, Tool-Aufrufe, Reparaturversuche, Modellkosten."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import LLMResult

from agent.config import ModelSettings
from agent.model import cost_usd
from contracts import RunLimits, Usage
from tools.common import utcnow

LimitName = Literal["runtime", "tool_calls", "repair_attempts", "cost"]


class LimitReached(Exception):
    def __init__(self, limit: LimitName, detail: str) -> None:
        super().__init__(f"Grenze erreicht: {limit} ({detail})")
        self.limit = limit
        self.detail = detail


class RunBudget:
    def __init__(self, limits: RunLimits, model: ModelSettings, clock: Callable[[], float] = time.monotonic) -> None:
        self.limits = limits
        self.model = model
        self.clock = clock
        self.started = clock()
        self.tool_calls = 0
        self.repair_attempts = 0
        self.model_calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.cost = 0.0

    @property
    def deadline(self) -> float:
        return self.started + self.limits.max_runtime_seconds

    def remaining_seconds(self) -> float:
        return max(0.0, self.deadline - self.clock())

    def check_runtime(self) -> None:
        elapsed = self.clock() - self.started
        if elapsed >= self.limits.max_runtime_seconds:
            raise LimitReached("runtime", f"{int(elapsed)}s von {self.limits.max_runtime_seconds}s")

    def check_cost(self) -> None:
        if self.cost >= self.limits.max_cost_usd:
            raise LimitReached("cost", f"{self.cost:.4f} USD von {self.limits.max_cost_usd} USD")

    def before_model(self) -> None:
        self.check_runtime()
        self.check_cost()

    def add_usage(self, input_tokens: int, output_tokens: int) -> None:
        self.model_calls += 1
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.cost += cost_usd(self.model, input_tokens, output_tokens)

    def before_tool(self, name: str) -> None:
        self.check_runtime()
        if self.tool_calls >= self.limits.max_tool_calls:
            raise LimitReached("tool_calls", f"{self.tool_calls} von {self.limits.max_tool_calls}, abgelehnt: {name}")
        self.tool_calls += 1

    def begin_repair(self) -> None:
        if self.repair_attempts >= self.limits.max_repair_attempts:
            raise LimitReached(
                "repair_attempts", f"{self.repair_attempts} von {self.limits.max_repair_attempts} ohne positive Verifikation"
            )
        self.repair_attempts += 1

    def usage(self) -> Usage:
        return Usage(
            provider=self.model.provider,
            model=self.model.model,
            model_calls=self.model_calls,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            cost_usd=round(self.cost, 6),
        )


class UsageCallback(BaseCallbackHandler):
    """Erfasst Tokens aller Modellaufrufe im Graphen, auch solcher außerhalb der Middleware-Kette."""

    def __init__(self, budget: RunBudget) -> None:
        self.budget = budget

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        for gens in response.generations:
            for g in gens:
                msg = getattr(g, "message", None)
                if isinstance(msg, AIMessage) and msg.usage_metadata:
                    self.budget.add_usage(msg.usage_metadata.get("input_tokens", 0), msg.usage_metadata.get("output_tokens", 0))


def _tool_name(tool: Any) -> str:
    return tool.get("name", "") if isinstance(tool, dict) else getattr(tool, "name", "")


class BudgetMiddleware(AgentMiddleware):
    """Erzwingt die Grenzen um jeden Modell- und Werkzeugaufruf und blendet nicht freigegebene Tools aus.

    Grenzverletzungen werden als ``LimitReached`` geworfen und beenden den Graph-Lauf.
    """

    def __init__(
        self,
        budget: RunBudget,
        allowed_tools: frozenset[str],
        on_sdk_tool: Callable[[str, Any, Any], None] | None = None,
        sdk_tools: frozenset[str] = frozenset(),
    ) -> None:
        super().__init__()
        self.budget = budget
        self.allowed = allowed_tools
        # SDK-Werkzeuge (Dateien, Shell) protokolliert kein eigener Wrapper; das übernimmt dieser Rückruf.
        self.on_sdk_tool = on_sdk_tool
        self.sdk_tools = sdk_tools

    def _filter(self, request: ModelRequest) -> ModelRequest:
        return request.override(tools=[t for t in request.tools if _tool_name(t) in self.allowed])

    def _account(self, response: ModelResponse) -> None:
        # Verbrauch erfasst ``UsageCallback`` für jeden Modellaufruf, auch für die SDK-Zusammenfassung.
        self.budget.check_cost()

    def wrap_model_call(self, request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]) -> ModelResponse:
        self.budget.before_model()
        response = handler(self._filter(request))
        self._account(response)
        return response

    async def awrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[ModelResponse]]
    ) -> ModelResponse:
        self.budget.before_model()
        response = await handler(self._filter(request))
        self._account(response)
        return response

    def wrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        name = request.tool_call["name"]
        if name not in self.allowed:
            return ToolMessage(
                content=f"Fehler: Werkzeug '{name}' ist nicht freigegeben.",
                tool_call_id=request.tool_call["id"] or "",
                name=name,
                status="error",
            )
        self.budget.before_tool(name)
        if self.on_sdk_tool is None or name not in self.sdk_tools:
            return handler(request)
        started = utcnow()
        result = handler(request)
        self.on_sdk_tool(name, request.tool_call.get("args"), (started, result))
        return result
