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


@dataclass
class GenerateDocumentsValidationResult:
    """Validation of a generate_documents payload against stored field_mapping."""

    is_valid: bool
    expected_form_data_keys: List[str] = field(default_factory=list)
    missing_form_data_keys: List[str] = field(default_factory=list)
    unexpected_form_data_keys: List[str] = field(default_factory=list)
    missing_envelope_keys: List[str] = field(default_factory=list)
    empty_required_envelope_keys: List[str] = field(default_factory=list)
    semantic_errors: List[str] = field(default_factory=list)


_JOIN_RE = re.compile(
    r'join\s*\(\s*["\'][^"\']*["\']\s*,\s*(.+)\)\s*$',
    re.IGNORECASE,
)
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_YES_VALUES = frozenset({"yes", "y", "true", "1"})
_NO_VALUES = frozenset({"no", "n", "false", "0"})
_ENVELOPE_META_KEYS = ("id", "state", "jurisdiction", "version")
_COUNTY_COURT_TARGETS = frozenset({"_COUNTY_COURT", "_COUNTY"})
_AUTO_FILLED_DOLLAR_KEYS = frozenset({"$yesterday"})
_DATE_RE = re.compile(r"^\d{2}/\d{2}/\d{4}$")
_NUMERIC_CODE_RE = re.compile(r"^\d+$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


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
    if not key:
        return False
    if key.startswith("$"):
        return key not in _AUTO_FILLED_DOLLAR_KEYS
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


def resolve_envelope_meta(
    sample_input: Any,
    *,
    field_mapping_envelope: Optional[Dict[str, Any]] = None,
    selections: Optional[Dict[str, Any]] = None,
) -> Dict[str, str]:
    """Resolve id/state/jurisdiction/version; sample_input is authoritative when set."""
    sample = parse_sample_input(sample_input) or {}
    envelope = field_mapping_envelope or {}
    selections = selections or {}
    result: Dict[str, str] = {}

    for key in _ENVELOPE_META_KEYS:
        sample_value = _stringify(sample.get(key))
        if sample_value:
            result[key] = sample_value
            continue

        mapping_value = _stringify(envelope.get(key))
        if mapping_value:
            result[key] = (
                format_generate_documents_version(mapping_value)
                if key == "version"
                else mapping_value
            )
            continue

        if key == "id":
            result[key] = (
                _stringify(selections.get("template_code"))
                or _stringify(selections.get("doc_type"))
                or _stringify(selections.get("document_type_code"))
            )
        elif key == "state":
            result[key] = _stringify(selections.get("state_code")).upper()
        elif key == "jurisdiction":
            result[key] = _stringify(selections.get("jurisdiction_code"))
        elif key == "version":
            result[key] = format_generate_documents_version(
                selections.get("template_version"),
                selections.get("version"),
                s3_key=str(selections.get("s3_key") or ""),
            )
        else:
            result[key] = ""

    return result


def materialize_field_mapping(
    field_mapping: str,
    *,
    mapped_fields: Dict[str, Any],
    selections: Optional[Dict[str, Any]] = None,
    collected_answers: Optional[Dict[str, Any]] = None,
    sample_input: Any = None,
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
        meta = resolve_envelope_meta(
            sample_input,
            field_mapping_envelope=output,
            selections=selections,
        )
        output.update(meta)
        mapped_form = dict(output.get("form_data") or {})
        for key in list(mapped_form.keys()):
            mapped_form[key] = form_data.get(key, "") or _special_mapping_value(
                key, selections, answers
            )
        output["form_data"] = normalize_mapped_fields(
            mapped_form,
            sample_input=sample_input,
            selections=selections,
        )
        return output

    meta = resolve_envelope_meta(
        sample_input,
        selections=selections,
    )
    return {
        **meta,
        "form_data": normalize_mapped_fields(
            form_data,
            sample_input=sample_input,
            selections=selections,
        ),
    }


def find_missing_mapping_targets(
    fields: Dict[str, Any], targets: Iterable[str]
) -> List[str]:
    """Return configured TARGET names that are absent from the mapped JSON."""
    present = set(fields or {})
    return [target for target in targets if target not in present]


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, dict)):
        return json.dumps(value, default=str)
    return str(value).strip()


