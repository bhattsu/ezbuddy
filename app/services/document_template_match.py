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


def parse_template_questions_text(questions_text: str) -> List[str]:
    """Split configuration.document_templates.questions (one question per line)."""
    lines: List[str] = []
    for raw in str(questions_text or "").replace("\r\n", "\n").split("\n"):
        line = raw.strip()
        if line:
            lines.append(line)
    return lines


def workflow_questions_from_db_column(
    questions_text: str,
    field_mapping: str,
) -> List[Dict[str, Any]]:
    """
    Build workflow ask-list from RDS ``questions`` text aligned to field_mapping
    sources (same order as form_data / pipe mapping keys).
    """
    from app.services.field_mapping_service import parse_field_mapping_sources

    lines = parse_template_questions_text(questions_text)
    sources = parse_field_mapping_sources(field_mapping)
    if not sources:
        raise ValueError("field_mapping has no askable sources for questions.")

    merged: List[Dict[str, Any]] = []
    for index, source in enumerate(sources, start=1):
        label = lines[index - 1] if index - 1 < len(lines) else _humanize_mapping_source(source)
        row: Dict[str, Any] = {
            "field_name": source,
            "field_label": label,
            "pdf_field": source,
            "question": label,
            "required": True,
            "sort_order": index,
            "question_type": "email" if source == "$email" else "text",
            "mapping_source": source,
        }
        merged.append(row)
    return attach_mapping_visibility(merged)


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
    key = str(name or "").strip()
    if key == "$email":
        return "What is your email address?"
    cleaned = re.sub(r"^_+|_+$", "", key)
    cleaned = cleaned.replace("_", " ").strip()
    return cleaned[:1].upper() + cleaned[1:].lower() if cleaned else name


_YES_NO_FIELDS = frozenset(
    {
        "CHILDREN",
        "DRIVER_LICENSE",
        "SOCIAL_SECURITY_NUMBER",
        "LEGAL_NOTICE",
        "PROTECTIVE_ORDER",
        "NAME_CHANGE",
        "DOMICILE",
    }
)
_FOLLOW_UP_GATES = {
    "DRIVER_LICENSE": (
        "LICENSE_NUMBER",
        "LICENSE_ISSUE_STATE",
    ),
    "SOCIAL_SECURITY_NUMBER": ("SOCIAL_SECURITY_NUMBER_LAST_THREE_DIGIT",),
    "PROTECTIVE_ORDER": (
        "PROTECTIVE_ORDER_CASE_NUMBER",
        "PROTECTIVE_ORDER_DATE",
        "PROTECTIVE_ORDER_COUNTY",
        "PROTECTIVE_ORDER_STATE",
    ),
    "NAME_CHANGE": (
        "NAME_CHANGE_TO_FIRST",
        "NAME_CHANGE_TO_MIDDLE",
        "NAME_CHANGE_TO_LAST",
    ),
}


def _mapping_stem(name: str) -> str:
    return re.sub(r"^_+", "", str(name or "")).upper()


