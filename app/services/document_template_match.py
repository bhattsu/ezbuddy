"""Match configuration.document_templates rows to the current case selections."""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Sequence

_SYNONYMS = {
    "without": "no",
    "wout": "no",
    "w": "with",
}


def normalize_token_text(value: Any) -> str:
    text = re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()
    if not text:
        return ""
    parts = [_SYNONYMS.get(part, part) for part in text.split()]
    return " ".join(parts)


def _tokens(value: Any) -> set[str]:
    text = normalize_token_text(value)
    return set(text.split()) if text else set()


def _candidates(selections: Dict[str, Any], keys: Sequence[str]) -> List[str]:
    values: List[str] = []
    for key in keys:
        raw = selections.get(key)
        if raw is None or raw == "":
            continue
        values.append(str(raw))
        nested = selections.get("case_search_result") or {}
        if isinstance(nested, dict) and nested.get(key):
            values.append(str(nested[key]))
        details = selections.get("case_details") or selections.get("case_metadata") or {}
        if isinstance(details, dict) and details.get(key):
            values.append(str(details[key]))
    return values


def field_matches(db_value: Any, candidates: Iterable[str]) -> bool:
    """Empty DB values are wildcards. Otherwise require a fuzzy overlap."""
    db_text = normalize_token_text(db_value)
    if not db_text:
        return True
    db_tokens = _tokens(db_value)
    for candidate in candidates:
        cand_text = normalize_token_text(candidate)
        if not cand_text:
            continue
        if db_text == cand_text or db_text in cand_text or cand_text in db_text:
            return True
        cand_tokens = _tokens(candidate)
        if db_tokens and db_tokens <= cand_tokens:
            return True
        if cand_tokens and cand_tokens <= db_tokens:
            return True
    return False


