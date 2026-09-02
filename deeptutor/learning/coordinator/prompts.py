"""Localized prompts for the scope classifier's isolated LLM call."""

from __future__ import annotations

from functools import cache
from importlib import resources
from typing import Any

import yaml


@cache
def load_scope_prompt(language: str) -> str:
    """Load the classifier system prompt, defaulting unsupported locales to English."""
    lang = "zh" if str(language).lower().startswith("zh") else "en"
    raw: dict[str, Any] = (
        yaml.safe_load(
            resources.files(__package__).joinpath("prompts", f"{lang}.yaml").read_text("utf-8")
        )
        or {}
    )
    prompt = raw.get("system")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError(f"Scope classifier prompt is missing for {lang}")
    return prompt
