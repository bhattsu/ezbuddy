"""Document generation 2.0: extracted PDF fields + user answers → filled JSON."""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any, Dict, Iterable, List, Optional

from app.adapters.llm.bedrock import Bedrock, get_bedrock
from app.agents.utils.json_utils import parse_llm_json
from app.api.schemas.document_generation_v2 import (
    ExtractedFormField,
    GenerateV2LLMOutput,
    GenerateV2Response,
)
from app.core.prompts.context import format_llm_prompt
from app.core.prompts.document_generation_v2 import DOCUMENT_GENERATION_V2_PROMPT

logger = logging.getLogger(__name__)


def _slug(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").lower()).strip("_")


def _normalize(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _field_key(row: Dict[str, Any]) -> str:
    return str(row.get("field") or row.get("question") or row.get("field_label") or "").strip()


def normalize_extracted_fields(rows: Iterable[Any]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for row in rows or []:
        if isinstance(row, ExtractedFormField):
            data = row.model_dump()
        elif isinstance(row, dict):
            data = dict(row)
        else:
            data = {
                "field": getattr(row, "field", None),
                "question": getattr(row, "question", None),
                "page": getattr(row, "page", None),
                "answer": getattr(row, "answer", ""),
            }
        if not data.get("field"):
            data["field"] = data.get("pdf_field") or data.get("field_label") or data.get("question")
        if not data.get("question"):
            data["question"] = data.get("field_label") or data.get("field")
        items.append(data)
    return items


def match_answer_for_field(row: Dict[str, Any], answers: Dict[str, Any]) -> str:
    """Find the user value for one PDF field without inventing content."""
    direct = _stringify(row.get("answer"))
    if not answers:
        return direct
    keys = [
        row.get("field"),
        row.get("pdf_field"),
        row.get("question"),
        row.get("field_label"),
        row.get("field_name"),
        _slug(row.get("field")),
        _slug(row.get("question") or row.get("field_label")),
    ]
    for key in keys:
        if key in (None, ""):
            continue
        if key in answers and _stringify(answers[key]):
            return _stringify(answers[key])
    wanted = {
        _normalize(row.get("field")),
        _normalize(row.get("question") or row.get("field_label")),
        _slug(row.get("field")),
        _slug(row.get("question") or row.get("field_label")),
    }
    wanted.discard("")
    for answer_key, value in answers.items():
        text = _stringify(value)
        if not text:
            continue
        if _normalize(answer_key) in wanted or _slug(answer_key) in wanted:
            return text
    return direct


def map_answers_to_pdf_fields(
    extracted_fields: List[Dict[str, Any]], answers: Dict[str, Any]
) -> Dict[str, str]:
    """Deterministic PDF-label → answer map used when the LLM is unavailable."""
    filled: Dict[str, str] = {}
    for row in extracted_fields:
        key = _field_key(row)
        if not key or key in filled:
            continue
        filled[key] = match_answer_for_field(row, answers)
    return filled


class DocumentGenerationV2Service:
    """Fill extracted court-form keys with user answers and return JSON."""

    def __init__(self, bedrock: Optional[Bedrock] = None):
        self.bedrock = bedrock or get_bedrock()

    async def generate(
        self,
        *,
        extracted_text: str = "",
        extracted_fields: Optional[Iterable[Any]] = None,
        answers: Optional[Dict[str, Any]] = None,
        file_name: str = "",
    ) -> GenerateV2Response:
        fields = normalize_extracted_fields(extracted_fields or [])
        answers = dict(answers or {})
        text = (extracted_text or "").strip()
        if not text:
            text = "\n".join(
                f"[page {row.get('page') or '?'}] {row.get('field') or ''}: "
                f"{row.get('question') or ''}"
                for row in fields
            ) or "(none)"

        fallback = map_answers_to_pdf_fields(fields, answers)
        llm_fields = await self._ask_llm(text, fields, answers)
        merged = dict(fallback)
        if llm_fields:
            allowed = set(fallback) if fallback else set(llm_fields)
            for key, value in llm_fields.items():
                label = str(key or "").strip()
                if not label:
                    continue
                if allowed and label not in allowed:
                    continue
                merged[label] = _stringify(value)

        return GenerateV2Response(
            document_id=str(uuid.uuid4()),
            file_name=file_name or "",
            fields=merged,
            message=(
                f"Filled {sum(1 for value in merged.values() if value)} of "
                f"{len(merged)} PDF fields from user answers."
            ),
        )

    async def _ask_llm(
        self,
        extracted_text: str,
        extracted_fields: List[Dict[str, Any]],
        answers: Dict[str, Any],
    ) -> Dict[str, str]:
        prompt = format_llm_prompt(
            DOCUMENT_GENERATION_V2_PROMPT,
            extracted_text=extracted_text[:20000] or "(none)",
            extracted_fields_json=json.dumps(extracted_fields, default=str)[:20000],
            answers_json=json.dumps(answers, default=str)[:20000],
        )
        try:
            parsed = await self.bedrock.invoke_structured_prompt(
                prompt, GenerateV2LLMOutput
            )
            return {
                str(key).strip(): _stringify(value)
                for key, value in (parsed.fields or {}).items()
                if str(key).strip()
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("Document generation 2.0 structured invoke failed: %s", exc)

        try:
            body = {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 8192,
                "temperature": 0,
                "messages": [{"role": "user", "content": prompt}],
            }
            raw = await self.bedrock.invoke_prompt_with_timeout(body)
            parsed = parse_llm_json(raw)
            fields = parsed.get("fields")
            if isinstance(fields, dict):
                return {
                    str(key).strip(): _stringify(value)
                    for key, value in fields.items()
                    if str(key).strip()
                }
            if parsed and all(isinstance(k, str) for k in parsed.keys()):
                if "fields" not in parsed:
                    return {
                        str(key).strip(): _stringify(value)
                        for key, value in parsed.items()
                        if str(key).strip()
                    }
        except Exception as exc:  # noqa: BLE001
            logger.warning("Document generation 2.0 prompt fallback failed: %s", exc)
        return {}