def match_document_templates(
    templates: List[Dict[str, Any]], selections: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """Return active templates whose case attributes fit the current filing."""
    state_vals = _candidates(
        selections, ("state_code", "state_name", "state")
    )
    jurisdiction_vals = _candidates(
        selections,
        (
            "jurisdiction_code",
            "jurisdiction_name",
            "jurisdiction_display",
            "jurisdiction",
        ),
    )
    category_vals = _candidates(
        selections,
        (
            "case_category_code",
            "case_category_name",
            "case_category_display",
            "case_category",
        ),
    )
    type_vals = _candidates(
        selections,
        (
            "case_type_code",
            "case_type_name",
            "case_type_display",
            "case_type",
        ),
    )
    subtype_vals = _candidates(
        selections,
        (
            "sub_case_type",
            "case_subtype",
            "case_type_name",
            "case_type_display",
        ),
    )

    matched: List[Dict[str, Any]] = []
    for row in templates:
        if row.get("is_active") is False:
            continue
        if not field_matches(row.get("state"), state_vals):
            continue
        if not field_matches(row.get("jurisdiction"), jurisdiction_vals):
            continue
        if not field_matches(row.get("case_category"), category_vals):
            continue
        if not field_matches(row.get("case_type"), type_vals):
            continue
        if not field_matches(row.get("case_subtype"), subtype_vals):
            continue
        option = dict(row)
        option["id"] = str(row.get("id") or "")
        option["template_code"] = row.get("code")
        option["code"] = row.get("doc_type") or row.get("code")
        option["name"] = row.get("name") or row.get("doc_type") or row.get("code")
        option["template_id"] = str(row.get("id") or row.get("template_id") or "")
        option["doc_type"] = row.get("doc_type") or option["code"]
        matched.append(option)
    return matched


def template_questions_to_workflow(
    questions: Iterable[Any],
) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Turn court-form Q&A into workflow questions plus any prefilled answers."""
    items: List[Dict[str, Any]] = []
    answers: Dict[str, Any] = {}
    used: set[str] = set()
    for index, question in enumerate(questions, start=1):
        if isinstance(question, dict):
            label = str(question.get("question") or "").strip()
            field = str(question.get("field") or "").strip()
            answer = str(question.get("answer") or "").strip()
            page = question.get("page")
        else:
            label = str(getattr(question, "question", "") or "").strip()
            field = str(getattr(question, "field", "") or "").strip()
            answer = str(getattr(question, "answer", "") or "").strip()
            page = getattr(question, "page", None)
        if not label:
            continue
        base = field or label
        field_name = re.sub(r"[^a-z0-9]+", "_", base.lower()).strip("_") or f"field_{index}"
        if field_name in used:
            field_name = f"{field_name}_{index}"
        used.add(field_name)
        items.append(
            {
                "field_name": field_name,
                "field_label": label,
                "pdf_field": field or None,
                "question": label,
                "required": not bool(answer),
                "sort_order": index,
                "question_type": "text",
                "page": page,
            }
        )
        if answer:
            answers[field_name] = answer
    return items, answers


def _humanize_mapping_source(name: str) -> str:
    cleaned = re.sub(r"^_+|_+$", "", str(name or ""))
    cleaned = cleaned.replace("_", " ").strip()
    return cleaned[:1].upper() + cleaned[1:].lower() if cleaned else name


def merge_document_and_mapping_questions(
    document_questions: List[Dict[str, Any]],
    field_mapping: str,
) -> List[Dict[str, Any]]:
    """
    Build the ask-list from field_mapping sources, using PDF question wording
    when a source matches an extracted field. Extra PDF-only fields are omitted
    because they are not in field_mapping.
    """
    from app.services.field_mapping_service import parse_field_mapping_sources

    sources = parse_field_mapping_sources(field_mapping)
    if not sources:
        return list(document_questions or [])

    leftover = list(document_questions or [])
    merged: List[Dict[str, Any]] = []
    used: set[str] = set()

    def _matches(question: Dict[str, Any], source: str) -> bool:
        source_norm = normalize_token_text(source)
        haystack = normalize_token_text(
            " ".join(
                str(question.get(key) or "")
                for key in ("field_name", "pdf_field", "field", "field_label", "question")
            )
        )
        source_slug = re.sub(r"[^a-z0-9]+", "_", source.lower()).strip("_")
        field_slug = re.sub(
            r"[^a-z0-9]+", "_", str(question.get("field_name") or "").lower()
        ).strip("_")
        return bool(
            (source_norm and (source_norm in haystack or haystack in source_norm))
            or (source_slug and source_slug == field_slug)
        )

    for index, source in enumerate(sources, start=1):
        match_idx = next(
            (idx for idx, row in enumerate(leftover) if _matches(row, source)),
            None,
        )
        if match_idx is not None:
            row = leftover.pop(match_idx)
            field_name = str(row.get("field_name") or source)
            if field_name in used:
                continue
            used.add(field_name)
            row = dict(row)
            row.setdefault("pdf_field", source)
            row["mapping_source"] = source
            merged.append(row)
            continue
        field_name = re.sub(r"[^a-z0-9]+", "_", source.lower()).strip("_") or f"field_{index}"
        if field_name in used:
            continue
        used.add(field_name)
        merged.append(
            {
                "field_name": field_name,
                "field_label": _humanize_mapping_source(source),
                "pdf_field": source,
                "question": _humanize_mapping_source(source),
                "required": True,
                "sort_order": index,
                "question_type": "text",
                "mapping_source": source,
            }
        )
    return merged


_CHILD_HINTS = (
    "child",
    "children",
    "custody",
    "conservator",
    "parenting",
    "visitation",
    "child support",
    "possessory",
)
_CAUSE_HINTS = ("cause number", "case number", "cause no")
_COUNTY_HINTS = ("county name", "county of", "what is the county")
_COURT_TYPE_HINTS = ("district court or county court", "court type", "district court")
_PETITIONER_HINTS = ("petitioner", "plaintiff")
_RESPONDENT_HINTS = ("respondent", "defendant")


def _label_text(question: Dict[str, Any]) -> str:
    return normalize_token_text(
        " ".join(
            str(question.get(key) or "")
            for key in ("pdf_field", "field", "field_label", "question")
        )
    )


def _case_has_no_children(selections: Dict[str, Any]) -> bool:
    blob = normalize_token_text(
        " ".join(
            str(selections.get(key) or "")
            for key in (
                "case_type_name",
                "case_type_display",
                "case_type",
                "doc_type",
                "document_type_code",
                "document_type_name",
            )
        )
    )
    return (
        "no children" in blob
        or "without children" in blob
        or "petition no children" in blob
    )


def _party_names_from_selections(selections: Dict[str, Any]) -> tuple[str, str]:
    details = selections.get("case_details") or selections.get("case_metadata") or {}
    parties = details.get("case_parties") if isinstance(details, dict) else []
    petitioner = ""
    respondent = ""
    if isinstance(parties, list):
        for party in parties:
            if not isinstance(party, dict):
                continue
            name = (
                party.get("name")
                or party.get("full_name")
                or " ".join(
                    str(party.get(key) or "").strip()
                    for key in ("first_name", "middle_name", "last_name")
                ).strip()
            )
            role = normalize_token_text(
                party.get("role") or party.get("party_type") or party.get("type") or ""
            )
            if name and any(token in role for token in ("petitioner", "plaintiff")):
                petitioner = petitioner or str(name)
            if name and any(token in role for token in ("respondent", "defendant")):
                respondent = respondent or str(name)
    title = str(
        (details.get("case_title") if isinstance(details, dict) else "")
        or selections.get("case_title")
        or ""
    )
    if " of " in title.lower() and (not petitioner or not respondent):
        tail = title.split(" of ", 1)[-1]
        parts = [part.strip(" ,") for part in tail.split(",") if part.strip(" ,")]
        if len(parts) >= 2:
            petitioner = petitioner or parts[0]
            respondent = respondent or parts[1]
    return petitioner, respondent


def apply_known_case_answers(
    questions: List[Dict[str, Any]], selections: Dict[str, Any]
) -> tuple[Dict[str, Any], List[str]]:
    """Prefill known case facts and skip fields that do not apply."""
    answers: Dict[str, Any] = {}
    skipped: List[str] = []
    case_number = str(selections.get("case_number") or "").strip()
    county = str(
        selections.get("jurisdiction_name")
        or selections.get("jurisdiction_display")
        or ""
    ).strip()
    if " - " in county:
        county = county.split(" - ", 1)[0].replace(" County", "").strip() or county
    petitioner, respondent = _party_names_from_selections(selections)
    skip_children = _case_has_no_children(selections)
    jurisdiction_display = str(
        selections.get("jurisdiction_display")
        or selections.get("jurisdiction_name")
        or ""
    ).lower()

    for question in questions:
        name = str(question.get("field_name") or "")
        if not name:
            continue
        label = _label_text(question)
        if skip_children and any(hint in label for hint in _CHILD_HINTS):
            skipped.append(name)
            continue
        if case_number and any(hint in label for hint in _CAUSE_HINTS):
            answers.setdefault(name, case_number)
            continue
        if county and any(hint in label for hint in _COUNTY_HINTS):
            answers.setdefault(name, county)
            continue
        if "district" in jurisdiction_display and any(
            hint in label for hint in _COURT_TYPE_HINTS
        ):
            answers.setdefault(name, "District Court")
            continue
        if petitioner and any(hint in label for hint in _PETITIONER_HINTS) and "attorney" not in label:
            if "name" in label or label.endswith("petitioner") or "full name" in label:
                answers.setdefault(name, petitioner)
                continue
        if respondent and any(hint in label for hint in _RESPONDENT_HINTS) and "attorney" not in label:
            if "name" in label or "full name" in label:
                answers.setdefault(name, respondent)
    return answers, skipped
