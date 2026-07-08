"""Provider-agnostic LLM access.

A tiny key manager: detect which provider keys are present, resolve `--provider auto` to
the first available one, and build a uniform LangChain chat model via `init_chat_model`.
Graph/node code never imports a provider SDK directly, so swapping OpenAI ↔ Anthropic ↔
Gemini never touches the agent logic.
"""

from __future__ import annotations

import os

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from ..config import (
    DEFAULT_MODELS,
    PROVIDER_ENV,
    PROVIDER_MODEL_PROVIDER,
    PROVIDER_ORDER,
    Settings,
    load_env,
)


class NoProviderError(RuntimeError):
    """No usable LLM provider key was found."""


def available_providers() -> list[str]:
    """Providers (in preference order) that have an API key set in the environment."""
    load_env()
    return [p for p in PROVIDER_ORDER if os.environ.get(PROVIDER_ENV[p])]


def resolve_provider(preference: str = "auto") -> str:
    """Resolve a provider name, honoring an explicit choice or auto-selecting the first
    available. Raises NoProviderError with actionable guidance if none are usable."""
    load_env()
    available = available_providers()
    if preference != "auto":
        if not os.environ.get(PROVIDER_ENV.get(preference, "")):
            raise NoProviderError(
                f"Provider '{preference}' selected but {PROVIDER_ENV.get(preference)} is not set."
            )
        return preference
    if not available:
        needed = ", ".join(PROVIDER_ENV.values())
        raise NoProviderError(
            f"No LLM provider key found. Set one of: {needed} (see .env.example)."
        )
    return available[0]


def get_chat_model(settings: Settings) -> BaseChatModel:
    """Build a chat model for the resolved provider. Deterministic (temp 0) by default."""
    provider = resolve_provider(settings.provider)
    model = settings.model or DEFAULT_MODELS[provider]
    return init_chat_model(
        model,
        model_provider=PROVIDER_MODEL_PROVIDER[provider],
        temperature=settings.temperature,
    )
