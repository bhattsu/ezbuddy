"""Tests for cache-only e-file mapping and existing-case payload shape."""

from __future__ import annotations

import pytest

from app.services.efile_mapping_service import (
    apply_known_efile_facts,
    assemble_new_case_efile_data,
    mapped_form_data_from_documents,
)
from app.services.uslegalpro_efile_service import (
    EFileSubmitResult,
    USLegalProEFileService,
    format_efile_preview_message,
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


def test_format_efile_preview_message_asks_to_confirm():
    message = format_efile_preview_message({"data": {"reference_id": "DRAFT-2026-10034"}})
    assert "This is the e-file request JSON" in message
    assert "yes if we should e-file" in message
    assert "DRAFT-2026-10034" in message


def test_assemble_leaves_missing_fields_empty():
    data = assemble_new_case_efile_data(
        selections={"state_code": "tx"},
        collected_answers={},
        generated_documents=[],
        reference_id="DRAFT-2026-10034",
    )
    assert data["jurisdiction"] == ""
    assert data["case_type"] == ""
    assert data["case_category"] == ""
    assert data["filer_type"] == ""
    assert data["filing_type"] == ""
    assert data["provider_tax"] == ""
    assert data["provider_fee"] == ""
    assert data["filings"][0]["code"] == ""
    assert data["filings"][0]["doc_type"] == ""
    assert data["filings"][0]["file"] == ""
    assert data["filings"][0]["size"] == ""
    assert data["case_parties"][0]["first_name"] == ""
    assert data["case_parties"][0]["type"] == ""
    assert data["reference_id"] == "DRAFT-2026-10034"
    assert data["filing_state"] == "tx"


class _EmptyCodes:
    async def fetch_by_url(self, url):
        raise AssertionError("must not live-fetch catalog for e-file mapping")

    async def get_party_types_for_case_type(self, *args, **kwargs):
        raise AssertionError("must not live-fetch catalog for e-file mapping")


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
        "document_type_name": "Petition",
    }
    data.update(extra)
    return data


@pytest.mark.asyncio
async def test_existing_case_payload_uses_session_cache_only():
    service = USLegalProEFileService(codes_service=_EmptyCodes())
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
    assert data["filings"][0]["code"] == "209523"
    assert data["filings"][0]["doc_type"] == "53689"


@pytest.mark.asyncio
async def test_new_case_payload_uses_session_cache_only():
    service = USLegalProEFileService(codes_service=_EmptyCodes())
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
    assert data["case_category"] == "131370"
    assert data["case_parties"][0]["first_name"] == "Jane"
    assert data["case_parties"][0]["type"] == "53024"
    assert data["filings"][0]["file"] == "https://generated.example/case_document.pdf"
    assert data["filings"][0]["code"] == "209523"
    assert data["filings"][0]["doc_type"] == "53689"
    assert data["filings"][0]["description"] == "Petition"
    assert data["filer_type"] == ""
    assert data["filing_type"] == ""


@pytest.mark.asyncio
async def test_build_submit_payload_uses_confirmed_override():
    service = USLegalProEFileService(codes_service=_EmptyCodes())
    preview = {"data": {"reference_id": "PREVIEW-1", "payment_account_id": "CC_1"}}
    payload = await service.build_submit_payload(
        mode="filing_new",
        selections=_base_selections(efile_payload_override=preview),
        collected_answers={},
        generated_documents=[],
    )
    assert payload == preview
