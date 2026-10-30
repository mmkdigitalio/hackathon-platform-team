"""Tool-Calling-Modell je Anbieter; der Wechsel erfolgt nur über Konfiguration."""

from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel

from agent.config import ModelSettings


def build_chat_model(s: ModelSettings) -> BaseChatModel:
    if s.provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(model=s.model, api_key=s.api_key, max_tokens=4096, timeout=120, max_retries=2)
    from langchain_openai import AzureChatOpenAI

    return AzureChatOpenAI(
        azure_deployment=s.model,
        azure_endpoint=s.endpoint,
        api_version=s.api_version,
        api_key=s.api_key,
        timeout=120,
        max_retries=2,
        use_responses_api=s.responses_api,
        # Keine Speicherung bei Azure; Reasoning-Zustand reist verschlüsselt im Verlauf mit.
        **({"store": False, "include": ["reasoning.encrypted_content"]} if s.responses_api else {}),
    )


def cost_usd(s: ModelSettings, input_tokens: int, output_tokens: int) -> float:
    return (input_tokens * s.price_input_per_mtok + output_tokens * s.price_output_per_mtok) / 1_000_000
