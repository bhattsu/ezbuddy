"""Tests for :mod:`app.services.efile_validator`."""

from __future__ import annotations

import pytest

from app.services.efile_validator import (
    EFilePayloadValidationError,
    validate_existing_case_payload,
    validate_new_case_payload,
)
from app.services.uslegalpro_codes_service import CodeBundle


def _bundle(**overrides) -> CodeBundle:
    """A bundle matching the guide's Harris/Divorce No Children sample."""
    base = dict(
        state="tx",
        jurisdiction={"code": "harris:dc", "name": "Harris DC"},
        case_category={"code": "131370", "name": "Family"},
        case_type={"code": "209421", "name": "Divorce No Children"},
        filer_types=[{"code": "54325", "name": "Attorney"}],
        filing_type_options=[{"code": "EFile", "name": "EFile"}],
        filing_codes=[
            {"code": "209523", "name": "Petition"},
            {"code": "209524", "name": "Exhibit"},
        ],
        party_types=[
            {"code": "53024", "name": "Petitioner", "is_required": True},
            {"code": "271011", "name": "Respondent", "is_required": True},
        ],
        document_types_by_filing_code={
            "209523": [{"code": "53689", "name": "Petition-Divorce"}],
            "209524": [{"code": "53690", "name": "Exhibit A"}],
        },
    )
    base.update(overrides)
    return CodeBundle(**base)


def _payload(**overrides) -> dict:
    data = {
        "filer_type": "54325",
        "reference_id": "DRAFT-2026-10038",
        "jurisdiction": "harris:dc",
        "payment_account_id": "CC_pay",
        "filings": [
            {
                "code": "209523",
                "file_name": "complaint.pdf",
                "description": "Petition",
                "doc_type": "53689",
                "file": "https://ex.test/complaint.pdf",
                "size": 123261,
                "associated_parties": [],
                "id": "Filing_1",
            }
        ],
        "case_parties": [
            {
                "country": "US",
                "city": "Houston",
                "type": "53024",
                "zip_code": "77002",
                "address_line_1": "1200 Baker Street",
                "id": "Party_1",
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
                "id": "Party_2",
                "first_name": "JOHN",
                "is_business": False,
                "last_name": "DOE",
                "additional_attorneys": [],
            },
        ],
        "provider_tax": "0.25",
        "filing_type": "EFile",
        "filing_state": "tx",
        "case_type": "209421",
        "provider_fee": "2.99",
        "case_category": "131370",
        "filing_party_id": "Party_1",
    }
    data.update(overrides)
    return {"data": data}


def test_valid_payload_passes() -> None:
    result = validate_new_case_payload(_payload(), _bundle(), strict=False)
    assert result.is_valid, result.errors


def test_strict_raises_on_error() -> None:
    payload = _payload(filer_type="wrong")
    with pytest.raises(EFilePayloadValidationError) as excinfo:
        validate_new_case_payload(payload, _bundle(), strict=True)
    assert any("filer_type" in err for err in excinfo.value.errors)


def test_missing_filer_type_when_bundle_has_options() -> None:
    payload = _payload(filer_type="")
    result = validate_new_case_payload(payload, _bundle(), strict=False)
    assert not result.is_valid
    assert any("filer_type" in err for err in result.errors)


def test_route_state_mismatch() -> None:
    result = validate_new_case_payload(
        _payload(), _bundle(), strict=False, route_state="ca"
    )
    assert not result.is_valid
    assert any("filing_state" in err for err in result.errors)


def test_filing_state_must_be_lowercase() -> None:
    payload = _payload(filing_state="TX")
    result = validate_new_case_payload(payload, _bundle(), strict=False)
    assert not result.is_valid
    assert any("lowercase" in err for err in result.errors)


def test_wrong_doc_type_for_filing_code() -> None:
    payload = _payload()
    # 53690 belongs to filing code 209524, not 209523.
    payload["data"]["filings"][0]["doc_type"] = "53690"
    result = validate_new_case_payload(payload, _bundle(), strict=False)
    assert not result.is_valid
    assert any(
        "doc_type" in err and "209523" in err for err in result.errors
    )


def test_doc_type_valid_for_correct_filing_in_multi_filing() -> None:
    payload = _payload()
    payload["data"]["filings"].append(
        {
            "code": "209524",
            "file_name": "exhibit.pdf",
            "description": "Exhibit A",
            "doc_type": "53690",
            "file": "https://ex.test/exhibit.pdf",
            "size": 42000,
            "associated_parties": [],
            "id": "Filing_2",
        }
    )
    result = validate_new_case_payload(payload, _bundle(), strict=False)
    assert result.is_valid, result.errors


def test_cross_filing_doc_type_mismatch() -> None:
    """The 2nd filing's doc_type only belongs to the 1st filing's code."""
    payload = _payload()
    payload["data"]["filings"].append(
        {
            "code": "209524",
            "file_name": "exhibit.pdf",
            "description": "Exhibit A",
            "doc_type": "53689",  # belongs to 209523, NOT 209524
            "file": "https://ex.test/exhibit.pdf",
            "size": 42000,
            "associated_parties": [],
            "id": "Filing_2",
        }
    )
    result = validate_new_case_payload(payload, _bundle(), strict=False)
    assert not result.is_valid
    assert any("209524" in err and "53689" in err for err in result.errors)


