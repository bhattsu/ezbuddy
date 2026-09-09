"""Map collected filing data onto a new-case e-file request via LLM."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.adapters.llm.bedrock import Bedrock, get_bedrock
from app.core.prompts.context import format_llm_prompt
from app.core.prompts.efile_mapping import (
    EFILE_MAPPING_PROMPT,
    EXISTING_CASE_EFILE_MAPPING_PROMPT,
)

logger = logging.getLogger(__name__)

NEW_CASE_EFILE_SAMPLE = {
    "data": {
        "filer_type": "54325",
        "reference_id": "DRAFT-2026-10038",
        "jurisdiction": "harris:dc",
        "payment_account_id": "CC_01eb044f-9e41-4966-93e2-31a5f8d9c01b",
        "filings": [
            {
                "code": "209523",
                "file_name": "complaint.pdf",
                "description": "Petition",
                "doc_type": "53689",
                "file": "https://example.com/complaint.pdf",
                "size": 123261,
                "associated_parties": [],
                "id": "332-c6931c95805340608d6daf5a640618fa",
            }
        ],
        "case_parties": [
            {
                "country": "US",
                "city": "Houston",
                "type": "53024",
                "zip_code": "77002",
                "address_line_1": "1200 Baker Street",
                "id": "Party_8168279",
                "state": "TX",
                "first_name": "JANE",
                "is_business": False,
                "lead_attorney": "PRO SE",
                "last_name": "DOE",
                "additional_attorneys": [],
            },
            {
                "country": "US",
                "type": "271011",
                "id": "Party_0325725",
                "first_name": "JOHN",
                "is_business": False,
                "last_name": "DOE",
                "additional_attorneys": [],
            },
        ],
        "provider_tax": "0.25",
        "filing_type": "EFileAndServe",
        "filing_state": "tx",
        "case_type": "209421",
        "provider_fee": "2.99",
        "case_category": "131370",
        "filing_party_id": "Party_8168279",
    }
}

EXISTING_CASE_EFILE_SAMPLE = {
    "data": {
        "reference_id": "48291",
        "case_tracking_id": "tyler_yuba:sc~example-case-id~CT",
        "payment_account_id": "CC_example-payment-account",
        "filing_party_id": "Party_example",
        "filing_type": "EFile",
        "filings": [
            {
                "code": "29736",
                "file_name": "Notice.pdf",
                "description": "Notice of Appeal",
                "doc_type": "44889",
                "file": "https://ontheline.trincoll.edu/images/bookdown/sample-local-pdf.pdf",
            }
        ],
    }
}

EXISTING_CASE_EFILE_KEYS = (
    "reference_id",
    "case_tracking_id",
    "payment_account_id",
    "filing_party_id",
    "filing_type",
    "filings",
)
NEW_CASE_EFILE_KEYS = (
    "filer_type",
    "reference_id",
    "jurisdiction",
    "payment_account_id",
    "filings",
    "case_parties",
    "provider_tax",
    "filing_type",
    "filing_state",
    "case_type",
    "provider_fee",
    "case_category",
    "filing_party_id",
)
NEW_CASE_FILING_KEYS = (
    "code",
    "file_name",
    "description",
    "doc_type",
    "file",
    "size",
    "associated_parties",
    "id",
)
EXISTING_CASE_FILING_KEYS = (
    "code",
    "file_name",
    "description",
    "doc_type",
    "file",
)
NEW_CASE_PARTY_KEYS = (
    "country",
    "city",
    "type",
    "zip_code",
    "address_line_1",
    "id",
    "state",
    "first_name",
    "is_business",
    "lead_attorney",
    "last_name",
    "additional_attorneys",
)


def _text(value: Any) -> str:
    if value in (None,):
        return ""
    return str(value).strip()


def _int_or_empty(value: Any):
    if value in (None, ""):
        return ""
    if isinstance(value, bool):
        return ""
    if isinstance(value, int):
        return value
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return ""


def _selected_code(selections: Dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = _text(selections.get(key))
        if value:
            return value
    return ""


def _split_name(value: Any) -> tuple[str, str]:
    parts = _text(value).split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def _name_from_maps(
    sources: List[Dict[str, Any]], needles: tuple[str, ...]
) -> tuple[str, str]:
    """
    Discover a first/last name pair from arbitrary workflow answers.

    Priority order:
    1. Explicit ``<needle>_FIRST_NAME`` / ``<needle>_LAST_NAME`` pair.
    2. A single ``<needle>_FULL_NAME`` / ``<needle>_NAME`` split on whitespace.
    3. Any key that *contains* the needle, split on whitespace.
    """
    upper_needles = tuple(n.upper() for n in needles)

    # 1. Explicit first + last pair.
    for source in sources:
        for needle in upper_needles:
            first = ""
            last = ""
            for key, raw in (source or {}).items():
                upper = str(key).upper()
                if needle not in upper:
                    continue
                if "FIRST" in upper and "NAME" in upper and not first:
                    first = _text(raw)
                elif "LAST" in upper and "NAME" in upper and not last:
                    last = _text(raw)
            if first or last:
                return first, last

    # 2. Full-name-style single field (e.g. ``PETITIONER_FULL_NAME``).
    for source in sources:
        for needle in upper_needles:
            for key, raw in (source or {}).items():
                upper = str(key).upper()
                if needle in upper and (
                    "FULL_NAME" in upper or upper.endswith("_NAME") or upper == "NAME"
                ):
                    first, last = _split_name(raw)
                    if first or last:
                        return first, last

    # 3. Fallback: any key containing the needle, split on whitespace.
    for source in sources:
        for key, raw in (source or {}).items():
            upper = str(key).upper()
            if any(needle in upper for needle in upper_needles):
                first, last = _split_name(raw)
                if first or last:
                    return first, last
    return "", ""


def _address_from_maps(sources: List[Dict[str, Any]], *needles: str) -> str:
    for source in sources:
        for key, raw in (source or {}).items():
            upper = str(key).upper()
            if any(needle in upper for needle in needles):
                value = _text(raw)
                if value:
                    return value
    return ""


def _generated_file(
    selections: Dict[str, Any], generated_documents: Optional[List[Dict[str, Any]]]
) -> Dict[str, Any]:
    row = dict((generated_documents or [{}])[0] or {}) if generated_documents else {}
    file_url = _text(
        row.get("file")
        or row.get("file_url")
        or row.get("download_url")
        or row.get("s3_url")
        or selections.get("efile_file_url")
    )
    return {
        "file": file_url,
        "file_name": _text(row.get("file_name") or selections.get("generated_pdf_name")),
        "description": _text(
            row.get("template_name")
            or selections.get("document_type_name")
            or selections.get("template_code")
        ),
        "size": _int_or_empty(row.get("size") or selections.get("efile_file_size")),
        "id": _text(row.get("document_id") or row.get("id")),
    }


def _new_case_filing(
    selections: Dict[str, Any], generated_documents: Optional[List[Dict[str, Any]]]
) -> Dict[str, Any]:
    generated = _generated_file(selections, generated_documents)
    filing = {key: "" for key in NEW_CASE_FILING_KEYS}
    filing["code"] = _selected_code(selections, "filing_code")
    filing["doc_type"] = _selected_code(
        selections, "doc_type", "document_type_code"
    )
    filing["file"] = generated["file"]
    filing["file_name"] = generated["file_name"]
    filing["description"] = generated["description"]
    filing["size"] = generated["size"]
    filing["id"] = generated["id"]
    filing["associated_parties"] = []
    return filing


def _existing_case_filing(
    selections: Dict[str, Any], generated_documents: Optional[List[Dict[str, Any]]]
) -> Dict[str, Any]:
    generated = _generated_file(selections, generated_documents)
    return {
        "code": _selected_code(selections, "filing_code"),
        "file_name": generated["file_name"],
        "description": generated["description"],
        "doc_type": _selected_code(selections, "doc_type", "document_type_code"),
        "file": generated["file"],
    }


# Role-name needles used to mine first/last names from workflow answers.
# Ordered by specificity so the primary party grabs "PETITIONER_FIRST_NAME"
# before a stub party can accidentally pick it up.
_PRIMARY_PARTY_NEEDLES = (
    "PLAINTIFF",
    "PETITIONER",
    "PARTY_1",
    "FILING_PARTY",
    "APPLICANT",
)
_OTHER_PARTY_NEEDLES_BY_TYPE_NAME = {
    # Map a role keyword to the answer needles most likely to hold that role's
    # name. The lookup is prefix-matched (case-insensitive) against the party
    # type's ``name`` from the live bundle.
    "respondent": ("RESPONDENT", "PARTY_2", "SPOUSE"),
    "defendant": ("DEFENDANT", "PARTY_2"),
    "appellee": ("APPELLEE", "PARTY_2"),
    "petitioner": ("PETITIONER", "PARTY_1"),
    "plaintiff": ("PLAINTIFF", "PARTY_1"),
    "appellant": ("APPELLANT", "PARTY_1"),
}


def _needles_for_type_name(name: str) -> tuple[str, ...]:
    n = str(name or "").strip().lower()
    if not n:
        return ("PARTY_2", "OTHER_PARTY")
    for keyword, needles in _OTHER_PARTY_NEEDLES_BY_TYPE_NAME.items():
        if keyword in n:
            return needles
    return ("PARTY_2", "OTHER_PARTY")


def _first_last_from_selections_and_answers(
    selections: Dict[str, Any],
    sources: List[Dict[str, Any]],
    role_needles: tuple[str, ...],
) -> tuple[str, str]:
    """Prefer explicit selections keys, else mine workflow answers."""
    first = _text(
        selections.get("first_name") or selections.get("plaintiff_first_name")
    )
    last = _text(
        selections.get("last_name") or selections.get("plaintiff_last_name")
    )
    if first or last:
        return first, last
    return _name_from_maps(sources, role_needles)


def _new_case_parties(
    selections: Dict[str, Any],
    collected_answers: Optional[Dict[str, Any]],
    form_data: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Build the *primary* case party (id=Party_1) from selections + answers.

    Additional (respondent / defendant) parties are added downstream by
    :func:`_fill_missing_required_parties` so they always get a valid
    ``type`` code from the live bundle instead of being appended type-less.
    """
    answers = dict(collected_answers or {})
    form = dict(form_data or {})
    sources = [selections, answers, form]
    party_type = _selected_code(selections, "party_type_code")
    first, last = _first_last_from_selections_and_answers(
        selections, sources, _PRIMARY_PARTY_NEEDLES
    )
    city = _address_from_maps(sources, "CITY")
    address = _address_from_maps(sources, "ADDRESS_LINE", "ADDRESS1", "STREET")
    zip_code = _address_from_maps(sources, "ZIP", "POSTAL")
    state = _address_from_maps(sources, "STATE")
    country = _address_from_maps(sources, "COUNTRY")
    attorney = _address_from_maps(sources, "LEAD_ATTORNEY", "ATTORNEY")
    # Never surface ``filing_party_id`` on the party itself -- it's a top-level
    # payload field that must match one of the ``case_parties[].id`` values.
    # ``assemble_new_case_efile_data_live`` renumbers ``id`` for every party
    # to ``Party_{n}`` after assembly.
    party_id = "Party_1"

    first_party = {key: "" for key in NEW_CASE_PARTY_KEYS}
    first_party.update(
        {
            "country": country,
            "city": city,
            "type": party_type,
            "zip_code": zip_code,
            "address_line_1": address,
            "id": party_id,
            "state": state,
            "first_name": first,
            "is_business": False,
            "lead_attorney": attorney,
            "last_name": last,
            "additional_attorneys": [],
        }
    )
    return [first_party]


