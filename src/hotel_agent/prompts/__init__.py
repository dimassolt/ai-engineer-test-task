"""Prompt loader. One prompt per file, kept minimal and explicit (see CLAUDE.md §8)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_DIR = Path(__file__).parent


@lru_cache(maxsize=None)
def load_prompt(name: str) -> str:
    return (_DIR / f"{name}.md").read_text().strip()
