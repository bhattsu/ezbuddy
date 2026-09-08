"""Tests for new-case e-file mapping and existing-case payload shape."""

from __future__ import annotations

import pytest

from app.services.efile_mapping_service import (
    apply_known_efile_facts,
    mapped_form_data_from_documents,
)
from app.services.uslegalpro_efile_service import (
    EFileSubmitResult,
    USLegalProEFileService,
    format_efile_success_message,
)


def test_apply_known_efile_facts_keeps_mapped_parties():
    merged = apply_known_efile_facts(
        {
            "filer_type": "copied-from-sample",
            "jurisdiction": "wrong",
            "case_parties": [
                {
                    "first_name": "Jane",
                    "last_name": "Doe",
                    "id": "Party_1",
                    "type": "53024",
                }
            ],
            "filings": [{"file_name": "old.pdf", "file": "https://old.example/file.pdf"}],
        },
        known={
            "payment_account_id": "CC_selected",
            "jurisdiction": "harris:dc",
            "filing_state": "tx",
            "case_category": "131370",
            "case_type": "209421",
            "reference_id": "EFILE-1",
            "filer_type": "",
        },
        filing={
            "file": "https://generated.example/case_document.pdf",
            "file_name": "case_document.pdf",
            "code": "209523",
            "doc_type": "53689",
        },
    )
    assert merged["payment_account_id"] == "CC_selected"
    assert merged["jurisdiction"] == "harris:dc"
    assert merged["case_parties"][0]["first_name"] == "Jane"
    assert merged["filings"][0]["file"] == "https://generated.example/case_document.pdf"
    assert merged["filings"][0]["code"] == "209523"
    assert merged["filing_party_id"] == "Party_1"


def test_mapped_form_data_from_documents():
    docs = [
        {
            "request_payload": {
                "form_data": {"_PLAINTIFF_1_FULL_NAME": "Jane Doe"}
            }
        }
    ]
    assert mapped_form_data_from_documents(docs) == {
        "_PLAINTIFF_1_FULL_NAME": "Jane Doe"
    }


def test_format_efile_success_message():
    message = format_efile_success_message(
        EFileSubmitResult(
            envelope_id="326710",
            reference_id="EFILE-1",
            status="submitted",
            message="ok",
            raw={},
            case_tracking_id="tyler_harris:dc~abc~CT",
            filings=[
                {
                    "code": "209523",
                    "id": "77c253a9-eaf3-47ea-93da-5fa67c6ab187",
                    "status": "submitted",
                }
            ],
        )
    )
    assert "E-filed successfully." in message
    assert "Envelope ID: 326710" in message
    assert "Case tracking ID: tyler_harris:dc~abc~CT" in message
    assert "77c253a9-eaf3-47ea-93da-5fa67c6ab187" in message


class _FakeMapper:
    async def map_new_case(self, **kwargs):
        return {
            "filer_type": "54325",
            "case_parties": [
                {
                    "id": "Party_abc",
                    "type": "53024",
                    "first_name": "JANE",
                    "last_name": "DOE",
                    "city": "Houston",
                    "state": "TX",
                    "is_business": False,
                    "additional_attorneys": [],
                }
            ],
            "filings": [{"description": "Petition"}],
        }

    async def map_existing_case(self, **kwargs):
        raise AssertionError("new-case path must not call the existing-case mapper")


class _ExistingFakeMapper:
    async def map_new_case(self, **kwargs):
        raise AssertionError("existing-case path must not call the new-case mapper")

    async def map_existing_case(self, **kwargs):
        return {
            "reference_id": "should-be-overlaid",
            "case_tracking_id": "wrong-track",
            "payment_account_id": "wrong-pay",
            "filing_party_id": "",
            "filing_type": "",
            "filings": [
                {
                    "description": "Notice of Appeal",
                    "file": "https://old.example/notice.pdf",
                }
            ],
        }


class _EmptyCodes:
    async def fetch_by_url(self, url):
        return []

    async def get_party_types_for_case_type(self, *args, **kwargs):
        return []


def _base_selections(**extra):
    data = {
        "state_code": "tx",
        "jurisdiction_code": "harris:dc",
        "case_category_code": "131370",
        "case_type_code": "209421",
        "court_payment_account_id": "CC_01eb044f-9e41-4966-93e2-31a5f8d9c01b",
        "doc_type": "53689",
        "filing_code": "209523",
        "efile_file_url": "https://generated.example/case_document.pdf",
        "party_type_code": "53024",
        "first_name": "Jane",
        "last_name": "Doe",
        "reference_id": "EFILE-TEST-1",
    }
    data.update(extra)
    return data


@pytest.mark.asyncio
async def test_existing_case_payload_uses_llm_mapping():
    service = USLegalProEFileService(
        codes_service=_EmptyCodes(),
        mapping_service=_ExistingFakeMapper(),
    )
    payload = await service.build_submit_payload(
        mode="filing_existing",
        selections=_base_selections(
            case_tracking_id="existing-track-1",
            filing_party_id="Party_from_case",
        ),
        collected_answers={"_PLAINTIFF_1_FULL_NAME": "Jane Doe"},
        generated_documents=[
            {
                "file_name": "case_document.pdf",
                "download_url": "https://generated.example/case_document.pdf",
            }
        ],
    )
    data = payload["data"]
    assert set(data.keys()) == {
        "reference_id",
        "case_tracking_id",
        "payment_account_id",
        "filing_party_id",
        "filing_type",
        "filings",
    }
    assert data["case_tracking_id"] == "existing-track-1"
    assert data["payment_account_id"] == "CC_01eb044f-9e41-4966-93e2-31a5f8d9c01b"
    assert data["filing_party_id"] == "Party_from_case"
    assert data["filing_type"] == ""
    assert "case_parties" not in data
    assert data["filings"][0]["file"] == "https://generated.example/case_document.pdf"
    assert data["filings"][0]["file_name"] == "case_document.pdf"
    assert data["filings"][0]["description"] == "Notice of Appeal"


@pytest.mark.asyncio
async def test_new_case_payload_uses_llm_mapping():
    service = USLegalProEFileService(
        codes_service=_EmptyCodes(),
        mapping_service=_FakeMapper(),
    )
    payload = await service.build_submit_payload(
        mode="filing_new",
        selections=_base_selections(),
        collected_answers={"_PLAINTIFF_1_FULL_NAME": "Jane Doe"},
        generated_documents=[
            {
                "file_name": "case_document.pdf",
                "download_url": "https://generated.example/case_document.pdf",
                "request_payload": {
                    "form_data": {"_PLAINTIFF_1_FULL_NAME": "Jane Doe"}
                },
            }
        ],
    )
    data = payload["data"]
    assert data["jurisdiction"] == "harris:dc"
    assert data["payment_account_id"] == "CC_01eb044f-9e41-4966-93e2-31a5f8d9c01b"
    assert data["case_type"] == "209421"
    assert data["case_parties"][0]["first_name"] == "JANE"
    assert data["filings"][0]["file"] == "https://generated.example/case_document.pdf"
    assert data["filings"][0]["description"] == "Petition"
    assert data["filing_party_id"] == "Party_abc"