def test_duplicate_filing_id_across_multi_filing() -> None:
    payload = _payload()
    payload["data"]["filings"].append(
        {
            "code": "209524",
            "file_name": "exhibit.pdf",
            "description": "Exhibit A",
            "doc_type": "53690",
            "file": "https://ex.test/exhibit.pdf",
            "size": 42000,
            "associated_parties": [],
            "id": "Filing_1",  # duplicate!
        }
    )
    result = validate_new_case_payload(payload, _bundle(), strict=False)
    assert not result.is_valid
    assert any(
        "duplicated" in err and "Filing_1" in err for err in result.errors
    )


def test_duplicate_party_id() -> None:
    payload = _payload()
    payload["data"]["case_parties"][1]["id"] = "Party_1"
    result = validate_new_case_payload(payload, _bundle(), strict=False)
    assert not result.is_valid
    assert any(
        "duplicated" in err and "Party_1" in err for err in result.errors
    )


def test_orphan_filing_party_id() -> None:
    payload = _payload(filing_party_id="Party_missing")
    result = validate_new_case_payload(payload, _bundle(), strict=False)
    assert not result.is_valid
    assert any(
        "filing_party_id" in err and "Party_missing" in err
        for err in result.errors
    )


def test_orphan_associated_party() -> None:
    payload = _payload()
    payload["data"]["filings"][0]["associated_parties"] = ["Party_missing"]
    result = validate_new_case_payload(payload, _bundle(), strict=False)
    assert not result.is_valid
    assert any(
        "associated_parties" in err and "Party_missing" in err
        for err in result.errors
    )


def test_required_party_type_missing() -> None:
    payload = _payload()
    # drop the required "271011" respondent
    payload["data"]["case_parties"] = [payload["data"]["case_parties"][0]]
    result = validate_new_case_payload(payload, _bundle(), strict=False)
    assert not result.is_valid
    assert any(
        "required party type" in err and "271011" in err
        for err in result.errors
    )


def test_non_string_numeric_code_is_flagged() -> None:
    payload = _payload()
    payload["data"]["case_type"] = 209421  # int, not string
    result = validate_new_case_payload(payload, _bundle(), strict=False)
    assert not result.is_valid
    assert any("case_type" in err and "string" in err for err in result.errors)


def test_is_business_must_be_bool() -> None:
    payload = _payload()
    payload["data"]["case_parties"][0]["is_business"] = "false"
    result = validate_new_case_payload(payload, _bundle(), strict=False)
    assert not result.is_valid
    assert any("is_business" in err for err in result.errors)


def test_size_must_be_positive_int() -> None:
    payload = _payload()
    payload["data"]["filings"][0]["size"] = "big"
    result = validate_new_case_payload(payload, _bundle(), strict=False)
    assert not result.is_valid
    assert any("size" in err for err in result.errors)


def test_missing_file_url_and_name() -> None:
    payload = _payload()
    payload["data"]["filings"][0]["file"] = ""
    payload["data"]["filings"][0]["file_name"] = ""
    result = validate_new_case_payload(payload, _bundle(), strict=False)
    assert not result.is_valid
    assert any("file" in err for err in result.errors)
    assert any("file_name" in err for err in result.errors)


def test_filing_code_not_in_bundle() -> None:
    payload = _payload()
    payload["data"]["filings"][0]["code"] = "999999"
    result = validate_new_case_payload(payload, _bundle(), strict=False)
    assert not result.is_valid
    assert any("filings[0].code" in err for err in result.errors)


def test_missing_reference_id_and_payment_account() -> None:
    payload = _payload(reference_id="", payment_account_id="")
    result = validate_new_case_payload(payload, _bundle(), strict=False)
    assert not result.is_valid
    assert any("reference_id" in err for err in result.errors)
    assert any("payment_account_id" in err for err in result.errors)


def test_jurisdiction_mismatch_with_bundle() -> None:
    payload = _payload(jurisdiction="dallas:dc")
    result = validate_new_case_payload(payload, _bundle(), strict=False)
    assert not result.is_valid
    assert any(
        "jurisdiction" in err and "dallas:dc" in err for err in result.errors
    )


# ---------------------------------------------------------------------------
# existing-case validator


def _existing_payload(**overrides) -> dict:
    data = {
        "reference_id": "EFILE-1",
        "case_tracking_id": "tyler_harris:dc~abc~CT",
        "payment_account_id": "CC_pay",
        "filing_party_id": "Party_from_case",
        "filing_type": "EFile",
        "filings": [
            {
                "code": "29736",
                "file_name": "notice.pdf",
                "description": "Notice",
                "doc_type": "44889",
                "file": "https://ex.test/notice.pdf",
            }
        ],
    }
    data.update(overrides)
    return {"data": data}


def test_existing_case_valid() -> None:
    result = validate_existing_case_payload(_existing_payload(), strict=False)
    assert result.is_valid, result.errors


def test_existing_case_missing_case_tracking_id() -> None:
    result = validate_existing_case_payload(
        _existing_payload(case_tracking_id=""), strict=False
    )
    assert not result.is_valid
    assert any("case_tracking_id" in err for err in result.errors)


def test_existing_case_missing_filing_fields() -> None:
    payload = _existing_payload()
    payload["data"]["filings"][0]["file"] = ""
    payload["data"]["filings"][0]["doc_type"] = ""
    result = validate_existing_case_payload(payload, strict=False)
    assert not result.is_valid
    assert any("file" in err for err in result.errors)
    assert any("doc_type" in err for err in result.errors)


def test_existing_case_empty_filings_list() -> None:
    payload = _existing_payload()
    payload["data"]["filings"] = []
    result = validate_existing_case_payload(payload, strict=False)
    assert not result.is_valid
    assert any("filings" in err for err in result.errors)
