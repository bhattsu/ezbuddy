"""Strict validators for the ``/v2/{state}/efile`` payload.

The validator is the final gate before the platform ``submit_efile`` call. It
enforces the checklist from ``efile-payload-guide.md`` -- every code in the
payload must belong to the live :class:`CodeBundle` walked from the selected
case type, and every structural rule (unique party ids, filing party
cross-references, non-empty file/reference/payment fields) must hold.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence

from app.services.uslegalpro_codes_service import CodeBundle

# Kept in sync with ``efile_mapping_service.EXISTING_CASE_EFILE_KEYS`` so a
# circular import is not required.
EXISTING_CASE_EFILE_KEYS: tuple[str, ...] = (
    "reference_id",
    "case_tracking_id",
    "payment_account_id",
    "filing_party_id",
    "filing_type",
    "filings",
)

# Numeric-looking codes must remain strings so leading zeros survive.
STRING_CODE_FIELDS: tuple[str, ...] = (
    "jurisdiction",
    "case_category",
    "case_type",
    "filer_type",
    "filing_type",
)

STRING_FILING_CODE_FIELDS: tuple[str, ...] = ("code", "doc_type")


@dataclass
class ValidationResult:
    """Report produced by :func:`validate_new_case_payload`."""

    is_valid: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
        }

    def format(self) -> str:
        parts = [f"is_valid={self.is_valid}"]
        if self.errors:
            parts.append("errors:\n  - " + "\n  - ".join(self.errors))
        if self.warnings:
            parts.append("warnings:\n  - " + "\n  - ".join(self.warnings))
        return "\n".join(parts)


class EFilePayloadValidationError(Exception):
    """Raised by strict validators when the payload has one or more errors."""

    def __init__(self, result: ValidationResult):
        self.result = result
        message = "; ".join(result.errors) or "E-file payload validation failed"
        super().__init__(message)

    @property
    def errors(self) -> List[str]:
        return list(self.result.errors)

    @property
    def warnings(self) -> List[str]:
        return list(self.result.warnings)


# ---------------------------------------------------------------------------
# helpers


def _data(payload: Any) -> Dict[str, Any]:
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, dict):
            return data
        return payload
    return {}


def _text(value: Any) -> str:
    if value in (None, ""):
        return ""
    return str(value).strip()


def _is_str_code(value: Any) -> bool:
    return isinstance(value, str) and value.strip() != ""


def _codes(rows: Iterable[Dict[str, Any]]) -> List[str]:
    return sorted(
        {
            str((row or {}).get("code") or "").strip()
            for row in rows or []
            if str((row or {}).get("code") or "").strip()
        }
    )


# ---------------------------------------------------------------------------
# new-case validator


def validate_new_case_payload(
    payload: Any,
    bundle: CodeBundle,
    *,
    strict: bool = True,
    route_state: Optional[str] = None,
) -> ValidationResult:
    """
    Validate a new-case ``/efile`` payload against a live :class:`CodeBundle`.

    ``strict=True`` raises :class:`EFilePayloadValidationError` if any rule
    fails; ``strict=False`` returns the :class:`ValidationResult` unchanged.
    ``route_state`` (e.g. ``"tx"``) is compared to ``data.filing_state`` when
    provided.
    """
    errors: List[str] = []
    warnings: List[str] = []
    data = _data(payload)

    if not isinstance(data, dict) or not data:
        errors.append("payload must contain a non-empty 'data' object")
        return _finalize(ValidationResult(False, errors, warnings), strict)

    # ----- 1. top-level scalar codes -----
    for field_name in STRING_CODE_FIELDS:
        raw_value = data.get(field_name)
        if raw_value in (None, ""):
            errors.append(f"data.{field_name} is required")
            continue
        if not _is_str_code(raw_value):
            errors.append(
                f"data.{field_name} must be a string (got {type(raw_value).__name__})"
            )

    # ----- 2. dependency chain -----
    jurisdiction_code = _text(data.get("jurisdiction"))
    expected_jur = _text(bundle.jurisdiction.get("code"))
    if expected_jur and jurisdiction_code and jurisdiction_code != expected_jur:
        errors.append(
            f"data.jurisdiction '{jurisdiction_code}' does not match the walked "
            f"jurisdiction '{expected_jur}'"
        )

    category_code = _text(data.get("case_category"))
    expected_cat = _text(bundle.case_category.get("code"))
    if expected_cat and category_code and category_code != expected_cat:
        errors.append(
            f"data.case_category '{category_code}' does not match the walked "
            f"category '{expected_cat}'"
        )

    case_type_code = _text(data.get("case_type"))
    expected_type = _text(bundle.case_type.get("code"))
    if expected_type and case_type_code and case_type_code != expected_type:
        errors.append(
            f"data.case_type '{case_type_code}' does not match the walked "
            f"case type '{expected_type}'"
        )

    # ----- 3. filer_type / filing_type against live options -----
    filer_type = _text(data.get("filer_type"))
    if filer_type and not bundle.has_filer_type(filer_type):
        errors.append(
            f"data.filer_type '{filer_type}' is not one of the filer types for "
            f"case type {expected_type or case_type_code} "
            f"(allowed: {_codes(bundle.filer_types) or '<none>'})"
        )
    elif not filer_type and bundle.filer_types:
        errors.append(
            "data.filer_type is required; case type exposes filer types "
            f"{_codes(bundle.filer_types)}"
        )

    filing_type = _text(data.get("filing_type"))
    if filing_type and bundle.filing_type_options and not bundle.has_filing_type(
        filing_type
    ):
        errors.append(
            f"data.filing_type '{filing_type}' is not a valid filing type "
            f"(allowed: {_codes(bundle.filing_type_options)})"
        )

    # ----- 4. filing_state -----
    filing_state = _text(data.get("filing_state"))
    if filing_state and filing_state != filing_state.lower():
        errors.append(
            f"data.filing_state '{filing_state}' must be lowercase"
        )
    normalized_state = filing_state.lower()
    if route_state and normalized_state and normalized_state != route_state.strip().lower():
        errors.append(
            f"data.filing_state '{normalized_state}' does not match route state "
            f"'{route_state.strip().lower()}'"
        )
    if bundle.state and normalized_state and normalized_state != bundle.state:
        errors.append(
            f"data.filing_state '{normalized_state}' does not match the walked "
            f"state '{bundle.state}'"
        )

    # ----- 5. reference / payment scalar values -----
    if not _text(data.get("reference_id")):
        errors.append("data.reference_id is required")
    if not _text(data.get("payment_account_id")):
        errors.append("data.payment_account_id is required")

    # ----- 6. case_parties -----
    parties = data.get("case_parties")
    if not isinstance(parties, list) or not parties:
        errors.append("data.case_parties must be a non-empty list")
        parties = []

    party_ids: List[str] = []
    party_types_in_payload: List[str] = []
    for idx, party in enumerate(parties):
        prefix = f"data.case_parties[{idx}]"
        if not isinstance(party, dict):
            errors.append(f"{prefix} must be an object")
            continue
        pid = _text(party.get("id"))
        if not pid:
            errors.append(f"{prefix}.id is required")
        else:
            if pid in party_ids:
                errors.append(f"{prefix}.id '{pid}' is duplicated in case_parties")
            party_ids.append(pid)

        ptype = _text(party.get("type"))
        if not ptype:
            errors.append(f"{prefix}.type is required")
        else:
            party_types_in_payload.append(ptype)
            if bundle.party_types and not bundle.has_party_type(ptype):
                errors.append(
                    f"{prefix}.type '{ptype}' is not a valid party type "
                    f"(allowed: {_codes(bundle.party_types)})"
                )

        # is_business is a JSON boolean per the guide.
        if "is_business" in party and not isinstance(party.get("is_business"), bool):
            errors.append(
                f"{prefix}.is_business must be a JSON boolean, not "
                f"{type(party.get('is_business')).__name__}"
            )

        if not isinstance(party.get("additional_attorneys", []), list):
            errors.append(f"{prefix}.additional_attorneys must be a list")

        if party.get("is_business") is False:
            if not (_text(party.get("first_name")) or _text(party.get("last_name"))):
                errors.append(
                    f"{prefix} requires first_name or last_name when is_business=false"
                )
        elif party.get("is_business") is True:
            if not _text(party.get("business_name")):
                errors.append(
                    f"{prefix}.business_name is required when is_business=true"
                )

    # required party types
    required_codes = bundle.required_party_type_codes()
    for req_code in required_codes:
        if req_code not in party_types_in_payload:
            errors.append(
                f"required party type '{req_code}' is missing from case_parties"
            )

    # ----- 7. filing_party_id -----
    filing_party_id = _text(data.get("filing_party_id"))
    if not filing_party_id:
        errors.append("data.filing_party_id is required")
    elif party_ids and filing_party_id not in party_ids:
        errors.append(
            f"data.filing_party_id '{filing_party_id}' does not match any "
            f"case_parties[].id (available: {party_ids})"
        )

    # ----- 8. filings -----
    filings = data.get("filings")
    if not isinstance(filings, list) or not filings:
        errors.append("data.filings must be a non-empty list")
        filings = []

    filing_ids: List[str] = []
    for idx, filing in enumerate(filings):
        prefix = f"data.filings[{idx}]"
        if not isinstance(filing, dict):
            errors.append(f"{prefix} must be an object")
            continue

        fid = _text(filing.get("id"))
        if not fid:
            errors.append(f"{prefix}.id is required")
        else:
            if fid in filing_ids:
                errors.append(f"{prefix}.id '{fid}' is duplicated in filings")
            filing_ids.append(fid)

        for field_name in STRING_FILING_CODE_FIELDS:
            value = filing.get(field_name)
            if value in (None, ""):
                errors.append(f"{prefix}.{field_name} is required")
            elif not _is_str_code(value):
                errors.append(
                    f"{prefix}.{field_name} must be a string (got {type(value).__name__})"
                )

        filing_code = _text(filing.get("code"))
        if filing_code and bundle.filing_codes and not bundle.has_filing_code(filing_code):
            errors.append(
                f"{prefix}.code '{filing_code}' is not a valid filing code for "
                f"case type {expected_type or case_type_code} "
                f"(sample allowed: {_codes(bundle.filing_codes)[:10]})"
            )

        doc_type = _text(filing.get("doc_type"))
        if filing_code and doc_type:
            allowed_docs = bundle.document_types_by_filing_code.get(filing_code)
            if allowed_docs is None:
                warnings.append(
                    f"{prefix}.doc_type '{doc_type}' could not be validated: "
                    f"no document types were pre-fetched for filing code {filing_code}"
                )
            elif not _code_in(allowed_docs, doc_type):
                errors.append(
                    f"{prefix}.doc_type '{doc_type}' is not a valid document type "
                    f"for filing code '{filing_code}' "
                    f"(allowed: {_codes(allowed_docs) or '<none>'})"
                )

        if not _text(filing.get("file")):
            errors.append(f"{prefix}.file (URL) is required")
        if not _text(filing.get("file_name")):
            errors.append(f"{prefix}.file_name is required")

        size = filing.get("size")
        if size in (None, ""):
            errors.append(f"{prefix}.size is required")
        elif not isinstance(size, int) or size <= 0:
            errors.append(
                f"{prefix}.size must be a positive integer (got {size!r})"
            )

        assoc = filing.get("associated_parties", [])
        if not isinstance(assoc, list):
            errors.append(f"{prefix}.associated_parties must be a list")
        else:
            for a_idx, ref in enumerate(assoc):
                ref_id = _text(ref)
                if not ref_id:
                    errors.append(
                        f"{prefix}.associated_parties[{a_idx}] is empty"
                    )
                elif party_ids and ref_id not in party_ids:
                    errors.append(
                        f"{prefix}.associated_parties[{a_idx}] '{ref_id}' does not "
                        f"match any case_parties[].id"
                    )

        # optional services (only validated when a bundle carries them)
        opt_services = filing.get("optional_services") or []
        if isinstance(opt_services, list) and filing_code:
            allowed_opts = bundle.optional_services_by_filing_code.get(filing_code)
            for o_idx, service in enumerate(opt_services):
                if not isinstance(service, dict):
                    errors.append(
                        f"{prefix}.optional_services[{o_idx}] must be an object"
                    )
                    continue
                svc_code = _text(service.get("code"))
                if not svc_code:
                    errors.append(
                        f"{prefix}.optional_services[{o_idx}].code is required"
                    )
                elif allowed_opts is not None and not _code_in(allowed_opts, svc_code):
                    errors.append(
                        f"{prefix}.optional_services[{o_idx}].code '{svc_code}' is "
                        f"not a valid optional service for filing code "
                        f"'{filing_code}' (allowed: {_codes(allowed_opts) or '<none>'})"
                    )

    result = ValidationResult(is_valid=not errors, errors=errors, warnings=warnings)
    return _finalize(result, strict)


# ---------------------------------------------------------------------------
# existing-case validator (structural only)


def validate_existing_case_payload(
    payload: Any,
    *,
    strict: bool = True,
    required_keys: Sequence[str] = EXISTING_CASE_EFILE_KEYS,
) -> ValidationResult:
    """
    Structural validator for an existing-case payload.

    Rules follow ``efile-existing-case-payload-guide.md``: every required
    key must be non-empty, ``filings`` must be a non-empty list, and each
    filing must carry ``code``, ``doc_type``, ``file``, ``file_name``.
    """
    errors: List[str] = []
    warnings: List[str] = []
    data = _data(payload)

    if not isinstance(data, dict) or not data:
        errors.append("payload must contain a non-empty 'data' object")
        return _finalize(ValidationResult(False, errors, warnings), strict)

    for key in required_keys:
        value = data.get(key)
        if key == "filings":
            if not isinstance(value, list) or not value:
                errors.append("data.filings must be a non-empty list")
            continue
        if value in (None, ""):
            errors.append(f"data.{key} is required")

    filings = data.get("filings") if isinstance(data.get("filings"), list) else []
    for idx, filing in enumerate(filings):
        prefix = f"data.filings[{idx}]"
        if not isinstance(filing, dict):
            errors.append(f"{prefix} must be an object")
            continue
        for field_name in ("code", "doc_type", "file", "file_name"):
            if not _text(filing.get(field_name)):
                errors.append(f"{prefix}.{field_name} is required")

    result = ValidationResult(is_valid=not errors, errors=errors, warnings=warnings)
    return _finalize(result, strict)


# ---------------------------------------------------------------------------
# internal helpers


def _code_in(rows: Iterable[Dict[str, Any]], code: str) -> bool:
    target = str(code or "").strip()
    if not target:
        return False
    for row in rows or []:
        if str((row or {}).get("code") or "").strip() == target:
            return True
    return False


def _finalize(result: ValidationResult, strict: bool) -> ValidationResult:
    if strict and not result.is_valid:
        raise EFilePayloadValidationError(result)
    return result
