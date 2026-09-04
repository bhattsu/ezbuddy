"""Map document analysis output onto workflow question field names."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.adapters.llm.bedrock import Bedrock
from app.agents.utils.json_utils import parse_llm_json
from app.core.prompts.context import format_llm_prompt
from app.core.prompts.analysis_prefill import ANALYSIS_PREFILL_PROMPT

logger = logging.getLogger(__name__)


class AnalysisPrefillOutput(BaseModel):
    answers_update: Dict[str, Any] = Field(default_factory=dict)


def _flatten(obj: Any, prefix: str = "") -> Dict[str, Any]:
    flat: Dict[str, Any] = {}
    if isinstance(obj, dict):
        for key, value in obj.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(value, dict):
                flat.update(_flatten(value, path))
            elif value not in (None, "", [], {}):
                flat[path] = value
                flat[str(key)] = value
    return flat


def _normalize_key(key: str) -> str:
    return "".join(ch for ch in str(key).lower() if ch.isalnum())


def heuristic_map_analysis_to_answers(
    workflow_questions: List[Dict[str, Any]],
    analyses: List[Dict[str, Any]],
    existing_answers: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Deterministic mapping: exact and case-insensitive field_name matches."""
    existing = existing_answers or {}
    allowed = {q["field_name"] for q in workflow_questions if q.get("field_name")}
    allowed_norm = {_normalize_key(name): name for name in allowed}

    pooled: Dict[str, Any] = {}
    for analysis in analyses:
        extracted = analysis.get("extracted_fields") or {}
        if isinstance(extracted, dict):
            pooled.update(extracted)
        user_details = analysis.get("user_details") or {}
        if isinstance(user_details, dict):
            pooled.update(_flatten(user_details))

    mapped: Dict[str, Any] = {}
    for key, value in pooled.items():
        if value in (None, "", [], {}):
            continue
        if key in allowed and key not in existing:
            mapped[key] = value
            continue
        norm = _normalize_key(key)
        field = allowed_norm.get(norm)
        if field and field not in existing and field not in mapped:
            mapped[field] = value
    return mapped


async def map_analyses_to_answers(
    bedrock: Optional[Bedrock],
    workflow_questions: List[Dict[str, Any]],
    analyses: List[Dict[str, Any]],
    existing_answers: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Heuristic map plus optional LLM fill for remaining unmatched fields."""
    existing = dict(existing_answers or {})
    mapped = heuristic_map_analysis_to_answers(
        workflow_questions, analyses, existing
    )
    if not bedrock or not workflow_questions or not analyses:
        return mapped

    remaining = [
        q
        for q in workflow_questions
        if q.get("field_name")
        and q["field_name"] not in existing
        and q["field_name"] not in mapped
    ]
    if not remaining:
        return mapped

    prompt = format_llm_prompt(
        ANALYSIS_PREFILL_PROMPT,
        workflow_questions_json=json.dumps(remaining, default=str),
        collected_answers_json=json.dumps({**existing, **mapped}, default=str),
        analyses_json=json.dumps(analyses, default=str),
    )
    try:
        result = await bedrock.invoke_structured_prompt(prompt, AnalysisPrefillOutput)
        llm_update = result.answers_update or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("Prefill LLM mapping failed, using heuristic only: %s", exc)
        try:
            body = Bedrock.build_text_prompt_body(prompt)
            raw = await bedrock.invoke_prompt_with_timeout(body)
            parsed = parse_llm_json(raw)
            llm_update = parsed.get("answers_update") or {}
        except Exception as inner:  # noqa: BLE001
            logger.warning("Prefill LLM fallback failed: %s", inner)
            return mapped

    allowed = {q["field_name"] for q in workflow_questions if q.get("field_name")}
    for key, value in llm_update.items():
        if key not in allowed or key in existing or key in mapped:
            continue
        if value in (None, "", [], {}):
            continue
        mapped[key] = value
    return mapped
