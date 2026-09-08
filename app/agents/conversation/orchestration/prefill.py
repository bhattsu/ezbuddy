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


def _question_aliases(question: Dict[str, Any]) -> List[str]:
    names = [
        question.get("field_name"),
        question.get("mapping_source"),
        question.get("pdf_field"),
        question.get("field_label"),
        question.get("question"),
        question.get("field"),
    ]
    return [str(name).strip() for name in names if str(name or "").strip()]


def _key_variants(key: str) -> List[str]:
    text = str(key or "").strip()
    if not text:
        return []
    variants = [text]
    for sep in (".", "/", ":"):
        if sep in text:
            parts = [part for part in text.split(sep) if part]
            if parts:
                variants.append(parts[-1])
                variants.append(" ".join(parts))
                variants.append("".join(parts))
            break
    return variants


def heuristic_map_analysis_to_answers(
    workflow_questions: List[Dict[str, Any]],
    analyses: List[Dict[str, Any]],
    existing_answers: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Match filled analysis values onto workflow field names, including aliases."""
    existing = existing_answers or {}
    allowed_norm: Dict[str, str] = {}
    for question in workflow_questions:
        field_name = str(question.get("field_name") or "").strip()
        if not field_name:
            continue
        for alias in _question_aliases(question):
            allowed_norm.setdefault(_normalize_key(alias), field_name)

    pooled: Dict[str, Any] = {}
    for analysis in analyses:
        extracted = analysis.get("extracted_fields") or {}
        if isinstance(extracted, dict):
            for key, value in extracted.items():
                if value not in (None, "", [], {}):
                    pooled[str(key)] = value
        user_details = analysis.get("user_details") or {}
        if isinstance(user_details, dict):
            pooled.update(_flatten(user_details))

    mapped: Dict[str, Any] = {}
    for key, value in pooled.items():
        if value in (None, "", [], {}):
            continue
        for variant in _key_variants(key):
            field = allowed_norm.get(_normalize_key(variant))
            if field and field not in existing and field not in mapped:
                mapped[field] = value
                break
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
