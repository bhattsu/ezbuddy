"""Compact, display, match, and validate API/RDS navigation options."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

MAX_PROMPT_OPTIONS = 60
MAX_LISTED_OPTIONS = 40
_HEAVY_KEYS = frozenset({"raw", "links", "field_mapping"})

DROPDOWN_PHASES = frozenset(
    {
        "selecting_state",
        "existing_selecting_state",
        "selecting_county",
        "selecting_jurisdiction",
        "existing_selecting_jurisdiction",
        "selecting_case_category",
        "selecting_case_type",
        "selecting_case_parties",
        "selecting_filer_type",
        "selecting_filing_code",
        "selecting_doc_type_code",
        "selecting_document_type",
        "selecting_filing_type",
        "existing_search_party",
        "existing_search_date",
        "verifying_court_payment",
    }
)

_PHASE_NOUNS = {
    "selecting_state": "state",
    "existing_selecting_state": "state",
    "selecting_county": "county",
    "selecting_jurisdiction": "court",
    "existing_selecting_jurisdiction": "court",
    "selecting_case_category": "case category",
    "selecting_case_type": "case type",
    "selecting_case_parties": "party type",
    "selecting_filer_type": "filer type",
    "selecting_filing_code": "filing code",
    "selecting_doc_type_code": "court document type",
    "selecting_document_type": "document type",
    "selecting_filing_type": "filing type",
    "existing_search_party": "party",
    "existing_search_date": "case",
    "verifying_court_payment": "payment account",
}


def compact_db_options(
    options: List[Dict[str, Any]], limit: int = MAX_PROMPT_OPTIONS
) -> List[Dict[str, Any]]:
    """Remove raw API payloads and link URLs before sending options to an LLM."""
    return [
        {
            key: value
            for key, value in row.items()
            if key not in _HEAVY_KEYS and not key.endswith("_url")
        }
        for row in options[:limit]
    ]


def _label(row: Dict[str, Any]) -> str:
    return str(
        row.get("label")
        or row.get("name")
        or row.get("state_name")
        or row.get("county_name")
        or row.get("jurisdiction_name")
        or row.get("case_type")
        or row.get("code")
        or ""
    )


def option_label(row: Dict[str, Any]) -> str:
    if row.get("state_name") and row.get("state_code"):
        return f"{row['state_name']} ({row['state_code']})"
    return _label(row)


def build_selection_options_payload(
    phase: str, options: List[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """Structured dropdown payload for WebSocket / API clients."""
    phase_key = str(phase).lower()
    if phase_key not in DROPDOWN_PHASES:
        return None
    items: List[Dict[str, Any]] = []
    for row in options:
        label = option_label(row)
        if not label:
            continue
        code = (
            row.get("code")
            or row.get("state_code")
            or row.get("jurisdiction_code")
            or row.get("case_type_code")
            or row.get("county_name")
        )
        items.append(
            {
                "label": label,
                "value": label,
                "code": str(code) if code else None,
            }
        )
    if not items:
        return None
    noun = _PHASE_NOUNS.get(phase_key, "option")
    return {
        "phase": phase_key,
        "type": "dropdown",
        "prompt": f"Please select the {noun}",
        "total": len(items),
        "options": items,
    }


def _normalize(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def match_option(
    options: List[Dict[str, Any]], text: str
) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    """Resolve an exact or uniquely narrowing free-text option locally."""
    needle = _normalize(text)
    if not needle:
        return None, []
    fields = (
        "name",
        "code",
        "state_name",
        "state_code",
        "county_name",
        "jurisdiction_name",
        "jurisdiction_code",
        "doc_type",
        "document_type_code",
        "case_type",
        "id",
        "last4_digit",
        "card_type",
        "label",
    )
    for row in options:
        if any(_normalize(row.get(field)) == needle for field in fields):
            return row, []
        if _normalize(option_label(row)) == needle:
            return row, []
    candidates = [
        row
        for row in options
        if any(needle in _normalize(row.get(field)) for field in fields)
        or needle in _normalize(option_label(row))
    ]
    return (candidates[0], []) if len(candidates) == 1 else (None, candidates)


def selection_update_for_option(
    phase: str, option: Dict[str, Any]
) -> Dict[str, Any]:
    keys = {
        "selecting_state": "state_code",
        "existing_selecting_state": "state_code",
        "selecting_county": "county_name",
        "selecting_jurisdiction": "jurisdiction_code",
        "existing_selecting_jurisdiction": "jurisdiction_code",
        "selecting_case_category": "case_category_code",
        "selecting_case_type": "case_type_code",
        "selecting_case_parties": "party_type_code",
        "selecting_filer_type": "filer_type",
        "selecting_filing_code": "filing_code",
        "selecting_doc_type_code": "doc_type_code",
        "selecting_document_type": "document_type_code",
        "selecting_filing_type": "filing_type",
        "verifying_court_payment": "court_payment_account_id",
    }
    key = keys.get(str(phase).lower())
    if not key:
        return {}
    value = (
        option.get("code")
        or option.get(key)
        or option.get("state_code")
        or option.get("county_name")
        or option.get("name")
    )
    return {key: value} if value else {}


def format_db_options_summary(
    phase: str, options: List[Dict[str, Any]]
) -> str:
    if not options:
        return "(empty - do NOT invent options)"
    if str(phase).lower() in {
        "greeting",
        "intent_pending",
        "selecting_state",
        "existing_selecting_state",
    }:
        labels = [
            (
                f"{row.get('state_name')} ({row.get('state_code')})"
                if row.get("state_name") and row.get("state_code")
                else _label(row)
            )
            for row in options
        ]
        return "Available states (use ONLY these values):\n" + "\n".join(
            f"- {label}" for label in labels[:MAX_PROMPT_OPTIONS]
        )
    labels = [_label(row) for row in options if _label(row)]
    shown = labels[:MAX_PROMPT_OPTIONS]
    prefix = f"Available options ({len(labels)} total; use only these values):"
    return prefix + "\n" + "\n".join(f"- {label}" for label in shown)


def build_phase_selection_message(
    phase: str,
    selections: Dict[str, Any],
    options: List[Dict[str, Any]],
) -> Optional[str]:
    phase_key = str(phase).lower()
    labels = [option_label(row) for row in options if option_label(row)]
    noun = _PHASE_NOUNS.get(phase_key)
    if not noun:
        return None
    if not labels:
        topic = str(selections.get("case_topic") or "").strip()
        if topic and phase_key in {
            "selecting_jurisdiction",
            "selecting_case_category",
            "selecting_case_type",
        }:
            return (
                f"I couldn't find any {noun} options for {topic} in the selected state. "
                "Please describe the case type in different words, or mention your county."
            )
        if phase_key == "selecting_county":
            return "There are no counties available for the selected state."
        return f"No {noun} options are available for the current selection."
    if phase_key in DROPDOWN_PHASES:
        topic = str(selections.get("case_topic") or "").strip()
        topic_prefix = ""
        if topic and phase_key in {
            "selecting_jurisdiction",
            "selecting_case_category",
            "selecting_case_type",
        }:
            topic_prefix = f"These options match {topic}. "
        if len(labels) == 1:
            return f"{topic_prefix}Please select the {noun}: {labels[0]}"
        return (
            f"{topic_prefix}Please select the {noun} from the dropdown "
            f"({len(labels)} options)."
        )
    shown = labels[:MAX_LISTED_OPTIONS]
    lines = "\n".join(f"- {label}" for label in shown)
    if len(labels) > len(shown):
        return (
            f"There are {len(labels)} {noun} options. Type the {noun} name to "
            f"search. Here are the first {len(shown)}:\n\n{lines}"
        )
    return f"Please select the {noun}:\n\n{lines}"


def _match(options: List[Dict[str, Any]], candidate: Any) -> Optional[Dict[str, Any]]:
    needle = _normalize(candidate)
    if not needle:
        return None
    return next(
        (
            row
            for row in options
            if _normalize(row.get("code")) == needle
            or _normalize(row.get("name")) == needle
            or _normalize(row.get("state_code")) == needle
            or _normalize(row.get("state_name")) == needle
            or _normalize(row.get("county_name")) == needle
            or _normalize(row.get("case_type")) == needle
        ),
        None,
    )


def filter_selections_update(
    phase: str,
    update: Dict[str, Any],
    options: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Validate an update and carry the selected item's next API link."""
    if not update or not options:
        return {}
    phase_key = str(phase).lower()

    if phase_key in {"selecting_state", "existing_selecting_state"}:
        match = _match(
            options, update.get("state_code") or update.get("state_name")
        )
        return (
            {
                "state_code": match.get("state_code"),
                "state_name": match.get("state_name"),
            }
            if match
            else {}
        )

    if phase_key == "selecting_county":
        match = _match(options, update.get("county_name"))
        if not match:
            return {}
        result = {"county_name": match.get("county_name")}
        if match.get("county_id"):
            result["county_id"] = match["county_id"]
        return result

    configs = {
        "selecting_jurisdiction": (
            "jurisdiction",
            "case_category_codes_url",
        ),
        "existing_selecting_jurisdiction": ("jurisdiction", None),
        "selecting_case_category": ("case_category", "case_type_codes_url"),
        "selecting_case_type": ("case_type", "party_type_codes_url"),
        "selecting_case_parties": ("party_type", None),
        "selecting_filer_type": ("filer_type", None),
        "selecting_filing_code": ("filing_code", "document_type_codes_url"),
        "selecting_doc_type_code": ("doc_type", None),
        "selecting_document_type": ("document_type", None),
        "selecting_filing_type": ("filing_type", None),
    }
    config = configs.get(phase_key)
    if not config:
        return dict(update)
    prefix, next_url_key = config
    match = _match(
        options,
        update.get(prefix)
        or update.get(f"{prefix}_code")
        or update.get(f"{prefix}_name")
        or update.get("case_type")
        or update.get("party_role"),
    )
    if not match:
        return {}
    code = match.get("code")
    name = match.get("name") or code
    result: Dict[str, Any] = {
        f"{prefix}_code": code,
        f"{prefix}_name": name,
        f"selected_{prefix}": match.get("raw")
        or {"code": code, "name": name, "link": match.get("links") or {}},
    }
    if next_url_key and match.get(next_url_key):
        result[next_url_key] = match[next_url_key]
    if prefix == "case_type":
        result["case_type"] = code
        if match.get("filing_codes_url"):
            result["filing_codes_url"] = match["filing_codes_url"]
        if match.get("case_subtype_codes_url"):
            result["case_subtype_codes_url"] = match["case_subtype_codes_url"]
        # New: propagate filer_type and filing_type link URLs so the new
        # SELECTING_FILER_TYPE / SELECTING_FILING_TYPE phases can call them.
        if match.get("filer_type_codes_url"):
            result["filer_type_codes_url"] = match["filer_type_codes_url"]
        if match.get("filing_type_url"):
            result["filing_type_url"] = match["filing_type_url"]
        if match.get("optional_service_codes_url"):
            result["optional_service_codes_url"] = match[
                "optional_service_codes_url"
            ]
        if match.get("case_category_code"):
            result.setdefault("case_category_code", match["case_category_code"])
        if match.get("case_category_name"):
            result.setdefault("case_category_name", match["case_category_name"])
    if prefix == "party_type":
        result["case_parties"] = [{"code": code, "name": name}]
        result["parties_complete"] = True
    # Filer-type, filing-code and filing-type selections plug the raw scalar
    # into ``selections`` under the bare key so the live payload assembler /
    # validator can look them up (they don't read ``<prefix>_code``).
    if prefix == "filer_type":
        result["filer_type"] = code
    if prefix == "filing_code":
        result["filing_code"] = code
    if prefix == "filing_type":
        result["filing_type"] = code
    if prefix == "document_type":
        result["document_type_code"] = match.get("doc_type") or code
        result["document_type_name"] = name
        result["doc_type"] = match.get("doc_type") or code
        if match.get("template_id") or match.get("id"):
            result["template_id"] = str(match.get("template_id") or match.get("id"))
        if match.get("s3_bucket"):
            result["s3_bucket"] = match["s3_bucket"]
        if match.get("s3_key"):
            result["s3_key"] = match["s3_key"]
        if match.get("template_code"):
            result["template_code"] = match["template_code"]
        elif match.get("code") and match.get("code") != result.get("doc_type"):
            result["template_code"] = match.get("code")
        if match.get("field_mapping"):
            result["field_mapping"] = match["field_mapping"]
    return result
