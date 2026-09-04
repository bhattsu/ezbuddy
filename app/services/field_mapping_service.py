"""Map collected workflow answers onto document_templates.field_mapping targets via LLM."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

from pydantic import BaseModel, Field

from app.adapters.llm.bedrock import Bedrock, get_bedrock
from app.agents.utils.json_utils import parse_llm_json
from app.core.prompts.context import format_llm_prompt
from app.core.prompts.field_mapping import FIELD_MAPPING_PROMPT

logger = logging.getLogger(__name__)


class FieldMappingLLMOutput(BaseModel):
    fields: Dict[str, Any] = Field(default_factory=dict)


class FieldMappingValidationError(ValueError):
    """Raised when mapped JSON is missing required field_mapping targets."""


@dataclass
class FieldMappingResult:
    """Mapped template fields plus validation metadata."""

    fields: Dict[str, str] = field(default_factory=dict)
    expected_targets: List[str] = field(default_factory=list)
    missing_from_llm: List[str] = field(default_factory=list)
    backfilled_targets: List[str] = field(default_factory=list)
    is_complete: bool = True

    @property
    def empty_targets(self) -> List[str]:
        return [key for key, value in self.fields.items() if not str(value or "").strip()]


_JOIN_RE = re.compile(
    r'join\s*\(\s*["\'][^"\']*["\']\s*,\s*(.+)\)\s*$',
    re.IGNORECASE,
)
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def parse_field_mapping_targets(field_mapping: str) -> List[str]:
    """Extract TARGET field names from a pipe-separated field_mapping string."""
    targets: List[str] = []
    for segment in str(field_mapping or "").split("|"):
        piece = segment.strip()
        if not piece or "=" not in piece:
            continue
        target, _ = piece.split("=", 1)
        target = target.strip()
        if target:
            targets.append(target)
    return targets


def parse_field_mapping_sources(field_mapping: str) -> List[str]:
    """Extract source field names referenced on the right-hand side of mappings."""
    sources: List[str] = []
    seen: set[str] = set()
    for segment in str(field_mapping or "").split("|"):
        piece = segment.strip()
        if not piece or "=" not in piece:
            continue
        _, expr = piece.split("=", 1)
        expr = expr.strip()
        join_match = _JOIN_RE.match(expr)
        parts = (
            [part.strip() for part in join_match.group(1).split(",")]
            if join_match
            else [expr]
        )
        for part in parts:
            if not part or not _IDENT_RE.match(part) or part in seen:
                continue
            seen.add(part)
            sources.append(part)
    return sources


def find_missing_mapping_targets(
    fields: Dict[str, Any], targets: Iterable[str]
) -> List[str]:
    """Return configured TARGET names that are absent from the mapped JSON."""
    present = set(fields or {})
    return [target for target in targets if target not in present]


def validate_and_complete_mapping(
    *,
    fields: Dict[str, str],
    targets: List[str],
    fallback: Dict[str, str],
) -> FieldMappingResult:
    """Ensure every field_mapping target exists; backfill gaps from fallback."""
    ordered_targets = list(dict.fromkeys(targets))
    result: Dict[str, str] = {}
    missing_from_llm: List[str] = []
    backfilled: List[str] = []

    for target in ordered_targets:
        llm_value = _stringify(fields.get(target, "")) if target in fields else ""
        fallback_value = _stringify(fallback.get(target, ""))
        if target not in fields:
            missing_from_llm.append(target)
        elif not llm_value:
            missing_from_llm.append(target)

        if llm_value:
            result[target] = llm_value
        elif fallback_value:
            result[target] = fallback_value
            backfilled.append(target)
        else:
            result[target] = llm_value

    still_missing = find_missing_mapping_targets(result, ordered_targets)
    is_complete = not still_missing
    if still_missing:
        logger.error(
            "Field mapping validation failed; missing targets: %s",
            ", ".join(still_missing),
        )

    extras = {
        key: _stringify(value)
        for key, value in fields.items()
        if key not in set(ordered_targets) and str(key).strip()
    }
    if extras:
        logger.warning(
            "Field mapping dropped unexpected keys: %s",
            ", ".join(sorted(extras)),
        )

    return FieldMappingResult(
        fields=result,
        expected_targets=ordered_targets,
        missing_from_llm=missing_from_llm,
        backfilled_targets=backfilled,
        is_complete=is_complete,
    )


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, dict)):
        return json.dumps(value, default=str)
    return str(value).strip()


class FieldMappingService:
    """Apply document_templates.field_mapping to produce template-ready JSON."""

    def __init__(self, bedrock: Optional[Bedrock] = None):
        self.bedrock = bedrock or get_bedrock()

    async def map_fields(
        self,
        *,
        field_mapping: str,
        workflow_questions: Iterable[Any],
        collected_answers: Dict[str, Any],
        filled_fields: Dict[str, Any],
    ) -> FieldMappingResult:
        mapping = str(field_mapping or "").strip()
        if not mapping:
            return FieldMappingResult()

        targets = parse_field_mapping_targets(mapping)
        prompt = format_llm_prompt(
            FIELD_MAPPING_PROMPT,
            field_mapping=mapping[:20000],
            workflow_questions_json=json.dumps(
                list(workflow_questions or []), default=str
            )[:20000],
            collected_answers_json=json.dumps(
                collected_answers or {}, default=str
            )[:20000],
            filled_json=json.dumps(filled_fields or {}, default=str)[:20000],
        )

        fallback = self._deterministic_fallback(
            mapping, collected_answers, filled_fields
        )
        llm_fields = await self._ask_llm(prompt)
        if not llm_fields:
            llm_fields = fallback

        llm_result: Dict[str, str] = {}
        allowed = set(targets) if targets else set(llm_fields)
        for key in allowed or llm_fields:
            label = str(key).strip()
            if not label:
                continue
            llm_result[label] = _stringify(llm_fields.get(label, ""))
        for key, value in llm_fields.items():
            label = str(key).strip()
            if not label or label in llm_result:
                continue
            if allowed and label not in allowed:
                continue
            llm_result[label] = _stringify(value)

        validated = validate_and_complete_mapping(
            fields=llm_result,
            targets=targets,
            fallback=fallback,
        )
        if validated.missing_from_llm:
            logger.warning(
                "Field mapping LLM omitted %d target(s); backfilled %d: %s",
                len(validated.missing_from_llm),
                len(validated.backfilled_targets),
                ", ".join(validated.missing_from_llm),
            )
        if targets and not validated.is_complete:
            missing = find_missing_mapping_targets(validated.fields, targets)
            raise FieldMappingValidationError(
                "Mapped JSON is missing required field_mapping targets: "
                + ", ".join(missing)
            )
        return validated

    async def _ask_llm(self, prompt: str) -> Dict[str, Any]:
        try:
            parsed = await self.bedrock.invoke_structured_prompt(
                prompt, FieldMappingLLMOutput
            )
            return dict(parsed.fields or {})
        except Exception as exc:  # noqa: BLE001
            logger.warning("Field mapping structured invoke failed: %s", exc)

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
                return fields
        except Exception as exc:  # noqa: BLE001
            logger.warning("Field mapping prompt fallback failed: %s", exc)
        return {}

    def _deterministic_fallback(
        self,
        field_mapping: str,
        collected_answers: Dict[str, Any],
        filled_fields: Dict[str, Any],
    ) -> Dict[str, str]:
        """Best-effort mapping when the LLM is unavailable."""
        pool = {**filled_fields, **collected_answers}
        pool_norm = {
            re.sub(r"[^a-z0-9]+", "_", str(k).lower()).strip("_"): v
            for k, v in pool.items()
        }
        result: Dict[str, str] = {}
        for segment in field_mapping.split("|"):
            piece = segment.strip()
            if not piece or "=" not in piece:
                continue
            target, source_expr = piece.split("=", 1)
            target = target.strip()
            source_expr = source_expr.strip()
            if not target:
                continue
            join_match = re.match(
                r'join\s*\(\s*"([^"]*)"\s*,\s*(.+)\)\s*$',
                source_expr,
                re.IGNORECASE,
            )
            if join_match:
                sep, fields_csv = join_match.groups()
                parts = [
                    _stringify(pool.get(f.strip()) or pool_norm.get(
                        re.sub(r"[^a-z0-9]+", "_", f.strip().lower()).strip("_"), ""
                    ))
                    for f in fields_csv.split(",")
                    if f.strip()
                ]
                parts = [p for p in parts if p]
                result[target] = sep.join(parts)
            else:
                norm = re.sub(r"[^a-z0-9]+", "_", source_expr.lower()).strip("_")
                result[target] = _stringify(
                    pool.get(source_expr) or pool_norm.get(norm, "")
                )
        return result