def parse_sample_input(sample_input: Any) -> Optional[Dict[str, Any]]:
    """Parse document_templates.sample_input JSON envelope."""
    if isinstance(sample_input, dict):
        return sample_input if isinstance(sample_input.get("form_data"), dict) else None
    text = str(sample_input or "").strip()
    if not text:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) and isinstance(data.get("form_data"), dict) else None


def sample_form_data(sample_input: Any) -> Dict[str, Any]:
    envelope = parse_sample_input(sample_input)
    if not envelope:
        return {}
    form_data = envelope.get("form_data")
    return dict(form_data) if isinstance(form_data, dict) else {}


def classify_sample_form_value(value: Any) -> str:
    text = _stringify(value)
    lower = text.lower()
    if lower in {"yes", "no"}:
        return "boolean"
    if _NUMERIC_CODE_RE.fullmatch(text):
        return "numeric_code"
    if _DATE_RE.fullmatch(text):
        return "date"
    if _EMAIL_RE.fullmatch(text):
        return "email"
    return "text"


def parse_county_court_from_selections(selections: Dict[str, Any]) -> str:
    """Derive county name from session jurisdiction selections, not yes/no answers."""
    display = str(
        selections.get("jurisdiction_name")
        or selections.get("jurisdiction_display")
        or selections.get("db_jurisdiction_name")
        or ""
    ).strip()
    if display:
        county = display.split(" - ", 1)[0].replace(" County", "").strip()
        if county and county.lower() not in _YES_VALUES | _NO_VALUES:
            return county
    code = str(selections.get("jurisdiction_code") or "").strip()
    if code:
        token = code.split(":", 1)[0].strip()
        if token and token.lower() not in _YES_VALUES | _NO_VALUES:
            return token.replace("_", " ").title()
    return ""


def _looks_like_invalid_county(value: str) -> bool:
    text = _stringify(value).lower()
    if not text:
        return True
    if text in _YES_VALUES | _NO_VALUES:
        return True
    if ":" in text:
        return True
    return False


def normalize_to_yes_no(value: Any) -> str:
    """Map user prose or mixed answers onto yes/no tokens."""
    text = _stringify(value)
    lower = text.lower()
    if lower in _YES_VALUES:
        return "yes"
    if lower in _NO_VALUES:
        return "no"
    if not text:
        return ""
    negative_markers = (
        "do not",
        "don't",
        "dont",
        "not ask",
        "i am not",
        "i do not",
        "without",
        "not have",
        "don't have",
        "do not have",
        "no protective",
        "citation of service",
        "issue a citation",
        "ask the clerk to issue",
        "provide legal notice",
        "my spouse lives",
    )
    positive_markers = (
        "will sign",
        "waiver of service",
        "waiver)",
        "have lived",
        "i have lived",
        "90 days",
        "asking the court to change",
        "do not send a sheriff",
    )
    if any(marker in lower for marker in negative_markers):
        return "no"
    if any(marker in lower for marker in positive_markers):
        return "yes"
    if " not " in lower or lower.startswith("not "):
        return "no"
    return text


def normalize_numeric_code(value: Any, sample_value: str = "") -> str:
    text = _stringify(value)
    if _NUMERIC_CODE_RE.fullmatch(text):
        return text
    lower = text.lower()
    if any(
        phrase in lower
        for phrase in ("90 days", "lived in this county", "last 90 days")
    ):
        sample_text = _stringify(sample_value)
        return sample_text if _NUMERIC_CODE_RE.fullmatch(sample_text) else "1"
    return text


