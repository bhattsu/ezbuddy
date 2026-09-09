"""Tests for the live e-file payload assembler and helpers.

These focus on the party-numbering / filing_party_id / name-mining behavior
that the user-facing validator relies on.
"""

from __future__ import annotations

import pytest

from app.services.efile_mapping_service import (
    _fill_missing_required_parties,
    _new_case_parties,
    _renumber_party_ids,
    assemble_new_case_efile_data_live,
)
from app.services.uslegalpro_codes_service import CodeBundle


def _bundle(**overrides) -> CodeBundle:
    base = dict(
        state="tx",
        jurisdiction={"code": "harrison:dc", "name": "Harrison DC"},
        case_category={"code": "131370", "name": "Family"},
        case_type={"code": "16102", "name": "Divorce No Children"},
        filer_types=[{"code": "54325", "name": "Attorney"}],
        filing_type_options=[{"code": "EFile", "name": "EFile"}],
        filing_codes=[{"code": "136697", "name": "Petition"}],
        party_types=[
            {"code": "53024", "name": "Petitioner", "is_required": True},
            {"code": "271011", "name": "Respondent", "is_required": True},
        ],
        document_types_by_filing_code={
            "136697": [{"code": "266947", "name": "Petition-Divorce"}],
        },
    )
    base.update(overrides)
    return CodeBundle(**base)


def test_primary_party_gets_party_1_id_and_name_from_answers() -> None:
    selections = {
        "party_type_code": "53024",
        "state_code": "TX",
    }
    answers = {
        "PETITIONER_FIRST_NAME": "Jane",
        "PETITIONER_LAST_NAME": "Doe",
    }
    parties = _new_case_parties(selections, answers, {})
    assert len(parties) == 1
    assert parties[0]["id"] == "Party_1"
    assert parties[0]["type"] == "53024"
    assert parties[0]["first_name"] == "Jane"
    assert parties[0]["last_name"] == "Doe"
    assert parties[0]["is_business"] is False


def test_primary_party_splits_full_name_field() -> None:
    answers = {"PETITIONER_FULL_NAME": "Jane Alice Doe"}
    parties = _new_case_parties(
        {"party_type_code": "53024"}, answers, {}
    )
    assert parties[0]["first_name"] == "Jane"
    assert parties[0]["last_name"] == "Alice Doe"


def test_fill_missing_required_parties_uses_role_names_from_answers() -> None:
    """Respondent stub should be populated from ``RESPONDENT_*`` answers and
    assigned ``Party_2``, not left type-less or nameless."""
    bundle = _bundle()
    primary = _new_case_parties(
        {"party_type_code": "53024"},
        {"PETITIONER_FIRST_NAME": "Jane", "PETITIONER_LAST_NAME": "Doe"},
        {},
    )
    parties = _fill_missing_required_parties(
        primary,
        bundle,
        collected_answers={
            "RESPONDENT_FIRST_NAME": "John",
            "RESPONDENT_LAST_NAME": "Doe",
        },
    )
    assert len(parties) == 2
    respondent = parties[1]
    assert respondent["id"] == "Party_2"
    assert respondent["type"] == "271011"
    assert respondent["first_name"] == "John"
    assert respondent["last_name"] == "Doe"
    assert respondent["is_business"] is False


def test_renumber_party_ids_is_idempotent() -> None:
    parties = [
        {"id": "Party_missing_x", "type": "53024"},
        {"id": "Party_missing_y", "type": "271011"},
        {"id": "", "type": "999"},
    ]
    _renumber_party_ids(parties)
    assert [p["id"] for p in parties] == ["Party_1", "Party_2", "Party_3"]
    # Second call is a no-op.
    _renumber_party_ids(parties)
    assert [p["id"] for p in parties] == ["Party_1", "Party_2", "Party_3"]


