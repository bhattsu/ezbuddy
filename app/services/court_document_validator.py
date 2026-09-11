"""Reject uploads that are not court-related after text extraction."""

from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

NON_COURT_DOCUMENT_REJECTION_MESSAGE = (
    "This uploaded file is not a court document. "
    "Please upload a court filing related to your case (petition, summons, order, "
    "affidavit, or similar), or reply that you have no file to continue without an upload."
)

_COURT_CLASSIFICATIONS = frozenset(
    {
        "petition",
        "summons",
        "order",
        "affidavit",
        "motion",
        "decree",
        "citation",
        "complaint",
        "waiver",
        "notice",
    }
)

_POSITIVE_HINTS = (
    "district court",
    "county court",
    "superior court",
    "circuit court",
    "family court",
    "cause no",
    "cause number",
    "case no",
    "case number",
    "docket",
    "in the court",
    "clerk of the court",
    "original petition",
    "final decree",
)

_NEGATIVE_HINTS = (
    "work experience",
    "curriculum vitae",
    "references available",
    "invoice number",
    "total due",
    "purchase order",
    "prescription",
    "patient name",
    "diagnosis",
    "tax return",
    "w-2",
    "form 1099",
    "balance sheet",
    "meeting agenda",
    "pricing calculator",
    "cost estimate",
    "aws pricing",
    "amazon web services",
    "monthly cost",
    "upfront cost",
    "amazon bedrock",
    "amazon dynamodb",
    "aws lambda",
    "cloudwatch",
    "elastic container registry",
    "cloud infrastructure",
)


class NonCourtDocumentError(ValueError):
    """Raised when extracted content is not a court filing."""

    def __init__(self, message: str = NON_COURT_DOCUMENT_REJECTION_MESSAGE):
        super().__init__(message)


def _normalize(text: str) -> str:
    return " ".join(str(text or "").lower().split())


def _collect_values(value: Any, parts: list[str]) -> None:
    if value is None:
        return
    if isinstance(value, dict):
        for item in value.values():
            _collect_values(item, parts)
        return
    if isinstance(value, list):
        for item in value:
            _collect_values(item, parts)
        return
    text = str(value).strip()
    if text:
        parts.append(text)


def _values_blob(
    *,
    text: str = "",
    form_kv: Optional[Dict[str, Any]] = None,
    extracted_fields: Optional[Dict[str, Any]] = None,
    user_details: Optional[Dict[str, Any]] = None,
    case_type: Optional[str] = None,
    sub_case_type: Optional[str] = None,
) -> str:
    """Build searchable text from field values only (ignore JSON key names)."""
    parts: list[str] = []
    if text.strip():
        parts.append(text)
    _collect_values(form_kv, parts)
    _collect_values(extracted_fields, parts)
    _collect_values(user_details, parts)
    for extra in (case_type, sub_case_type):
        if extra:
            parts.append(str(extra))
    return _normalize(" ".join(parts))


def _count_hits(blob: str, hints: Iterable[str]) -> int:
    return sum(1 for hint in hints if hint in blob)


def _party_names_present(user_details: Optional[Dict[str, Any]]) -> bool:
    details = user_details if isinstance(user_details, dict) else {}
    for role in ("petitioner", "respondent", "plaintiff", "defendant"):
        party = details.get(role)
        if not isinstance(party, dict):
            continue
        name = str(
            party.get("full_name")
            or party.get("name")
            or " ".join(
                str(party.get(key) or "").strip()
                for key in ("first_name", "middle_name", "last_name")
            ).strip()
        ).strip()
        if name:
            return True
    return False


def is_court_document(
    *,
    text: str = "",
    form_kv: Optional[Dict[str, Any]] = None,
    extracted_fields: Optional[Dict[str, Any]] = None,
    user_details: Optional[Dict[str, Any]] = None,
    classification: Optional[str] = None,
    case_type: Optional[str] = None,
    sub_case_type: Optional[str] = None,
) -> bool:
    """True when extracted content looks like a US court filing."""
    blob = _values_blob(
        text=text,
        form_kv=form_kv,
        extracted_fields=extracted_fields,
        user_details=user_details,
        case_type=case_type,
        sub_case_type=sub_case_type,
    )
    classification_key = _normalize(classification or "").split(" ")[0]
    positive = _count_hits(blob, _POSITIVE_HINTS)
    negative = _count_hits(blob, _NEGATIVE_HINTS)
    details = user_details if isinstance(user_details, dict) else {}
    has_case_id = bool(
        str(details.get("cause_number") or details.get("case_number") or "").strip()
    )
    has_court = bool(str(details.get("court_name") or details.get("county") or "").strip())
    has_parties = _party_names_present(details)

    if negative > 0 and not has_case_id and not has_court:
        return False
    if classification_key == "other" and not has_case_id and not has_court and not has_parties:
        return False
    if classification_key in _COURT_CLASSIFICATIONS and negative <= positive:
        return True
    if has_case_id or has_court:
        return True
    if has_parties and positive >= 1:
        return True
    if positive >= 2 and positive > negative:
        return True
    if not blob.strip():
        return classification_key in _COURT_CLASSIFICATIONS
    return False


def validate_court_document(
    *,
    text: str = "",
    form_kv: Optional[Dict[str, Any]] = None,
    extracted_fields: Optional[Dict[str, Any]] = None,
    user_details: Optional[Dict[str, Any]] = None,
    classification: Optional[str] = None,
    case_type: Optional[str] = None,
    sub_case_type: Optional[str] = None,
) -> None:
    if not is_court_document(
        text=text,
        form_kv=form_kv,
        extracted_fields=extracted_fields,
        user_details=user_details,
        classification=classification,
        case_type=case_type,
        sub_case_type=sub_case_type,
    ):
        raise NonCourtDocumentError()
