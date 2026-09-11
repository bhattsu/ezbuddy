"""Shared runtime context injected into LLM system prompts."""

from __future__ import annotations

from datetime import date
from typing import Any, Dict

from app.agents.utils.language_policy import ENGLISH_ONLY_PROMPT_BLOCK

CURRENT_DATE_BLOCK = """## Today's date
Today is {current_date_long} ({current_date}). Use this as the reference when interpreting or validating dates."""


def get_current_date_context(*, on_date: date | None = None) -> Dict[str, str]:
    """Return standard date placeholders for prompt formatting."""
    today = on_date or date.today()
    iso = today.isoformat()
    long_form = today.strftime("%B %d, %Y")
    return {
        "current_date": iso,
        "current_date_long": long_form,
        "current_date_line": f"Today's date: {long_form} ({iso}).",
    }


def format_llm_prompt(
    template: str,
    *,
    on_date: date | None = None,
    **kwargs: Any,
) -> str:
    """Format a prompt template and inject today's date unless already present."""
    ctx = get_current_date_context(on_date=on_date)
    text = template
    if "{current_date" not in text:
        text = CURRENT_DATE_BLOCK.format(**ctx) + "\n\n" + text
    if "## Language" not in text:
        text = ENGLISH_ONLY_PROMPT_BLOCK + "\n\n" + text
    return text.format(**{**ctx, **kwargs})