def assemble_new_case_efile_data(
    *,
    selections: Dict[str, Any],
    collected_answers: Optional[Dict[str, Any]] = None,
    generated_documents: Optional[List[Dict[str, Any]]] = None,
    reference_id: str = "",
) -> Dict[str, Any]:
    """Fill the new-case sample shape from session cache/answers only."""
    form_data = mapped_form_data_from_documents(generated_documents)
    parties = _new_case_parties(selections, collected_answers, form_data)
    filing_party_id = _selected_code(
        selections, "filing_party_id", "efile_filing_party_id"
    )
    if not filing_party_id and parties and parties[0].get("id"):
        filing_party_id = str(parties[0]["id"])
    data = {key: "" for key in NEW_CASE_EFILE_KEYS}
    data.update(
        {
            "filer_type": _text(selections.get("filer_type")),
            "reference_id": _text(reference_id or selections.get("reference_id")),
            "jurisdiction": _selected_code(selections, "jurisdiction_code"),
            "payment_account_id": _selected_code(
                selections, "court_payment_account_id"
            ),
            "filings": [_new_case_filing(selections, generated_documents)],
            "case_parties": parties,
            "provider_tax": _text(selections.get("provider_tax")),
            "filing_type": _text(selections.get("filing_type")),
            "filing_state": _text(selections.get("state_code")).lower(),
            "case_type": _selected_code(selections, "case_type_code"),
            "provider_fee": _text(selections.get("provider_fee")),
            "case_category": _selected_code(selections, "case_category_code"),
            "filing_party_id": filing_party_id,
        }
    )
    return data