def attach_mapping_visibility(questions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Hide follow-up fields until the user answers the related yes/no gate."""
    present = {_mapping_stem(row.get("field_name") or "") for row in questions}
    gate_fields = {
        stem: next(
            (
                str(row.get("field_name") or "")
                for row in questions
                if _mapping_stem(row.get("field_name") or "") == stem
            ),
            stem,
        )
        for stem in _FOLLOW_UP_GATES
        if stem in present
    }
    for row in questions:
        stem = _mapping_stem(row.get("field_name") or "")
        if stem in _YES_NO_FIELDS:
            row["question_type"] = "BOOLEAN"
        for gate, dependents in _FOLLOW_UP_GATES.items():
            if stem in dependents and gate in gate_fields:
                row["visibility_condition"] = {gate_fields[gate]: "yes"}
        if "CHILD" in stem and stem != "CHILDREN" and "CHILDREN" in present:
            children_field = next(
                (
                    str(item.get("field_name") or "")
                    for item in questions
                    if _mapping_stem(item.get("field_name") or "") == "CHILDREN"
                ),
                "CHILDREN",
            )
            row["visibility_condition"] = {children_field: "yes"}
    return questions


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
        if source in used:
            continue
        used.add(source)
        match_idx = next(
            (idx for idx, row in enumerate(leftover) if _matches(row, source)),
            None,
        )
        if match_idx is not None:
            row = dict(leftover.pop(match_idx))
            row["field_name"] = source
            row.setdefault("pdf_field", source)
            row["mapping_source"] = source
            row["sort_order"] = index
            merged.append(row)
            continue
        merged.append(
            {
                "field_name": source,
                "field_label": _humanize_mapping_source(source),
                "pdf_field": source,
                "question": _humanize_mapping_source(source),
                "required": True,
                "sort_order": index,
                "question_type": "email" if source == "$email" else "text",
                "mapping_source": source,
            }
        )
    return attach_mapping_visibility(merged)


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
_COUNTY_HINTS = (
    "county name",
    "county of",
    "what is the county",
    "county where",
    "filed in",
)
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


_MARRIAGE_TITLE_RE = re.compile(
    r"(?i)matter\s+of\s+the\s+marriage\s+of\s+(.+)$"
)


def _party_display_name(party: Dict[str, Any]) -> str:
    name = (
        party.get("name")
        or party.get("full_name")
        or " ".join(
            str(party.get(key) or "").strip()
            for key in ("first_name", "middle_name", "last_name")
        ).strip()
    )
    return re.sub(r"\s+", " ", str(name or "")).strip()


def _clean_role_prefix_from_name(name: str) -> str:
    text = re.sub(r"\s+", " ", str(name or "")).strip()
    upper = text.upper()
    for prefix in ("PLAINTIFF", "DEFENDANT", "PETITIONER", "RESPONDENT"):
        if upper.startswith(prefix):
            return text[len(prefix) :].strip(" ,") or text
    return text


def _party_role_bucket(party: Dict[str, Any]) -> str:
    role = normalize_token_text(
        " ".join(
            str(party.get(key) or "")
            for key in (
                "role",
                "party_type",
                "party_type_name",
                "type",
                "type_name",
                "description",
            )
        )
    )
    if any(token in role for token in ("petitioner", "plaintiff", "appellant")):
        return "plaintiff"
    if any(token in role for token in ("respondent", "defendant", "appellee")):
        return "defendant"
    display = _party_display_name(party).upper()
    if "PLAINTIFF" in display or "PETITIONER" in display:
        return "plaintiff"
    if "DEFENDANT" in display or "RESPONDENT" in display:
        return "defendant"
    return ""


def _parse_marriage_case_title(title: str) -> tuple[str, str]:
    match = _MARRIAGE_TITLE_RE.search(str(title or "").strip())
    if not match:
        return "", ""
    rest = match.group(1).strip()
    parts = [part.strip(" ,") for part in re.split(r"\s*,\s*", rest) if part.strip(" ,")]
    if len(parts) < 2:
        return "", ""
    petitioner = _clean_role_prefix_from_name(parts[0])
    respondent = _clean_role_prefix_from_name(parts[-1])
    return petitioner, respondent


def _party_list_from_selections(selections: Dict[str, Any]) -> List[Dict[str, Any]]:
    details = selections.get("case_details") or selections.get("case_metadata") or {}
    raw = selections.get("existing_case_parties")
    if isinstance(raw, list) and raw:
        return [dict(row) for row in raw if isinstance(row, dict)]
    if isinstance(details, dict):
        parties = details.get("case_parties")
        if isinstance(parties, list):
            return [dict(row) for row in parties if isinstance(row, dict)]
    return []


def is_full_person_name_field(field_name: str, field_label: str = "") -> bool:
    """True for plaintiff/defendant full legal name fields — not SSN, zip, name change, etc."""
    blob = normalize_token_text(f"{field_name} {field_label}")
    if not blob:
        return False
    blocked = (
        "social security",
        "ssn",
        "name change",
        "zip",
        "email",
        "phone",
        "license",
        "driver",
        "domicile",
        "address",
        "city",
        "state",
        "county",
        "cause number",
        "case number",
        "grounds",
        "notice",
        "protective order",
        "marriage",
        "living together",
    )
    if any(term in blob for term in blocked):
        return False
    if re.search(r"\b(full\s+)?legal\s+name\b", blob):
        return True
    if "full name" in blob:
        return True
    if re.search(r"\b(plaintiff|defendant|petitioner|respondent)\b", blob):
        return "name" in blob.split() or blob.endswith(" name")
    if field_name.upper().endswith("_FULL_NAME"):
        return True
    return False


def party_side_for_field(field_name: str, field_label: str = "") -> str:
    blob = normalize_token_text(f"{field_name} {field_label}")
    if "defendant" in blob or "respondent" in blob:
        return "defendant"
    if "plaintiff" in blob or "petitioner" in blob:
        return "plaintiff"
    upper = field_name.upper()
    if "DEFENDANT" in upper or "RESPONDENT" in upper:
        return "defendant"
    if "PLAINTIFF" in upper or "PETITIONER" in upper:
        return "plaintiff"
    return ""


def is_usable_party_name(name: str) -> bool:
    """False for API placeholders like PLAINTIFF PARTY → PARTY."""
    norm = normalize_token_text(name)
    if not norm:
        return False
    tokens = norm.split()
    placeholder = {
        "party",
        "plaintiff",
        "defendant",
        "petitioner",
        "respondent",
        "unknown",
        "na",
        "n",
        "a",
        "ii",
        "iii",
        "iv",
    }
    if norm in placeholder:
        return False
    if len(tokens) == 1 and tokens[0] in placeholder:
        return False
    if len(tokens) == 2 and tokens[0] in placeholder and tokens[1] in placeholder:
        return False
    if norm in ("plaintiff party", "defendant party"):
        return False
    if len(norm.replace(" ", "")) < 3:
        return False
    return True


def filing_user_party_side(selections: Dict[str, Any]) -> str:
    filing_id = str(selections.get("filing_party_id") or "").strip()
    parties = _party_list_from_selections(selections)
    if filing_id:
        for party in parties:
            if str(party.get("id") or "").strip() == filing_id:
                side = _party_role_bucket(party)
                if side:
                    return side
    if parties:
        side = _party_role_bucket(parties[0])
        if side:
            return side
    return "plaintiff"


def _party_names_from_selections(selections: Dict[str, Any]) -> tuple[str, str]:
    parties = _party_list_from_selections(selections)
    petitioner = ""
    respondent = ""
    for party in parties:
        name = _clean_role_prefix_from_name(_party_display_name(party))
        if not name:
            continue
        bucket = _party_role_bucket(party)
        if bucket == "plaintiff":
            petitioner = petitioner or name
        elif bucket == "defendant":
            respondent = respondent or name

    details = selections.get("case_details") or selections.get("case_metadata") or {}
    title = str(
        (details.get("case_title") if isinstance(details, dict) else "")
        or selections.get("case_title")
        or ""
    )
    if not petitioner or not respondent:
        parsed_p, parsed_r = _parse_marriage_case_title(title)
        petitioner = petitioner or parsed_p
        respondent = respondent or parsed_r

    if (not petitioner or not respondent) and len(parties) >= 2:
        first = _clean_role_prefix_from_name(_party_display_name(parties[0]))
        second = _clean_role_prefix_from_name(_party_display_name(parties[1]))
        petitioner = petitioner or first
        respondent = respondent or second

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
        if skip_children and _mapping_stem(name) == "CHILDREN":
            answers.setdefault(name, "no")
            continue
        if skip_children and any(hint in label for hint in _CHILD_HINTS):
            skipped.append(name)
            continue
        if case_number and any(hint in label for hint in _CAUSE_HINTS):
            answers.setdefault(name, case_number)
            continue
        if (
            county
            and "county" in label
            and any(hint in label for hint in _COUNTY_HINTS)
        ):
            answers.setdefault(name, county)
            continue
        if "district" in jurisdiction_display and any(
            hint in label for hint in _COURT_TYPE_HINTS
        ):
            answers.setdefault(name, "District Court")
            continue
        field_label = str(question.get("field_label") or question.get("question") or "")
        if (
            petitioner
            and is_usable_party_name(petitioner)
            and is_full_person_name_field(name, field_label)
        ):
            if party_side_for_field(name, field_label) in ("", "plaintiff"):
                answers.setdefault(name, petitioner)
                continue
        if (
            respondent
            and is_usable_party_name(respondent)
            and is_full_person_name_field(name, field_label)
        ):
            if party_side_for_field(name, field_label) == "defendant":
                answers.setdefault(name, respondent)
                continue
    return answers, skipped
