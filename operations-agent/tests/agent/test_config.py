from __future__ import annotations

import json

import pytest
from langchain_anthropic import ChatAnthropic
from langchain_openai import AzureChatOpenAI

from agent.config import ConfigError, agent_settings, limits_from_env, model_settings
from agent.model import build_chat_model
from agent.persistence import SDK_SCHEMA_VERSION, sdk_migrations

KEYS = json.dumps({"anthropic": "sk-ant-secret", "azure-openai": "az-secret"})


def test_provider_switch_by_configuration_only() -> None:
    a = model_settings({"OPS_MODEL_PROVIDER": "anthropic", "OPS_MODEL_NAME": "claude-sonnet-5-5", "OPS_MODEL_API_KEYS": KEYS})
    z = model_settings(
        {
            "OPS_MODEL_PROVIDER": "azure-openai",
            "OPS_MODEL_NAME": "gpt-deploy",
            "OPS_MODEL_ENDPOINT": "https://mmk.openai.azure.com",
            "OPS_MODEL_API_KEYS": KEYS,
        }
    )
    assert isinstance(build_chat_model(a), ChatAnthropic)
    assert isinstance(build_chat_model(z), AzureChatOpenAI)
    assert a.api_key.get_secret_value() == "sk-ant-secret" and z.api_key.get_secret_value() == "az-secret"


def test_local_key_from_env_and_no_secret_in_output() -> None:
    s = model_settings({"ANTHROPIC_API_KEY": "sk-local"})
    assert s.model == "claude-sonnet-5-5"
    assert "sk-local" not in repr(s) and "sk-local" not in json.dumps(s.public())


def test_missing_key_and_bad_provider() -> None:
    with pytest.raises(ConfigError):
        model_settings({"OPS_MODEL_PROVIDER": "anthropic"})
    with pytest.raises(ConfigError):
        model_settings({"OPS_MODEL_PROVIDER": "openai", "OPS_MODEL_API_KEYS": KEYS})
    with pytest.raises(ConfigError):
        model_settings({"OPS_MODEL_PROVIDER": "azure-openai", "OPS_MODEL_NAME": "x", "OPS_MODEL_API_KEYS": KEYS})


def test_limits_configurable_without_code_change() -> None:
    d = limits_from_env({})
    assert (d.max_runtime_seconds, d.max_tool_calls, d.max_repair_attempts, d.max_cost_usd) == (600, 30, 2, 1.0)
    c = limits_from_env({"OPS_LIMIT_TOOL_CALLS": "12", "OPS_LIMIT_COST_USD": "0.5"})
    assert c.max_tool_calls == 12 and c.max_cost_usd == 0.5


def test_agent_settings_defaults() -> None:
    s = agent_settings({"OPS_GITHUB_TOKEN": "t", "OPS_BASE_DOMAIN": "1-2-3-4.sslip.io"})
    assert s.dispatch_workflows == frozenset({"app-deploy.yml"})
    assert s.rules_file.name == "betriebsregeln.md" and s.rules_file.is_file()
    assert "'t'" not in repr(s)


def test_sdk_migrations_for_c() -> None:
    steps = sdk_migrations()
    assert steps[-1].version == SDK_SCHEMA_VERSION
    assert [s.version for s in steps if s.requires_autocommit]
