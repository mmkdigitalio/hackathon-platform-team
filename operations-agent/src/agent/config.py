"""Konfiguration aus Umgebungsvariablen; im Pod aus dem Kubernetes-Secret des App-Stacks."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import SecretStr

from contracts import RunLimits

Provider = Literal["anthropic", "azure-openai"]
PROVIDERS: tuple[Provider, ...] = ("anthropic", "azure-openai")
_KEY_ENV = {"anthropic": "ANTHROPIC_API_KEY", "azure-openai": "AZURE_OPENAI_API_KEY"}
# Platzhalterpreise in USD je 1 Mio. Tokens; vor Staging-Betrieb mit der Preisliste abgleichen.
_DEFAULT_PRICES = {"anthropic": (3.0, 15.0), "azure-openai": (2.5, 10.0)}


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class ModelSettings:
    provider: Provider
    model: str
    api_key: SecretStr = field(repr=False)
    endpoint: str | None = None
    api_version: str | None = None
    # Azure: Responses API; die GPT-6-Deployments erlauben Function Tools nicht über Chat Completions.
    responses_api: bool = True
    price_input_per_mtok: float = 0.0
    price_output_per_mtok: float = 0.0

    def public(self) -> dict[str, object]:
        """Protokollierbare Modellkonfiguration ohne Secret."""
        return {
            "provider": self.provider,
            "model": self.model,
            "endpoint": self.endpoint,
            "apiVersion": self.api_version,
            "responsesApi": self.responses_api if self.provider == "azure-openai" else None,
            "priceInputPerMTok": self.price_input_per_mtok,
            "priceOutputPerMTok": self.price_output_per_mtok,
        }


def _api_key(provider: Provider, env: dict[str, str]) -> SecretStr:
    """Foundation-Ausgabe ``modelApiKeys`` (JSON je Anbieter) hat Vorrang, lokal die Anbieter-Variable."""
    raw = env.get("OPS_MODEL_API_KEYS")
    if raw:
        try:
            keys = json.loads(raw)
        except ValueError as e:
            raise ConfigError("OPS_MODEL_API_KEYS ist kein JSON-Objekt") from e
        value = keys.get(provider) or keys.get(provider.replace("-", ""))
        if value:
            return SecretStr(value)
    value = env.get(_KEY_ENV[provider])
    if not value:
        raise ConfigError(f"Kein API-Key für {provider}: OPS_MODEL_API_KEYS oder {_KEY_ENV[provider]} setzen")
    return SecretStr(value)


def model_settings(env: dict[str, str] | None = None) -> ModelSettings:
    env = dict(os.environ if env is None else env)
    provider = env.get("OPS_MODEL_PROVIDER", "azure-openai")
    if provider not in PROVIDERS:
        raise ConfigError(f"OPS_MODEL_PROVIDER muss einer von {PROVIDERS} sein, nicht '{provider}'")
    model = env.get("OPS_MODEL_NAME") or ("claude-sonnet-5-5" if provider == "anthropic" else "")
    if not model:
        raise ConfigError("OPS_MODEL_NAME (Azure-Deploymentname) fehlt")
    endpoint = env.get("OPS_MODEL_ENDPOINT")
    if provider == "azure-openai" and not endpoint:
        raise ConfigError("OPS_MODEL_ENDPOINT ist für azure-openai erforderlich")
    pin, pout = _DEFAULT_PRICES[provider]
    return ModelSettings(
        provider=provider,
        model=model,
        api_key=_api_key(provider, env),
        endpoint=endpoint,
        api_version=env.get("OPS_MODEL_API_VERSION", "2025-04-01-preview") if provider == "azure-openai" else None,
        responses_api=env.get("OPS_MODEL_RESPONSES_API", "true").lower() == "true",
        price_input_per_mtok=float(env.get("OPS_MODEL_PRICE_INPUT_PER_MTOK", pin)),
        price_output_per_mtok=float(env.get("OPS_MODEL_PRICE_OUTPUT_PER_MTOK", pout)),
    )


def limits_from_env(env: dict[str, str] | None = None) -> RunLimits:
    """Startwerte für C's Auftragsbudget; ohne Codeänderung anpassbar."""
    env = dict(os.environ if env is None else env)
    d = RunLimits()
    return RunLimits(
        max_runtime_seconds=int(env.get("OPS_LIMIT_RUNTIME_SECONDS", d.max_runtime_seconds)),
        max_tool_calls=int(env.get("OPS_LIMIT_TOOL_CALLS", d.max_tool_calls)),
        max_repair_attempts=int(env.get("OPS_LIMIT_REPAIR_ATTEMPTS", d.max_repair_attempts)),
        max_cost_usd=float(env.get("OPS_LIMIT_COST_USD", d.max_cost_usd)),
    )


def _default_rules_file() -> Path:
    here = Path(__file__).resolve()
    for base in (*here.parents, Path("/app")):
        candidate = base / "platform" / "operations" / "agent" / "betriebsregeln.md"
        if candidate.is_file():
            return candidate
    return Path("/app/platform/operations/agent/betriebsregeln.md")


@dataclass(frozen=True)
class AgentSettings:
    github_repository: str
    github_token: SecretStr = field(repr=False)
    base_domain: str
    dispatch_workflows: frozenset[str]
    dispatch_ref: str
    image_pull_secret: str | None
    http_scheme: str
    rules_file: Path
    post_issue_comment: bool
    smoke_timeout_seconds: int


def agent_settings(env: dict[str, str] | None = None) -> AgentSettings:
    env = dict(os.environ if env is None else env)
    token = env.get("OPS_GITHUB_TOKEN")
    if not token:
        raise ConfigError("OPS_GITHUB_TOKEN (Foundation-Ausgabe opsGithubToken) fehlt")
    base_domain = env.get("OPS_BASE_DOMAIN")
    if not base_domain:
        raise ConfigError("OPS_BASE_DOMAIN (Foundation-Ausgabe baseDomain) fehlt")
    scheme = env.get("OPS_HTTP_SCHEME", "https")
    if scheme not in ("https", "http"):
        raise ConfigError("OPS_HTTP_SCHEME muss https oder http sein")
    return AgentSettings(
        github_repository=env.get("OPS_GITHUB_REPOSITORY", "mmkdigitalio/hackathon-platform-team"),
        github_token=SecretStr(token),
        base_domain=base_domain,
        dispatch_workflows=frozenset(w.strip() for w in env.get("OPS_DISPATCH_WORKFLOWS", "app-deploy.yml").split(",") if w.strip()),
        dispatch_ref=env.get("OPS_DISPATCH_REF", "main"),
        image_pull_secret=env.get("OPS_APP_IMAGE_PULL_SECRET") or None,
        http_scheme=scheme,
        rules_file=Path(env["OPS_RULES_FILE"]) if env.get("OPS_RULES_FILE") else _default_rules_file(),
        post_issue_comment=env.get("OPS_POST_ISSUE_COMMENT", "true").lower() == "true",
        smoke_timeout_seconds=int(env.get("OPS_SMOKE_TIMEOUT_SECONDS", "180")),
    )