def normalize_mapped_fields(
    fields: Dict[str, str],
    *,
    sample_input: Any = None,
    selections: Optional[Dict[str, Any]] = None,
) -> Dict[str, str]:
    """Apply county derivation, boolean normalization, and special auto-fill keys."""
    selections = selections or {}
    sample = sample_form_data(sample_input)
    county = parse_county_court_from_selections(selections)
    normalized = {key: _stringify(value) for key, value in (fields or {}).items()}

    for target in _COUNTY_COURT_TARGETS:
        if target in normalized and county:
            normalized[target] = county
        elif target in normalized and _looks_like_invalid_county(normalized[target]):
            normalized[target] = county or normalized[target]

    for key, sample_value in sample.items():
        if key not in normalized:
            continue
        kind = classify_sample_form_value(sample_value)
        current = normalized[key]
        if kind == "boolean":
            normalized[key] = normalize_to_yes_no(current)
        elif kind == "numeric_code":
            normalized[key] = normalize_numeric_code(current, _stringify(sample_value))

    for key in list(normalized.keys()):
        if str(key).startswith("$") and not normalized[key]:
            normalized[key] = _special_mapping_value(key, selections, {})

    return normalized


def validate_semantic_form_data(
    form_data: Dict[str, Any],
    *,
    sample_input: Any = None,
    selections: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """Return human-readable errors when values violate sample_input domain rules."""
    selections = selections or {}
    sample = sample_form_data(sample_input)
    if not sample:
        return []

    errors: List[str] = []
    county = parse_county_court_from_selections(selections)
    for target in _COUNTY_COURT_TARGETS:
        if target not in form_data:
            continue
        actual = _stringify(form_data.get(target))
        if _looks_like_invalid_county(actual):
            hint = county or "a county name such as Harris"
            errors.append(f"{target} must be {hint}, got {actual!r}")

    for key, sample_value in sample.items():
        actual = _stringify(form_data.get(key))
        kind = classify_sample_form_value(sample_value)
        if kind == "boolean" and actual:
            lower = actual.lower()
            if lower not in {"yes", "no"}:
                errors.append(
                    f"{key} must be 'yes' or 'no' (sample: {sample_value!r}), "
                    f"got {actual[:80]!r}"
                )
        elif kind == "numeric_code" and actual:
            if not _NUMERIC_CODE_RE.fullmatch(actual):
                errors.append(
                    f"{key} must be a numeric code like {sample_value!r}, got {actual[:80]!r}"
                )
        elif kind == "date" and actual and not _DATE_RE.fullmatch(actual):
            errors.append(
                f"{key} must be MM/DD/YYYY (sample: {sample_value!r}), got {actual!r}"
            )
        elif kind == "email" and actual and not _EMAIL_RE.fullmatch(actual):
            errors.append(f"{key} must be a valid email address, got {actual!r}")
        elif key == "$email" and not actual:
            errors.append("$email is required for document generation")
        elif key == "$yesterday" and not actual:
            errors.append("$yesterday is required for document generation")

    return errors


def validate_generate_documents_payload(
    field_mapping: str,
    payload: Dict[str, Any],
    *,
    require_non_empty_meta: bool = True,
    sample_input: Any = None,
    selections: Optional[Dict[str, Any]] = None,
) -> GenerateDocumentsValidationResult:
    """
    Verify payload matches the structure defined in document_templates.field_mapping.

    Ensures top-level envelope keys and every configured form_data key are present
    before calling POST /ai/{state}/generate_documents.
    """
    spec = parse_field_mapping_spec(field_mapping)
    expected_targets = list(dict.fromkeys(spec.targets))
    expected_top = {"id", "state", "jurisdiction", "version", "form_data"}

    missing_envelope = sorted(expected_top - set(payload or {}))
    empty_required: List[str] = []
    if require_non_empty_meta:
        for key in ("id", "state", "jurisdiction", "version"):
            if key in (payload or {}) and not _stringify(payload.get(key)):
                empty_required.append(key)

    form_data = payload.get("form_data") if isinstance(payload, dict) else None
    if not isinstance(form_data, dict):
        return GenerateDocumentsValidationResult(
            is_valid=False,
            expected_form_data_keys=expected_targets,
            missing_envelope_keys=missing_envelope or ["form_data"],
            empty_required_envelope_keys=empty_required,
        )

    missing_form = find_missing_mapping_targets(form_data, expected_targets)
    expected_set = set(expected_targets)
    unexpected_form = sorted(
        key for key in form_data.keys() if key not in expected_set
    )
    semantic_errors = validate_semantic_form_data(
        form_data,
        sample_input=sample_input,
        selections=selections,
    )
    is_valid = (
        not missing_envelope
        and not missing_form
        and not empty_required
    )
    return GenerateDocumentsValidationResult(
        is_valid=is_valid,
        expected_form_data_keys=expected_targets,
        missing_form_data_keys=missing_form,
        unexpected_form_data_keys=unexpected_form,
        missing_envelope_keys=missing_envelope,
        empty_required_envelope_keys=empty_required,
        semantic_errors=semantic_errors,
    )


def assert_valid_generate_documents_payload(
    field_mapping: str,
    payload: Dict[str, Any],
    *,
    require_non_empty_meta: bool = True,
    sample_input: Any = None,
    selections: Optional[Dict[str, Any]] = None,
) -> GenerateDocumentsValidationResult:
    """Raise FieldMappingValidationError when payload does not match field_mapping."""
    result = validate_generate_documents_payload(
        field_mapping,
        payload,
        require_non_empty_meta=require_non_empty_meta,
        sample_input=sample_input,
        selections=selections,
    )
    if result.is_valid:
        return result

    problems: List[str] = []
    if result.missing_envelope_keys:
        problems.append(
            "missing envelope keys: " + ", ".join(result.missing_envelope_keys)
        )
    if result.empty_required_envelope_keys:
        problems.append(
            "empty required envelope values: "
            + ", ".join(result.empty_required_envelope_keys)
        )
    if result.missing_form_data_keys:
        problems.append(
            "missing form_data keys: " + ", ".join(result.missing_form_data_keys)
        )
    if result.unexpected_form_data_keys:
        problems.append(
            "unexpected form_data keys: "
            + ", ".join(result.unexpected_form_data_keys)
        )
    raise FieldMappingValidationError(
        "generate_documents payload failed field_mapping validation; "
        + "; ".join(problems)
    )


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
        selections: Optional[Dict[str, Any]] = None,
        sample_input: Any = None,
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

        sample_envelope = parse_sample_input(sample_input) or {}
        sample_input_json = json.dumps(sample_envelope, indent=2, default=str)[:20000]
        if not sample_envelope:
            sample_input_json = (
                "(no sample_input configured for this template — infer types from "
                "field_mapping and use yes/no for boolean answers)"
            )

        prompt = format_llm_prompt(
            FIELD_MAPPING_PROMPT,
            field_mapping=mapping[:20000],
            sample_input_json=sample_input_json,
            workflow_questions_json=json.dumps(
                questions, default=str
            )[:20000],
            collected_answers_json=json.dumps(
                answers_with_aliases, default=str
            )[:20000],
            filled_json=json.dumps(filled_fields or {}, default=str)[:20000],
        )

        fallback = self._deterministic_fallback(
            mapping,
            answers_with_aliases,
            filled_fields,
            selections=selections,
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
        validated.fields = normalize_mapped_fields(
            validated.fields,
            sample_input=sample_input,
            selections=selections or {},
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
        selections: Optional[Dict[str, Any]] = None,
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
        county = parse_county_court_from_selections(selections or {})
        for target in _COUNTY_COURT_TARGETS:
            if county:
                result[target] = county
        return result
