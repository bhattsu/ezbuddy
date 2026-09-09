"""Tests for e-file mapping / assembly and the live-fetch submit pipeline."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

import pytest

from app.services.efile_mapping_service import (
    apply_known_efile_facts,
    assemble_new_case_efile_data,
    mapped_form_data_from_documents,
)
from app.services.efile_validator import EFilePayloadValidationError
from app.services.uslegalpro_codes_service import CodeBundle
from app.services.uslegalpro_efile_service import (
    EFileSubmitResult,
    USLegalProEFileService,
    format_efile_preview_message,
    format_efile_success_message,
)


class _StubCodesService:
    """Codes service stub returning a canned ``CodeBundle``."""

    def __init__(self, bundle: CodeBundle):
        self.bundle = bundle
        self.calls: List[Dict[str, Any]] = []

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
        self.calls.append(
            {
                "state": state,
                "jurisdiction": jurisdiction,
                "category": category,
                "case_type": case_type,
                "filing_codes": list(filing_codes or []),
                "fetch_optional_services": fetch_optional_services,
            }
        )
        return self.bundle


def _sample_bundle() -> CodeBundle:
    return CodeBundle(
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


def _base_selections(**extra):
    data = {
        "state_code": "tx",
        "jurisdiction_code": "harris:dc",
        "case_category_code": "131370",
        "case_type_code": "209421",
        "court_payment_account_id": "CC_01eb044f-9e41-4966-93e2-31a5f8d9c01b",
        "doc_type": "53689",
        "filing_code": "209523",
        "filer_type": "54325",
        "filing_type": "EFile",
        "efile_file_url": "https://generated.example/case_document.pdf",
        "efile_file_size": 123261,
        "party_type_code": "53024",
        "first_name": "Jane",
        "last_name": "Doe",
        "reference_id": "EFILE-TEST-1",
        "document_type_name": "Petition",
        # Second required party so the validator is satisfied.
        "efile_case_parties": [
            {
                "id": "Party_1",
                "type": "53024",
                "first_name": "JANE",
                "last_name": "DOE",
                "is_business": False,
                "additional_attorneys": [],
                "country": "US",
                "state": "TX",
            },
            {
                "id": "Party_2",
                "type": "271011",
                "first_name": "JOHN",
                "last_name": "DOE",
                "is_business": False,
                "additional_attorneys": [],
                "country": "US",
            },
        ],
        "filing_party_id": "Party_1",
    }
    data.update(extra)
    return data


@pytest.mark.asyncio
async def test_existing_case_payload_uses_session_cache_only():
    service = USLegalProEFileService(codes_service=_StubCodesService(_sample_bundle()))
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
    assert data["filing_type"] == "EFile"
    assert "case_parties" not in data
    assert data["filings"][0]["file"] == "https://generated.example/case_document.pdf"
    assert data["filings"][0]["file_name"] == "case_document.pdf"
    assert data["filings"][0]["code"] == "209523"
    assert data["filings"][0]["doc_type"] == "53689"


@pytest.mark.asyncio
async def test_new_case_payload_walks_live_codes_and_validates():
    stub = _StubCodesService(_sample_bundle())
    service = USLegalProEFileService(codes_service=stub)
    payload = await service.build_submit_payload(
        mode="filing_new",
        selections=_base_selections(),
        collected_answers={"_PLAINTIFF_1_FULL_NAME": "Jane Doe"},
        generated_documents=[
            {
                "file_name": "case_document.pdf",
                "download_url": "https://generated.example/case_document.pdf",
                "size": 123261,
                "request_payload": {
                    "form_data": {"_PLAINTIFF_1_FULL_NAME": "Jane Doe"}
                },
            }
        ],
    )
    assert stub.calls, "expected walk_case_type_chain to be invoked"
    walk = stub.calls[0]
    assert walk["state"] == "tx"
    assert walk["jurisdiction"] == "harris:dc"
    assert walk["case_type"] == "209421"
    assert "209523" in walk["filing_codes"]

    data = payload["data"]
    assert data["jurisdiction"] == "harris:dc"
    assert data["case_type"] == "209421"
    assert data["case_category"] == "131370"
    assert data["filer_type"] == "54325"
    assert data["filing_type"] == "EFile"
    assert data["filings"][0]["code"] == "209523"
    assert data["filings"][0]["doc_type"] == "53689"
    assert data["filings"][0]["file"] == "https://generated.example/case_document.pdf"
    assert data["filings"][0]["size"] == 123261
    # both required party types are present
    assert {p["type"] for p in data["case_parties"]} == {"53024", "271011"}


@pytest.mark.asyncio
async def test_build_submit_payload_produces_n_filings():
    stub = _StubCodesService(_sample_bundle())
    service = USLegalProEFileService(codes_service=stub)
    selections = _base_selections(
        efile_filings=[
            {
                "code": "209523",
                "doc_type": "53689",
                "file": "https://ex.test/complaint.pdf",
                "file_name": "complaint.pdf",
                "description": "Petition",
                "size": 123261,
                "id": "Filing_A",
                "associated_parties": [],
            },
            {
                "code": "209524",
                "doc_type": "53690",
                "file": "https://ex.test/exhibit.pdf",
                "file_name": "exhibit.pdf",
                "description": "Exhibit A",
                "size": 42000,
                "id": "Filing_B",
                "associated_parties": [],
            },
        ],
    )
    payload = await service.build_submit_payload(
        mode="filing_new",
        selections=selections,
        collected_answers={},
        generated_documents=[],
    )
    filings = payload["data"]["filings"]
    assert [f["code"] for f in filings] == ["209523", "209524"]
    assert [f["doc_type"] for f in filings] == ["53689", "53690"]
    assert [f["id"] for f in filings] == ["Filing_A", "Filing_B"]
    # The aggregated filing-code set was passed to the walker
    assert set(stub.calls[0]["filing_codes"]) == {"209523", "209524"}


@pytest.mark.asyncio
async def test_build_submit_payload_uses_confirmed_override_and_reuses_bundle():
    stub = _StubCodesService(_sample_bundle())
    service = USLegalProEFileService(codes_service=stub)
    selections = _base_selections()
    # First call: build a live payload and cache the bundle onto selections.
    first_payload = await service.build_submit_payload(
        mode="filing_new",
        selections=selections,
        collected_answers={},
        generated_documents=[
            {
                "file_name": "complaint.pdf",
                "file": "https://ex.test/complaint.pdf",
                "size": 123261,
            }
        ],
    )
    assert selections.get("efile_code_bundle"), "bundle should be cached"
    first_walk_count = len(stub.calls)

    # Second call with override: bundle is reused instead of re-walking.
    selections["efile_payload_override"] = first_payload
    reused = await service.build_submit_payload(
        mode="filing_new",
        selections=selections,
        collected_answers={},
        generated_documents=[],
    )
    assert reused == first_payload
    assert len(stub.calls) == first_walk_count, (
        "confirm step should reuse the cached bundle, not call the walker again"
    )


@pytest.mark.asyncio
async def test_build_submit_payload_blocks_when_override_is_invalid():
    """The override path skips the auto-correcting assembler, so a bad code
    supplied via ``efile_payload_override`` must be rejected by the validator."""
    stub = _StubCodesService(_sample_bundle())
    service = USLegalProEFileService(codes_service=stub)
    bad_override = {
        "data": {
            "filer_type": "9999-not-in-bundle",
            "reference_id": "R-1",
            "jurisdiction": "harris:dc",
            "payment_account_id": "CC_1",
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
            "filing_type": "EFile",
            "filing_state": "tx",
            "case_type": "209421",
            "case_category": "131370",
            "filing_party_id": "Party_1",
            "provider_fee": "",
            "provider_tax": "",
        }
    }
    selections = _base_selections(efile_payload_override=bad_override)
    with pytest.raises(EFilePayloadValidationError) as excinfo:
        await service.build_submit_payload(
            mode="filing_new",
            selections=selections,
            collected_answers={},
            generated_documents=[],
        )
    assert any("filer_type" in err for err in excinfo.value.errors)


@pytest.mark.asyncio
async def test_build_submit_payload_falls_back_and_warns_on_bad_hint(caplog):
    """A stale hint that is not in the live bundle is corrected by the
    assembler with a warning, so the payload still validates."""
    stub = _StubCodesService(_sample_bundle())
    service = USLegalProEFileService(codes_service=stub)
    payload = await service.build_submit_payload(
        mode="filing_new",
        selections=_base_selections(filer_type="9999-not-in-bundle"),
        collected_answers={},
        generated_documents=[
            {
                "file_name": "complaint.pdf",
                "file": "https://ex.test/complaint.pdf",
                "size": 123261,
            }
        ],
    )
    # Assembler used the first live filer_type.
    assert payload["data"]["filer_type"] == "54325"
