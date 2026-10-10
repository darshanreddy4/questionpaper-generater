from __future__ import annotations

from app.config import Settings
from app.llm.base import LLMError, LLMProvider, extract_json
from app.llm.providers import AnthropicProvider, OpenAICompatProvider


class OfflineProvider(LLMProvider):
    """Marker for 'no model configured'. Question writing falls back to the template engine."""

    name = "offline"
    model = "template-engine"
    is_offline = True

    def _complete(self, *a, **k) -> str:
        raise LLMError("No language model is configured. Set OPENAI_API_KEY or ANTHROPIC_API_KEY.")


def get_provider(settings: Settings) -> LLMProvider:
    choice = settings.llm_provider
    conc = settings.llm_max_concurrency
    if choice in ("auto", "openai") and settings.openai_api_key:
        return OpenAICompatProvider(settings.openai_api_key, settings.openai_base_url, settings.openai_model,
                                    settings.llm_timeout_seconds, conc, settings.openai_fast_model)
    if choice in ("auto", "anthropic") and settings.anthropic_api_key:
        return AnthropicProvider(settings.anthropic_api_key, settings.anthropic_base_url, settings.anthropic_model,
                                 settings.llm_timeout_seconds, conc, settings.anthropic_fast_model)
    return OfflineProvider(conc)


__all__ = ["get_provider", "LLMProvider", "LLMError", "OfflineProvider", "extract_json"]
