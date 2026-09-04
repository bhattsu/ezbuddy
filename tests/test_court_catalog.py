"""Flatten and search the cached jurisdiction_api_data catalog."""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.agents.conversation.orchestration.helpers import (
    apply_catalog_shortcuts,
    capture_new_case_topic,
    load_db_options,
    advance_mode_from_intent,
    advance_phase_after_selections,
)
from app.agents.conversation.orchestration.state import FilingSession
from app.agents.utils.db_options_format import build_phase_selection_message
from app.api.schemas.filing_events import FilingMode, FilingPhase
from app.services.court_catalog import (
    CourtCatalogService,
    flatten_jurisdiction_data,
    infer_case_topic,
    jurisdiction_options,
    reset_court_catalog,
    search_catalog_rows,
)

SAMPLE_JURISDICTION_DATA = {
    "state_code": "TX",
    "courts": [
        {
            "code": "harris:dc",
            "name": "Harris County - District Clerk",
            "categories": [
                {
                    "code": "49543",
                    "name": "Civil",
                    "case_types": [
                        {
                            "code": "209433",
                            "name": "Intellectual Property",
                            "party_type_codes_url": "https://example/parties/ip",
                        }
                    ],
                },
                {
                    "code": "fam1",
                    "name": "Family",
                    "case_types": [
                        {
                            "code": "div1",
                            "name": "Divorce No Children",
                            "party_type_codes_url": "https://example/parties/div",
                            "case_subtype_codes_url": "https://example/subtypes/div",
                        },
                        {
                            "code": "prot1",
                            "name": "Protective Orders: No Divorce",
                        },
                    ],
                },
            ],
        },
        {
            "code": "travis:dc",
            "name": "Travis County - District Clerk",
            "categories": [
                {
                    "code": "fam1",
                    "name": "Family",
                    "case_types": [
                        {
                            "code": "div3",
                            "name": "Divorce With Children",
                            "party_type_codes_url": "https://example/parties/div3",
                        }
                    ],
                }
            ],
        },
    ],
}


def test_flatten_keeps_codes_and_leaves_parties_empty():
    rows = flatten_jurisdiction_data(SAMPLE_JURISDICTION_DATA)
    ip = next(row for row in rows if row["case_type_code"] == "209433")
    assert ip == {
        "jurisdiction_code": "harris:dc",
        "jurisdiction_name": "Harris County - District Clerk",
        "case_category_code": "49543",
        "case_category_name": "Civil",
        "case_type_code": "209433",
        "case_type_name": "Intellectual Property",
        "party_code": "",
        "party_name": "",
        "party_type_codes_url": "https://example/parties/ip",
        "case_subtype_codes_url": "",
        "filing_codes_url": "",
    }


def test_search_divorce_excludes_no_divorce_protective_orders():
    rows = flatten_jurisdiction_data(SAMPLE_JURISDICTION_DATA)
    matched = search_catalog_rows(rows, "divorce")
    names = {row["case_type_name"] for row in matched}
    codes = {row["jurisdiction_code"] for row in matched}
    assert names == {"Divorce No Children", "Divorce With Children"}
    assert codes == {"harris:dc", "travis:dc"}
    assert "Protective Orders: No Divorce" not in names


def test_infer_case_topic_from_natural_language():
    assert infer_case_topic("I want to file a divorce like that") == "divorce"
    assert infer_case_topic("Help me file an eviction") == "eviction"
    assert infer_case_topic("I need to file a case for divorce") == "divorce"


def test_topic_dropdown_message():
    msg = build_phase_selection_message(
        "selecting_jurisdiction",
        {"case_topic": "divorce"},
        [
            {"name": "Harris County - District Clerk", "code": "harris:dc"},
            {"name": "Travis County - District Clerk", "code": "travis:dc"},
        ],
    )
    assert msg is not None
    assert "divorce" in msg.lower()
    assert "dropdown" in msg.lower()


@pytest.mark.asyncio
async def test_new_case_jurisdiction_options_filter_by_topic(monkeypatch):
    catalog = CourtCatalogService(fallback_path=Path("missing-catalog.json"))
    catalog._cache["TX"] = flatten_jurisdiction_data(SAMPLE_JURISDICTION_DATA)
    reset_court_catalog(catalog)
    monkeypatch.setattr(
        "app.agents.conversation.orchestration.helpers.get_court_catalog",
        lambda: catalog,
    )
    repo = AsyncMock()
    options = await load_db_options(
        repo,
        FilingPhase.SELECTING_JURISDICTION,
        {"state_code": "TX", "case_topic": "divorce", "matched_court_codes": ["harris:dc", "travis:dc"]},
        mode=FilingMode.FILING_NEW,
        bedrock=None,
    )
    assert {row["code"] for row in options} == {"harris:dc", "travis:dc"}
    repo.get_jurisdictions_for_county.assert_not_called()
    reset_court_catalog()


