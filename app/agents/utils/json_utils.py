"""JSON parsing helpers for LLM structured outputs."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional


def parse_llm_json(raw: Any) -> Dict[str, Any]:
    """Extract a JSON object from LLM output (dict, string, or nested result)."""
    if isinstance(raw, dict):
        if any(k in raw for k in ("assistant_message", "intent", "answers_update", "workflow_complete")):
            return raw
        nested = raw.get("result") or raw.get("content")
        if isinstance(nested, dict):
            return parse_llm_json(nested)
        if isinstance(nested, str):
            return parse_llm_json(nested)
        text = raw.get("text") or raw.get("output")
        if isinstance(text, str):
            return parse_llm_json(text)
        return raw

    if not isinstance(raw, str):
        return {}

    text = raw.strip()
    if not text:
        return {}

    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    fence = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if fence:
        try:
            parsed = json.loads(fence.group(1))
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            parsed = json.loads(text[start : end + 1])
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

    return {}
