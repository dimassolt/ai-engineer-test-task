"""Settings + environment loading.

Also normalizes API-key env var names: the SDKs expect `OPENAI_API_KEY`,
`ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, but a `.env` may use friendlier names. We copy
whatever is present into the canonical variable so provider setup "just works".
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Canonical env var -> list of accepted aliases found in the wild / this repo's .env.
PROVIDER_KEY_CANDIDATES: dict[str, list[str]] = {
    "OPENAI_API_KEY": ["OPENAI_API_KEY", "OpenAI-API-Key", "OPENAI_KEY"],
    "ANTHROPIC_API_KEY": ["ANTHROPIC_API_KEY", "Antropic", "Anthropic", "ANTHROPIC"],
    "GOOGLE_API_KEY": ["GOOGLE_API_KEY", "GEMINI_API_KEY", "Gemini-API-Key", "Google-API-Key"],
}

# provider name -> canonical env var it needs.
PROVIDER_ENV: dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GOOGLE_API_KEY",
}

# provider name -> langchain `model_provider` string + a sensible default model.
PROVIDER_MODEL_PROVIDER: dict[str, str] = {
    "openai": "openai",
    "anthropic": "anthropic",
    "gemini": "google_genai",
}
DEFAULT_MODELS: dict[str, str] = {
    "openai": "gpt-4o-mini",
    "anthropic": "claude-sonnet-4-6",
    "gemini": "gemini-2.0-flash",
}

# Preference order when `--provider auto` is used.
PROVIDER_ORDER: list[str] = ["anthropic", "openai", "gemini"]

DEFAULT_DATA_PATH = "data/mock_hotel_data.json"


def load_env(dotenv_path: str | Path = ".env") -> None:
    """Load .env (if present) and normalize provider key aliases. Idempotent."""
    p = Path(dotenv_path)
    if p.exists():
        load_dotenv(p, override=False)
    for canonical, candidates in PROVIDER_KEY_CANDIDATES.items():
        if os.environ.get(canonical):
            continue
        for alias in candidates:
            if os.environ.get(alias):
                os.environ[canonical] = os.environ[alias]
                break


@dataclass
class Settings:
    """Runtime configuration for a single agent run (built from CLI flags + env)."""

    mode: str = "human"                     # "human" | "auto"
    provider: str = "auto"                  # "auto" | "openai" | "anthropic" | "gemini"
    model: str | None = None                # override provider default
    data_path: str = DEFAULT_DATA_PATH
    checkpointer: str = "sqlite"            # "sqlite" | "memory"
    db_path: str = ".checkpoints.sqlite"
    dry_run: bool = False
    temperature: float = 0.0

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            mode=os.environ.get("HOTEL_AGENT_MODE", "human"),
            provider=os.environ.get("HOTEL_AGENT_PROVIDER", "auto"),
        )