@pytest.mark.asyncio
async def test_catalog_shortcuts_skip_unique_category_and_type(monkeypatch):
    catalog = CourtCatalogService(fallback_path=Path("missing-catalog.json"))
    catalog._cache["TX"] = flatten_jurisdiction_data(SAMPLE_JURISDICTION_DATA)
    monkeypatch.setattr(
        "app.agents.conversation.orchestration.helpers.get_court_catalog",
        lambda: catalog,
    )
    session = FilingSession(
        conversation_id="c1",
        user_id="u1",
        mode=FilingMode.FILING_NEW,
        phase=FilingPhase.SELECTING_CASE_CATEGORY,
        selections={
            "state_code": "TX",
            "case_topic": "divorce",
            "matched_court_codes": ["harris:dc"],
            "jurisdiction_code": "harris:dc",
            "jurisdiction_name": "Harris County - District Clerk",
        },
    )
    await apply_catalog_shortcuts(AsyncMock(), session, bedrock=None)
    assert session.selections["case_category_code"] == "fam1"
    assert session.selections["case_type_code"] == "div1"
    assert session.selections["party_type_codes_url"] == "https://example/parties/div"
    assert session.phase == FilingPhase.SELECTING_CASE_PARTIES
    reset_court_catalog()


@pytest.mark.asyncio
async def test_capture_topic_skipped_after_jurisdiction_selected():
    session = FilingSession(
        conversation_id="c1",
        user_id="u1",
        mode=FilingMode.FILING_NEW,
        phase=FilingPhase.SELECTING_CASE_CATEGORY,
        selections={
            "case_topic": "divorce",
            "jurisdiction_code": "harris:dc",
        },
    )
    await capture_new_case_topic(
        session, "Divorce No Children", bedrock=None
    )
    assert session.selections["case_topic"] == "divorce"


@pytest.mark.asyncio
async def test_capture_topic_only_before_jurisdiction_selected():
    session = FilingSession(
        conversation_id="c1",
        user_id="u1",
        mode=FilingMode.FILING_NEW,
        phase=FilingPhase.SELECTING_JURISDICTION,
    )
    await capture_new_case_topic(
        session, "I want to file a divorce", bedrock=None
    )
    assert session.selections["case_topic"] == "divorce"


@pytest.mark.asyncio
async def test_category_options_skip_llm_after_court_selected(monkeypatch):
    catalog = CourtCatalogService(fallback_path=Path("missing-catalog.json"))
    catalog._cache["TX"] = flatten_jurisdiction_data(SAMPLE_JURISDICTION_DATA)
    reset_court_catalog(catalog)
    monkeypatch.setattr(
        "app.agents.conversation.orchestration.helpers.get_court_catalog",
        lambda: catalog,
    )

    llm_called = False

    class _Bedrock:
        async def invoke_structured_prompt(self, prompt, schema):
            nonlocal llm_called
            llm_called = True
            return schema(matched_court_codes=[], reason="")

        async def invoke_prompt_with_timeout(self, body):
            return {}

    repo = AsyncMock()
    options = await load_db_options(
        repo,
        FilingPhase.SELECTING_CASE_CATEGORY,
        {
            "state_code": "TX",
            "case_topic": "divorce",
            "jurisdiction_code": "harris:dc",
        },
        mode=FilingMode.FILING_NEW,
        bedrock=_Bedrock(),
    )
    assert {row["code"] for row in options} == {"fam1"}
    assert llm_called is False
    reset_court_catalog()
    session = FilingSession(
        conversation_id="c1",
        user_id="u1",
        mode=FilingMode.FILING_NEW,
        phase=FilingPhase.SELECTING_JURISDICTION,
    )
    await capture_new_case_topic(
        session, "I want to file a divorce", bedrock=None
    )
    assert session.selections["case_topic"] == "divorce"


def test_jurisdiction_options_are_unique():
    rows = flatten_jurisdiction_data(SAMPLE_JURISDICTION_DATA)
    options = jurisdiction_options(rows)
    assert [row["code"] for row in options] == ["harris:dc", "travis:dc"]


@pytest.mark.asyncio
async def test_shared_state_then_intent_then_new_case_courts():
    session = FilingSession(
        conversation_id="c1",
        user_id="u1",
        mode=FilingMode.UNSET,
        phase=FilingPhase.SELECTING_STATE,
    )
    session.selections["state_code"] = "TX"
    session.selections["state_name"] = "Texas"
    advance_phase_after_selections(session)
    assert session.phase == FilingPhase.INTENT_PENDING
    assert session.mode == FilingMode.UNSET

    advance_mode_from_intent(session, "filing_new")
    await capture_new_case_topic(
        session, "I want to file a divorce", bedrock=None
    )
    assert session.mode == FilingMode.FILING_NEW
    assert session.phase == FilingPhase.SELECTING_JURISDICTION
    assert session.selections["case_topic"] == "divorce"


def test_post_state_existing_skips_state_again():
    session = FilingSession(
        conversation_id="c1",
        user_id="u1",
        mode=FilingMode.UNSET,
        phase=FilingPhase.INTENT_PENDING,
        selections={"state_code": "TX", "state_name": "Texas"},
    )
    advance_mode_from_intent(session, "filing_existing")
    assert session.mode == FilingMode.FILING_EXISTING
    assert session.phase == FilingPhase.EXISTING_SELECTING_JURISDICTION


def test_intent_ignored_until_state_selected():
    session = FilingSession(
        conversation_id="c1",
        user_id="u1",
        mode=FilingMode.UNSET,
        phase=FilingPhase.SELECTING_STATE,
    )
    advance_mode_from_intent(session, "filing_new")
    assert session.mode == FilingMode.UNSET
    assert session.phase == FilingPhase.SELECTING_STATE