def _first_code(rows: Optional[List[Dict[str, Any]]]) -> str:
    for row in rows or []:
        code = str((row or {}).get("code") or "").strip()
        if code:
            return code
    return ""


def _pick_code(
    hint: str,
    rows: Optional[List[Dict[str, Any]]],
    *,
    field_name: str = "",
    logger_: Optional[logging.Logger] = None,
) -> str:
    """Pick ``hint`` if it exists in ``rows``, else the first row's code."""
    hint_s = str(hint or "").strip()
    codes = {
        str((row or {}).get("code") or "").strip() for row in rows or []
    }
    codes.discard("")
    if hint_s and hint_s in codes:
        return hint_s
    fallback = _first_code(rows)
    if hint_s and hint_s != fallback and fallback and logger_ is not None:
        logger_.warning(
            "Live assembler falling back on %s: hint '%s' not in live codes %s, using '%s'",
            field_name or "code",
            hint_s,
            sorted(codes),
            fallback,
        )
    return fallback or hint_s


def _int_or_none(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _explicit_filings(selections: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw = selections.get("efile_filings")
    if isinstance(raw, list):
        return [dict(row) for row in raw if isinstance(row, dict)]
    return []


def _filing_from_document_row(
    row: Dict[str, Any], selections: Dict[str, Any]
) -> Dict[str, Any]:
    row = dict(row or {})
    return {
        "code": _text(
            row.get("filing_code")
            or row.get("code")
            or selections.get("filing_code")
        ),
        "doc_type": _text(
            row.get("doc_type")
            or row.get("document_type_code")
            or selections.get("doc_type")
            or selections.get("document_type_code")
        ),
        "file": _text(
            row.get("file")
            or row.get("file_url")
            or row.get("download_url")
            or row.get("s3_url")
            or selections.get("efile_file_url")
        ),
        "file_name": _text(row.get("file_name") or selections.get("generated_pdf_name")),
        "description": _text(
            row.get("description")
            or row.get("template_name")
            or selections.get("document_type_name")
        ),
        "size": row.get("size") or selections.get("efile_file_size"),
        "id": _text(row.get("document_id") or row.get("id")),
        "associated_parties": row.get("associated_parties") or [],
        "optional_services": row.get("optional_services") or [],
    }


def _finalize_filing(
    raw: Dict[str, Any],
    *,
    index: int,
    bundle: Any,
    fallback_filing_code: str,
    fallback_doc_type: str,
    logger_: logging.Logger,
) -> Dict[str, Any]:
    """Ensure a filing row has code/doc_type in the live bundle and a unique id."""
    filing_code = _pick_code(
        _text(raw.get("code") or fallback_filing_code),
        getattr(bundle, "filing_codes", None),
        field_name=f"filings[{index}].code",
        logger_=logger_,
    )
    allowed_doc_types = (
        getattr(bundle, "document_types_by_filing_code", {}) or {}
    ).get(filing_code, [])
    doc_type = _pick_code(
        _text(raw.get("doc_type") or fallback_doc_type),
        allowed_doc_types,
        field_name=f"filings[{index}].doc_type",
        logger_=logger_,
    )

    filing: Dict[str, Any] = {
        "code": filing_code,
        "file_name": _text(raw.get("file_name")) or f"filing_{index + 1}.pdf",
        "description": _text(raw.get("description")) or "Court Filing",
        "doc_type": doc_type,
        "file": _text(raw.get("file")),
        "id": _text(raw.get("id")) or f"Filing_{index + 1}",
        "associated_parties": list(raw.get("associated_parties") or []),
    }
    size = _int_or_none(raw.get("size"))
    if size is not None:
        filing["size"] = size
    elif raw.get("size") not in (None, ""):
        # Preserve caller-supplied non-int size for the validator to flag.
        filing["size"] = raw.get("size")

    opt = raw.get("optional_services")
    if isinstance(opt, list) and opt:
        filing["optional_services"] = [dict(o) for o in opt if isinstance(o, dict)]
    return filing


def _resolve_filing_type(
    hint: str, bundle: Any, logger_: logging.Logger
) -> str:
    """Prefer the session hint, else auto-pick when only one option exists."""
    options = list(getattr(bundle, "filing_type_options", None) or [])
    codes = {str((row or {}).get("code") or "").strip() for row in options}
    codes.discard("")
    hint_s = str(hint or "").strip()
    if hint_s and hint_s in codes:
        return hint_s
    if hint_s and not codes:
        return hint_s
    if len(codes) == 1:
        picked = next(iter(codes))
        if hint_s and hint_s != picked:
            logger_.warning(
                "Live assembler filing_type: hint '%s' not in %s, auto-picking '%s'",
                hint_s,
                sorted(codes),
                picked,
            )
        return picked
    # Multiple options and no valid hint -- leave blank so the validator
    # explains that filing_type is required.
    if hint_s:
        logger_.warning(
            "Live assembler filing_type: hint '%s' not in %s, leaving blank",
            hint_s,
            sorted(codes),
        )
    return ""


def _party_type_name_for(bundle: Any, code: str) -> str:
    """Look up ``party_types[i].name`` for a code from a live bundle."""
    target = str(code or "").strip()
    if not target:
        return ""
    for row in getattr(bundle, "party_types", None) or []:
        if str((row or {}).get("code") or "").strip() == target:
            return str((row or {}).get("name") or "").strip()
    return ""


def _fill_missing_required_parties(
    parties: List[Dict[str, Any]],
    bundle: Any,
    *,
    collected_answers: Optional[Dict[str, Any]] = None,
    form_data: Optional[Dict[str, Any]] = None,
    selections: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Add stub entries for every required party type absent from ``parties``.

    Each stub gets:

    - A sequential ``Party_{n}`` id (continuing from the primary party).
    - ``is_business = False`` (the current default).
    - ``first_name`` / ``last_name`` mined from workflow answers using role
      keywords derived from the required party type's ``name`` in the live
      bundle. If nothing matches, the fields are left blank so
      :func:`validate_new_case_payload` still surfaces the issue.
    """
    required = list(getattr(bundle, "required_party_type_codes", lambda: [])())
    present = {str((row or {}).get("type") or "").strip() for row in parties}
    sources = [
        dict(selections or {}),
        dict(collected_answers or {}),
        dict(form_data or {}),
    ]
    result = list(parties)
    next_index = len(parties) + 1
    for req_code in required:
        if req_code in present:
            continue
        type_name = _party_type_name_for(bundle, req_code)
        needles = _needles_for_type_name(type_name)
        first, last = _name_from_maps(sources, needles)
        stub = {key: "" for key in NEW_CASE_PARTY_KEYS}
        stub.update(
            {
                "id": f"Party_{next_index}",
                "type": req_code,
                "is_business": False,
                "additional_attorneys": [],
                "first_name": first,
                "last_name": last,
            }
        )
        result.append(stub)
        next_index += 1
    return result


def _renumber_party_ids(parties: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Assign ``Party_1``, ``Party_2``, ... to every party, in order.

    Idempotent -- callers can safely invoke this after each modification.
    """
    for idx, party in enumerate(parties):
        if isinstance(party, dict):
            party["id"] = f"Party_{idx + 1}"
    return parties


def _explicit_case_parties(selections: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw = selections.get("efile_case_parties")
    if isinstance(raw, list):
        return [dict(row) for row in raw if isinstance(row, dict)]
    return []


def assemble_new_case_efile_data_live(
    *,
    selections: Dict[str, Any],
    bundle: Any,
    collected_answers: Optional[Dict[str, Any]] = None,
    generated_documents: Optional[List[Dict[str, Any]]] = None,
    reference_id: str = "",
) -> Dict[str, Any]:
    """
    Fill the new-case payload using values from a live :class:`CodeBundle`.

    - Every code (filer_type, filing_type, filing_codes, doc_types, party
      types) is resolved against the bundle. A session hint is preferred if
      valid; otherwise the first bundle option is used and a warning logged.
    - N filings are produced from ``selections['efile_filings']`` (explicit)
      or from ``generated_documents`` (one per row), or a single filing built
      from top-level ``selections``.
    - Missing required party types are stubbed so the validator surfaces the
      issue instead of the assembler silently dropping them.
    """
    form_data = mapped_form_data_from_documents(generated_documents)
    explicit_parties = _explicit_case_parties(selections)
    if explicit_parties:
        parties = explicit_parties
    else:
        parties = _new_case_parties(selections, collected_answers, form_data)
    parties = _fill_missing_required_parties(
        parties,
        bundle,
        collected_answers=collected_answers,
        form_data=form_data,
        selections=selections,
    )
    # Give every party a stable ``Party_{n}`` id and ensure ``is_business`` is
    # always a JSON boolean (defaulting to False per the caller's request).
    _renumber_party_ids(parties)
    for party in parties:
        if isinstance(party, dict) and not isinstance(
            party.get("is_business"), bool
        ):
            party["is_business"] = False

    # ``filing_party_id`` must always reference one of ``case_parties[].id``.
    # Prefer an explicit selection; otherwise pin to the primary party.
    party_id_set = {
        str(p.get("id"))
        for p in parties
        if isinstance(p, dict) and p.get("id")
    }
    filing_party_id = _selected_code(
        selections, "filing_party_id", "efile_filing_party_id"
    )
    if filing_party_id not in party_id_set:
        filing_party_id = ""
    if not filing_party_id and parties and parties[0].get("id"):
        filing_party_id = str(parties[0]["id"])

    fallback_filing_code = _selected_code(selections, "filing_code")
    fallback_doc_type = _selected_code(
        selections, "doc_type", "document_type_code"
    )

    filings: List[Dict[str, Any]] = []
    explicit = _explicit_filings(selections)
    if explicit:
        source_rows: List[Dict[str, Any]] = explicit
    elif generated_documents:
        source_rows = [
            _filing_from_document_row(row, selections)
            for row in generated_documents
            if isinstance(row, dict)
        ]
    else:
        source_rows = [_new_case_filing(selections, generated_documents)]

    for idx, raw in enumerate(source_rows):
        filings.append(
            _finalize_filing(
                raw,
                index=idx,
                bundle=bundle,
                fallback_filing_code=fallback_filing_code,
                fallback_doc_type=fallback_doc_type,
                logger_=logger,
            )
        )

    filer_type = _pick_code(
        _text(selections.get("filer_type")),
        getattr(bundle, "filer_types", None),
        field_name="filer_type",
        logger_=logger,
    )
    filing_type = _resolve_filing_type(
        _text(selections.get("filing_type")), bundle, logger
    )

    jurisdiction_code = (
        _text(getattr(bundle, "jurisdiction", {}).get("code"))
        or _selected_code(selections, "jurisdiction_code")
    )
    case_category_code = (
        _text(getattr(bundle, "case_category", {}).get("code"))
        or _selected_code(selections, "case_category_code")
    )
    case_type_code = (
        _text(getattr(bundle, "case_type", {}).get("code"))
        or _selected_code(selections, "case_type_code")
    )
    filing_state = (
        _text(getattr(bundle, "state", "")).lower()
        or _text(selections.get("state_code")).lower()
    )

    data = {key: "" for key in NEW_CASE_EFILE_KEYS}
    data.update(
        {
            "filer_type": filer_type,
            "reference_id": _text(reference_id or selections.get("reference_id")),
            "jurisdiction": jurisdiction_code,
            "payment_account_id": _selected_code(
                selections, "court_payment_account_id"
            ),
            "filings": filings,
            "case_parties": parties,
            "provider_tax": _text(selections.get("provider_tax")),
            "filing_type": filing_type,
            "filing_state": filing_state,
            "case_type": case_type_code,
            "provider_fee": _text(selections.get("provider_fee")),
            "case_category": case_category_code,
            "filing_party_id": filing_party_id,
        }
    )
    return data


def assemble_existing_case_efile_data(
    *,
    selections: Dict[str, Any],
    generated_documents: Optional[List[Dict[str, Any]]] = None,
    reference_id: str = "",
) -> Dict[str, Any]:
    """Fill the existing-case sample shape from session cache/answers only."""
    return {
        "reference_id": _text(reference_id or selections.get("reference_id")),
        "case_tracking_id": _text(selections.get("case_tracking_id")),
        "payment_account_id": _selected_code(selections, "court_payment_account_id"),
        "filing_party_id": _selected_code(
            selections, "filing_party_id", "efile_filing_party_id"
        ),
        "filing_type": _text(selections.get("filing_type")),
        "filings": [_existing_case_filing(selections, generated_documents)],
    }


class EfileMappingLLMOutput(BaseModel):
    data: Dict[str, Any] = Field(default_factory=dict)


def mapped_form_data_from_documents(
    generated_documents: Optional[List[Dict[str, Any]]],
) -> Dict[str, Any]:
    if not generated_documents:
        return {}
    row = dict(generated_documents[-1] or {})
    payload = row.get("request_payload")
    if isinstance(payload, dict) and isinstance(payload.get("form_data"), dict):
        return dict(payload["form_data"])
    fields = row.get("fields")
    if isinstance(fields, dict) and "form_data" not in fields:
        return dict(fields)
    if isinstance(fields, dict) and isinstance(fields.get("form_data"), dict):
        return dict(fields["form_data"])
    return {}


def compact_catalog_options(rows: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    compact: List[Dict[str, str]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        code = str(row.get("code") or "").strip()
        name = str(row.get("name") or row.get("label") or "").strip()
        if code or name:
            compact.append({"code": code, "name": name})
    return compact


def apply_known_efile_facts(
    data: Dict[str, Any],
    *,
    known: Dict[str, Any],
    filing: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Keep LLM-mapped parties/text, but never invent payment, codes, or file URL."""
    merged = dict(data or {})
    for key in (
        "payment_account_id",
        "jurisdiction",
        "filing_state",
        "case_category",
        "case_type",
        "reference_id",
        "case_tracking_id",
        "filer_type",
        "filing_type",
        "provider_tax",
        "provider_fee",
        "filing_party_id",
    ):
        value = known.get(key)
        if value not in (None, ""):
            merged[key] = value

    known_filing = dict(filing or known.get("filing") or {})
    filings = merged.get("filings")
    if not isinstance(filings, list) or not filings:
        merged["filings"] = [known_filing] if known_filing else []
    elif known_filing:
        first = dict(filings[0]) if isinstance(filings[0], dict) else {}
        for key in ("file", "file_name", "code", "doc_type", "size", "id"):
            if known_filing.get(key) not in (None, ""):
                first[key] = known_filing[key]
        if not first.get("description") and known_filing.get("description"):
            first["description"] = known_filing["description"]
        if "associated_parties" not in first:
            first["associated_parties"] = known_filing.get("associated_parties") or []
        filings[0] = first
        merged["filings"] = filings

    parties = merged.get("case_parties")
    if isinstance(parties, list):
        merged["case_parties"] = [dict(row) for row in parties if isinstance(row, dict)]
    if not merged.get("filing_party_id"):
        first_party = (merged.get("case_parties") or [{}])[0]
        if isinstance(first_party, dict) and first_party.get("id"):
            merged["filing_party_id"] = str(first_party["id"])
    return merged


def slim_existing_case_data(data: Dict[str, Any]) -> Dict[str, Any]:
    """Keep only the existing-case e-file keys from the sample body."""
    source = dict(data or {})
    slim = {key: source.get(key, "") for key in EXISTING_CASE_EFILE_KEYS}
    filings = slim.get("filings")
    if not isinstance(filings, list):
        slim["filings"] = []
    return slim


class EfileMappingService:
    """Produce a new-case e-file `data` object from collected filing context."""

    def __init__(self, bedrock: Optional[Bedrock] = None) -> None:
        self.bedrock = bedrock or get_bedrock()

    async def map_new_case(
        self,
        *,
        known_facts: Dict[str, Any],
        catalog_options: Dict[str, Any],
        collected_answers: Dict[str, Any],
        form_data: Dict[str, Any],
        workflow_questions: Optional[List[Any]] = None,
    ) -> Dict[str, Any]:
        return await self._map(
            EFILE_MAPPING_PROMPT,
            NEW_CASE_EFILE_SAMPLE,
            known_facts=known_facts,
            catalog_options=catalog_options,
            collected_answers=collected_answers,
            form_data=form_data,
            workflow_questions=workflow_questions,
        )

    async def map_existing_case(
        self,
        *,
        known_facts: Dict[str, Any],
        catalog_options: Dict[str, Any],
        collected_answers: Dict[str, Any],
        form_data: Dict[str, Any],
        workflow_questions: Optional[List[Any]] = None,
    ) -> Dict[str, Any]:
        return await self._map(
            EXISTING_CASE_EFILE_MAPPING_PROMPT,
            EXISTING_CASE_EFILE_SAMPLE,
            known_facts=known_facts,
            catalog_options=catalog_options,
            collected_answers=collected_answers,
            form_data=form_data,
            workflow_questions=workflow_questions,
        )

    async def _map(
        self,
        prompt_template: str,
        sample: Dict[str, Any],
        *,
        known_facts: Dict[str, Any],
        catalog_options: Dict[str, Any],
        collected_answers: Dict[str, Any],
        form_data: Dict[str, Any],
        workflow_questions: Optional[List[Any]] = None,
    ) -> Dict[str, Any]:
        prompt = format_llm_prompt(
            prompt_template,
            sample_request_json=json.dumps(sample, default=str)[:12000],
            known_facts_json=json.dumps(known_facts or {}, default=str)[:12000],
            catalog_options_json=json.dumps(catalog_options or {}, default=str)[:12000],
            workflow_questions_json=json.dumps(
                list(workflow_questions or []), default=str
            )[:12000],
            collected_answers_json=json.dumps(
                collected_answers or {}, default=str
            )[:12000],
            form_data_json=json.dumps(form_data or {}, default=str)[:12000],
        )
        mapped = await self._ask_llm(prompt)
        data = mapped.get("data") if isinstance(mapped.get("data"), dict) else mapped
        if not isinstance(data, dict) or not data:
            return {}
        return data

    async def _ask_llm(self, prompt: str) -> Dict[str, Any]:
        try:
            parsed = await self.bedrock.invoke_structured_prompt(
                prompt, EfileMappingLLMOutput
            )
            return {"data": dict(parsed.data or {})}
        except Exception as exc:  # noqa: BLE001
            logger.warning("E-file mapping structured invoke failed: %s", exc)

        try:
            body = {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 8192,
                "temperature": 0,
                "messages": [{"role": "user", "content": prompt}],
            }
            raw = await self.bedrock.invoke_prompt_with_timeout(body)
            from app.agents.utils.json_utils import parse_llm_json

            parsed = parse_llm_json(raw)
            if isinstance(parsed, dict):
                return parsed
        except Exception as exc:  # noqa: BLE001
            logger.warning("E-file mapping prompt fallback failed: %s", exc)
        return {}