def test_assemble_new_case_efile_data_live_produces_valid_parties() -> None:
    """End-to-end: the assembler emits ``Party_1``/``Party_2`` ids, populates
    petitioner + respondent names from workflow answers, and sets
    ``filing_party_id = Party_1`` so the validator no longer complains."""
    bundle = _bundle()
    selections = {
        "state_code": "TX",
        "jurisdiction_code": "harrison:dc",
        "case_category_code": "131370",
        "case_type_code": "16102",
        "party_type_code": "53024",
        "filer_type": "54325",
        "filing_type": "EFile",
        "filing_code": "136697",
        "doc_type": "266947",
        "court_payment_account_id": "CC_pay",
    }
    answers = {
        "PETITIONER_FIRST_NAME": "Jane",
        "PETITIONER_LAST_NAME": "Doe",
        "RESPONDENT_FIRST_NAME": "John",
        "RESPONDENT_LAST_NAME": "Doe",
    }
    generated = [
        {
            "file": "https://example.com/doc.pdf",
            "file_name": "doc.pdf",
            "template_name": "Petition",
            "size": 42000,
        }
    ]

    data = assemble_new_case_efile_data_live(
        selections=selections,
        bundle=bundle,
        collected_answers=answers,
        generated_documents=generated,
        reference_id="DRAFT-2026-99999",
    )

    ids = [p["id"] for p in data["case_parties"]]
    assert ids == ["Party_1", "Party_2"]
    # ``is_business`` is a JSON boolean per the guide (never a string).
    for party in data["case_parties"]:
        assert party["is_business"] is False
    # Both parties have names from the answers.
    assert data["case_parties"][0]["first_name"] == "Jane"
    assert data["case_parties"][0]["last_name"] == "Doe"
    assert data["case_parties"][1]["first_name"] == "John"
    assert data["case_parties"][1]["last_name"] == "Doe"
    # filing_party_id references case_parties[0].id.
    assert data["filing_party_id"] == "Party_1"
    # Filing preserves its supplied size.
    assert data["filings"][0]["size"] == 42000


def test_assemble_ignores_stale_filing_party_id_not_in_parties() -> None:
    """A stale ``filing_party_id`` from the session should be dropped when it
    doesn't match any generated party id, and the default should pin to
    ``Party_1``."""
    bundle = _bundle()
    selections = {
        "state_code": "TX",
        "party_type_code": "53024",
        "filing_party_id": "Party_from_old_session",
    }
    data = assemble_new_case_efile_data_live(
        selections=selections,
        bundle=bundle,
        collected_answers={"PETITIONER_FIRST_NAME": "Jane"},
        generated_documents=[],
        reference_id="ref",
    )
    party_ids = {p["id"] for p in data["case_parties"]}
    assert data["filing_party_id"] in party_ids
    assert data["filing_party_id"] == "Party_1"


@pytest.mark.asyncio
async def test_resolve_filing_sizes_fills_missing_size(monkeypatch) -> None:
    """``resolve_filing_sizes`` should HEAD the file URL and write the byte
    count into ``filings[i].size`` when it's missing."""
    from app.services import uslegalpro_efile_service as efile_svc

    async def fake_fetch(url: str, timeout: float = 15.0):
        assert url == "https://example.com/doc.pdf"
        return 98765

    monkeypatch.setattr(efile_svc, "_fetch_url_size", fake_fetch)

    payload = {
        "data": {
            "filings": [
                {
                    "code": "136697",
                    "file": "https://example.com/doc.pdf",
                    "size": "",
                },
                {
                    "code": "136697",
                    "file": "https://example.com/doc2.pdf",
                    "size": 12345,  # already set -- must be left alone
                },
            ]
        }
    }
    result = await efile_svc.resolve_filing_sizes(payload)
    assert result["data"]["filings"][0]["size"] == 98765
    assert result["data"]["filings"][1]["size"] == 12345


@pytest.mark.asyncio
async def test_resolve_filing_sizes_coerces_string_digit_size() -> None:
    """A stringified numeric size should be normalized to ``int`` without a
    network call."""
    from app.services.uslegalpro_efile_service import resolve_filing_sizes

    payload = {
        "data": {
            "filings": [
                {"code": "1", "file": "https://ex.test/a.pdf", "size": "54321"}
            ]
        }
    }
    result = await resolve_filing_sizes(payload)
    assert result["data"]["filings"][0]["size"] == 54321
