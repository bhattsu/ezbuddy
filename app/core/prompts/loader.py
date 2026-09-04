"""Resolve prompt builder by version id. Default and one alternate wired for analytics."""

from typing import Any, Callable, Dict, Optional

from app.core.prompts.templates import build_key_value_extraction_prompt

# Map version id -> builder (same signature as build_key_value_extraction_prompt)
_PROMPT_BUILDERS: Dict[str, Callable[..., Dict[str, Any]]] = {
    "default": build_key_value_extraction_prompt,
    "v1": build_key_value_extraction_prompt,
}


def get_prompt_builder(version: Optional[str] = None) -> Callable[..., Dict[str, Any]]:
    """Return prompt builder for the given version; fallback to default."""
    if not version or not version.strip():
        return _PROMPT_BUILDERS["default"]
    key = version.strip().lower()
    return _PROMPT_BUILDERS.get(key, _PROMPT_BUILDERS["default"])
