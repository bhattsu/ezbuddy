"""Map collected workflow answers onto document_templates.field_mapping targets via LLM."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
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
_YES_VALUES = frozenset({"yes", "y", "true", "1"})
_NO_VALUES = frozenset({"no", "n", "false", "0"})
_ENVELOPE_META_KEYS = ("id", "state", "jurisdiction", "version")


@dataclass
class FieldMappingSpec:
    """Parsed document_templates.field_mapping (pipe list or generate_documents envelope)."""

    kind: str
    raw: str
    entries: List[tuple[str, str]] = field(default_factory=list)
    envelope: Optional[Dict[str, Any]] = None

    @property
    def targets(self) -> List[str]:
        return [target for target, _ in self.entries]


def _parse_pipe_entries(text: str) -> List[tuple[str, str]]:
    starts = list(
        re.finditer(
            r"(?:^|[|,])\s*([A-Za-z_][A-Za-z0-9_]*)\s*=",
            text,
        )
    )
    entries: List[tuple[str, str]] = []
    for index, match in enumerate(starts):
        expression_start = match.end()
        expression_end = starts[index + 1].start() if index + 1 < len(starts) else len(text)
        expression = text[expression_start:expression_end].strip().rstrip("|,").strip()
        target = match.group(1).strip()
        if target and expression:
            entries.append((target, expression))
    return entries


def parse_field_mapping_spec(field_mapping: str) -> FieldMappingSpec:
    """Parse pipe mappings or a generate_documents JSON envelope."""
    text = str(field_mapping or "").strip()
    if text.startswith("{") and "form_data" in text:
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict) and isinstance(data.get("form_data"), dict):
            form_data = data["form_data"]
            entries = [(str(key), str(key)) for key in form_data.keys()]
            return FieldMappingSpec(
                kind="envelope",
                raw=text,
                entries=entries,
                envelope=data,
            )
    return FieldMappingSpec(
        kind="pipe",
        raw=text,
        entries=_parse_pipe_entries(text),
    )


def parse_field_mapping_entries(field_mapping: str) -> List[tuple[str, str]]:
    """
    Parse TARGET=EXPRESSION entries.

    Pipe is the canonical separator, but existing rows may use commas between
    mappings. Commas inside join(...) are preserved. JSON envelopes use each
    form_data key as both target and source.
    """
    return parse_field_mapping_spec(field_mapping).entries


def parse_field_mapping_targets(field_mapping: str) -> List[str]:
    """Extract configured TARGET field names in their original order."""
    return parse_field_mapping_spec(field_mapping).targets


def is_askable_mapping_source(name: str, *, kind: str = "pipe") -> bool:
    key = str(name or "").strip()
    if not key or key.startswith("$"):
        return False
    if kind == "envelope" and key.lower() in _ENVELOPE_META_KEYS:
        return False
    return True


def parse_field_mapping_sources(field_mapping: str) -> List[str]:
    """Extract source field names that should be asked of the user."""
    spec = parse_field_mapping_spec(field_mapping)
    sources: List[str] = []
    seen: set[str] = set()
    for _, expr in spec.entries:
        join_match = _JOIN_RE.match(expr)
        parts = (
            [part.strip() for part in join_match.group(1).split(",")]
            if join_match
            else [expr]
        )
        for part in parts:
            if not part or part in seen or not is_askable_mapping_source(part, kind=spec.kind):
                continue
            if spec.kind == "pipe" and not _IDENT_RE.match(part):
                continue
            seen.add(part)
            sources.append(part)
    return sources


def mapping_lookup_value(answers: Dict[str, Any], key: str) -> Any:
    if key in answers:
        return answers.get(key)
    wanted = re.sub(r"[^a-z0-9]+", "_", str(key or "").lower()).strip("_")
    for existing, value in (answers or {}).items():
        if re.sub(r"[^a-z0-9]+", "_", str(existing).lower()).strip("_") == wanted:
            return value
    return None


def mapping_value_matches(actual: Any, expected: Any) -> bool:
    want = str(expected or "").strip().lower()
    got = str(actual or "").strip().lower()
    if want in _YES_VALUES:
        return got in _YES_VALUES
    if want in _NO_VALUES:
        return got in _NO_VALUES
    return got == want


def is_mapping_question_visible(
    question: Dict[str, Any], answers: Dict[str, Any]
) -> bool:
    cond = question.get("visibility_condition")
    if not cond:
        return True
    if isinstance(cond, dict):
        for key, expected in cond.items():
            if not mapping_value_matches(mapping_lookup_value(answers, key), expected):
                return False
        return True
    return True


_S3_VERSION_RE = re.compile(r"/v(\d+(?:\.\d+)?)/", re.IGNORECASE)
_DOTTED_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)", re.IGNORECASE)
_WHOLE_VERSION_RE = re.compile(r"^v?(\d+)$", re.IGNORECASE)


def format_generate_documents_version(*candidates: Any, s3_key: str = "") -> str:
    """Turn a stored template version into the generate_documents version string.

    Uses the first non-blank source. Integer 1 or path /v1/ becomes 1.0.
    Does not invent a version when no source exists.
    """
    for value in candidates:
        text = _stringify(value)
        if not text:
            continue
        dotted = _DOTTED_VERSION_RE.match(text)
        if dotted:
            return f"{int(dotted.group(1))}.{dotted.group(2)}"
        whole = _WHOLE_VERSION_RE.match(text)
        if whole:
            return f"{int(whole.group(1))}.0"
        return text
    s3_match = _S3_VERSION_RE.search(str(s3_key or ""))
    if not s3_match:
        return ""
    part = s3_match.group(1)
    return part if "." in part else f"{int(part)}.0"


def _special_mapping_value(
    key: str, selections: Dict[str, Any], answers: Dict[str, Any]
) -> str:
    if key == "$yesterday":
        return (date.today() - timedelta(days=1)).strftime("%m/%d/%Y")
    if key == "$email":
        return _stringify(
            mapping_lookup_value(answers, "$email")
            or mapping_lookup_value(answers, "email")
            or selections.get("email")
            or selections.get("user_email")
            or ""
        )
    return ""


def materialize_field_mapping(
    field_mapping: str,
    *,
    mapped_fields: Dict[str, Any],
    selections: Optional[Dict[str, Any]] = None,
    collected_answers: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Return JSON with the exact field_mapping shape and filled values."""
    spec = parse_field_mapping_spec(field_mapping)
    selections = selections or {}
    answers = collected_answers or {}
    values = {
        str(key): _stringify(value) for key, value in (mapped_fields or {}).items()
    }
    for target, _expr in spec.entries:
        if target.startswith("$") and not values.get(target):
            values[target] = _special_mapping_value(target, selections, answers)

    form_data = {target: values.get(target, "") for target, _expr in spec.entries}
    for key, value in list(form_data.items()):
        if key.startswith("$") and not value:
            form_data[key] = _special_mapping_value(key, selections, answers)

    if spec.kind == "envelope" and isinstance(spec.envelope, dict):
        output = json.loads(json.dumps(spec.envelope))
        # Keep envelope keys from the stored mapping. Fill blanks from the
        # current filing session only — never invent template ids or versions.
        if not _stringify(output.get("id")):
            output["id"] = (
                _stringify(selections.get("template_code"))
                or _stringify(selections.get("doc_type"))
                or _stringify(selections.get("document_type_code"))
            )
        if not _stringify(output.get("state")):
            output["state"] = _stringify(selections.get("state_code")).upper()
        if not _stringify(output.get("jurisdiction")):
            output["jurisdiction"] = _stringify(
                selections.get("jurisdiction_code")
            )
        output["version"] = format_generate_documents_version(
            output.get("version") if _stringify(output.get("version")) else None,
            selections.get("template_version"),
            selections.get("version"),
            s3_key=str(selections.get("s3_key") or ""),
        )
        mapped_form = dict(output.get("form_data") or {})
        for key in list(mapped_form.keys()):
            mapped_form[key] = form_data.get(key, "") or _special_mapping_value(
                key, selections, answers
            )
        output["form_data"] = mapped_form
        return output

    return {
        "id": (
            _stringify(selections.get("template_code"))
            or _stringify(selections.get("doc_type"))
            or _stringify(selections.get("document_type_code"))
        ),
        "state": _stringify(selections.get("state_code")).upper(),
        "jurisdiction": _stringify(selections.get("jurisdiction_code")),
        "version": format_generate_documents_version(
            selections.get("template_version"),
            selections.get("version"),
            s3_key=str(selections.get("s3_key") or ""),
        ),
        "form_data": form_data,
    }


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

        # Explicit direct/join mappings are deterministic. Prefer their value
        # over LLM output so a user's response cannot be rewritten.
        if fallback_value:
            result[target] = fallback_value
            if not llm_value:
                backfilled.append(target)
        elif llm_value:
            result[target] = llm_value
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

        questions = list(workflow_questions or [])
        targets = parse_field_mapping_targets(mapping)
        answers_with_aliases = dict(collected_answers or {})
        for question in questions:
            if isinstance(question, dict):
                field_name = str(question.get("field_name") or "").strip()
                aliases = (
                    question.get("mapping_source"),
                    question.get("pdf_field"),
                    question.get("field"),
                )
            else:
                field_name = str(getattr(question, "field_name", "") or "").strip()
                aliases = (
                    getattr(question, "mapping_source", None),
                    getattr(question, "pdf_field", None),
                    getattr(question, "field", None),
                )
            value = answers_with_aliases.get(field_name)
            if value in (None, ""):
                continue
            for alias in aliases:
                alias_name = str(alias or "").strip()
                if alias_name:
                    answers_with_aliases.setdefault(alias_name, value)

        prompt = format_llm_prompt(
            FIELD_MAPPING_PROMPT,
            field_mapping=mapping[:20000],
            workflow_questions_json=json.dumps(
                questions, default=str
            )[:20000],
            collected_answers_json=json.dumps(
                answers_with_aliases, default=str
            )[:20000],
            filled_json=json.dumps(filled_fields or {}, default=str)[:20000],
        )

        fallback = self._deterministic_fallback(
            mapping, answers_with_aliases, filled_fields
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
        for target, source_expr in parse_field_mapping_entries(field_mapping):
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
