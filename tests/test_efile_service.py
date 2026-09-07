from __future__ import annotations

import pytest

from app.services.uslegalpro_efile_service import USLegalProEFileService, unique_reference_id


def test_unique_reference_id_is_unique():
    first = unique_reference_id()
    second = unique_reference_id()
    assert first.startswith("EFILE-")
    assert second.startswith("EFILE-")
    assert first != second


@pytest.mark.asyncio
async def test_build_submit_payload_existing_resolves_filing_code():
    class _Codes:
        async def fetch_by_url(self, url):
            assert "filing" in url
            return [{"code": "29736", "name": "Notice of Appeal"}]

    service = USLegalProEFileService(codes_service=_Codes())
    payload = await service.build_submit_payload(
        mode="filing_existing",
        selections={
            "state_code": "ca",
            "court_payment_account_id": "CC_1",
            "case_tracking_id": "trk-123",
            "document_type_code": "44889",
            "filing_codes_url": "https://example/filing_codes",
            "efile_file_url": "https://example.com/notice.pdf",
        },
        collected_answers={},
        generated_documents=[],
    )
    data = payload["data"]
    assert data["case_tracking_id"] == "trk-123"
    assert data["filings"][0]["code"] == "29736"
    assert data["filings"][0]["doc_type"] == "44889"


@pytest.mark.asyncio
async def test_build_submit_payload_new_case_uses_supplied_party_data():
    service = USLegalProEFileService()
    payload = await service.build_submit_payload(
        mode="filing_new",
        selections={
            "state_code": "tx",
            "court_payment_account_id": "CC_1",
            "jurisdiction_code": "harris:dc",
            "case_category_code": "131370",
            "case_type_code": "209421",
            "filing_code": "209523",
            "document_type_code": "53689",
            "efile_file_url": "https://example.com/petition.pdf",
            "efile_case_parties": [{"id": "Party_1", "type": "53024", "first_name": "JANE"}],
            "filing_party_id": "Party_1",
        },
        collected_answers={},
        generated_documents=[],
    )
    data = payload["data"]
    assert data["jurisdiction"] == "harris:dc"
    assert data["case_parties"][0]["type"] == "53024"
    assert data["filings"][0]["code"] == "209523"


@pytest.mark.asyncio
async def test_submit_normalizes_response(monkeypatch):
    class _Client:
        async def submit_efile(self, state, payload):
            assert state == "tx"
            assert payload["data"]["payment_account_id"] == "CC_1"
            return {"item": {"id": "325900", "status": "processing"}, "message": "submitted"}

    service = USLegalProEFileService()

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
            "document_type_code": "44889",
            "efile_file_url": "https://example.com/notice.pdf",
        },
        collected_answers={},
        generated_documents=[],
    )
    assert result.envelope_id == "325900"
    assert result.status == "processing"
    assert result.message == "submitted"
    assert result.reference_id
