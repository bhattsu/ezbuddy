from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

import pytest

from app.services.uslegalpro_codes_service import CodeBundle
from app.services.uslegalpro_efile_service import (
    USLegalProEFileService,
    unique_reference_id,
)


def test_unique_reference_id_is_unique():
    first = unique_reference_id()
    second = unique_reference_id()
    assert first.startswith("EFILE-")
    assert second.startswith("EFILE-")
    assert first != second


def _bundle() -> CodeBundle:
    return CodeBundle(
        state="tx",
        jurisdiction={"code": "harris:dc", "name": "Harris DC"},
        case_category={"code": "131370", "name": "Family"},
        case_type={"code": "209421", "name": "Divorce No Children"},
        filer_types=[{"code": "54325", "name": "Attorney"}],
        filing_type_options=[{"code": "EFile", "name": "EFile"}],
        filing_codes=[{"code": "209523", "name": "Petition"}],
        party_types=[
            {"code": "53024", "name": "Petitioner", "is_required": True},
            {"code": "271011", "name": "Respondent", "is_required": True},
        ],
        document_types_by_filing_code={
            "209523": [{"code": "53689", "name": "Petition-Divorce"}]
        },
    )


class _StubCodesService:
    def __init__(self, bundle: CodeBundle):
        self.bundle = bundle

    async def walk_case_type_chain(
        self,
        state,
        jurisdiction,
        category,
        case_type,
        *,
        filing_codes: Optional[Iterable[str]] = None,
        fetch_optional_services: bool = False,
    ) -> CodeBundle:
        return self.bundle


@pytest.mark.asyncio
async def test_build_submit_payload_existing_case_shape():
    service = USLegalProEFileService(codes_service=_StubCodesService(_bundle()))
    payload = await service.build_submit_payload(
        mode="filing_existing",
        selections={
            "state_code": "ca",
            "court_payment_account_id": "CC_1",
            "case_tracking_id": "trk-123",
            "document_type_code": "44889",
            "filing_code": "29736",
            "filing_type": "EFile",
            "filing_party_id": "Party_from_case",
            "efile_file_url": "https://example.com/notice.pdf",
        },
        collected_answers={},
        generated_documents=[
            {
                "file_name": "notice.pdf",
                "file": "https://example.com/notice.pdf",
            }
        ],
    )
    data = payload["data"]
    assert data["case_tracking_id"] == "trk-123"
    assert data["filing_type"] == "EFile"
    assert data["filing_party_id"] == "Party_from_case"
    assert data["filings"][0]["code"] == "29736"
    assert data["filings"][0]["doc_type"] == "44889"
    assert data["filings"][0]["file_name"] == "notice.pdf"


@pytest.mark.asyncio
async def test_build_submit_payload_new_case_uses_supplied_party_data():
    service = USLegalProEFileService(codes_service=_StubCodesService(_bundle()))
    payload = await service.build_submit_payload(
        mode="filing_new",
        selections={
            "state_code": "tx",
            "court_payment_account_id": "CC_1",
            "jurisdiction_code": "harris:dc",
            "case_category_code": "131370",
            "case_type_code": "209421",
            "filing_code": "209523",
            "doc_type": "53689",
            "filer_type": "54325",
            "filing_type": "EFile",
            "efile_file_url": "https://example.com/petition.pdf",
            "efile_file_size": 12345,
            "efile_case_parties": [
                {
                    "id": "Party_1",
                    "type": "53024",
                    "first_name": "JANE",
                    "last_name": "DOE",
                    "is_business": False,
                    "additional_attorneys": [],
                },
                {
                    "id": "Party_2",
                    "type": "271011",
                    "first_name": "JOHN",
                    "last_name": "DOE",
                    "is_business": False,
                    "additional_attorneys": [],
                },
            ],
            "filing_party_id": "Party_1",
        },
        collected_answers={},
        generated_documents=[
            {
                "file_name": "petition.pdf",
                "file": "https://example.com/petition.pdf",
                "size": 12345,
            }
        ],
    )
    data = payload["data"]
    assert data["jurisdiction"] == "harris:dc"
    assert data["case_parties"][0]["type"] == "53024"
    assert data["case_parties"][1]["type"] == "271011"
    assert data["filings"][0]["code"] == "209523"
    assert data["filings"][0]["doc_type"] == "53689"


@pytest.mark.asyncio
async def test_submit_normalizes_response(monkeypatch):
    class _Client:
        async def submit_efile(self, state, payload):
            assert state == "tx"
            assert payload["data"]["payment_account_id"] == "CC_1"
            return {
                "item": {"id": "325900", "status": "processing"},
                "message": "submitted",
            }

    service = USLegalProEFileService(codes_service=_StubCodesService(_bundle()))

    async def _fake_client(_user_id: str):
        return _Client()

    monkeypatch.setattr(service, "_client_for_user", _fake_client)
    result = await service.submit(
        user_id="u1",
        mode="filing_existing",
        selections={
            "state_code": "tx",
            "court_payment_account_id": "CC_1",
            "case_tracking_id": "trk-1",
            "filing_code": "29736",
            "doc_type": "44889",
            "filing_type": "EFile",
            "filing_party_id": "Party_from_case",
            "efile_file_url": "https://example.com/notice.pdf",
        },
        collected_answers={},
        generated_documents=[
            {
                "file_name": "notice.pdf",
                "file": "https://example.com/notice.pdf",
            }
        ],
    )
    assert result.envelope_id == "325900"
    assert result.status == "processing"
    assert result.message == "submitted"
    assert result.reference_id
