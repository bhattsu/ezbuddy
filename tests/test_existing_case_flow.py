"""Tests for authenticated existing-case API navigation."""

from __future__ import annotations

import pytest

from app.agents.conversation.orchestration.helpers import (
    advance_phase_after_selections,
    cache_existing_case_details,
    handle_lookup,
)
from app.agents.conversation.orchestration.state import FilingSession
from app.api.schemas.filing_events import FilingMode, FilingPhase
from app.services.uslegalpro_existing_case_service import (
    USLegalProExistingCaseService,
)


def test_existing_case_phase_transitions():
    session = FilingSession(
        conversation_id="c1",
        user_id="u1",
        mode=FilingMode.FILING_EXISTING,
        phase=FilingPhase.EXISTING_SELECTING_STATE,
    )
    session.selections["state_code"] = "TX"

    advance_phase_after_selections(session)
    assert session.phase == FilingPhase.EXISTING_SELECTING_JURISDICTION

    session.selections["jurisdiction_code"] = "refugio:dc"
    advance_phase_after_selections(session)
    assert session.phase == FilingPhase.EXISTING_ENTER_CASE_NUMBER


def test_existing_jurisdiction_stores_code_from_selected_name():
    """Existing-case court pick stores API code, then asks for the case number."""
    session = FilingSession(
        conversation_id="c1",
        user_id="u1",
        mode=FilingMode.FILING_EXISTING,
        phase=FilingPhase.EXISTING_SELECTING_JURISDICTION,
        selections={"state_code": "TX", "state_name": "Texas"},
    )
    session.selections["jurisdiction_name"] = "Refugio County - District Clerk"
    session.selections["jurisdiction_code"] = "refugio:dc"
    advance_phase_after_selections(session)
    assert session.phase == FilingPhase.EXISTING_ENTER_CASE_NUMBER
    assert session.selections["jurisdiction_code"] == "refugio:dc"
    assert session.selections["jurisdiction_name"] == "Refugio County - District Clerk"


def test_new_case_moves_through_filing_code_phase_when_available():
    session = FilingSession(
        conversation_id="c1",
        user_id="u1",
        mode=FilingMode.FILING_NEW,
        phase=FilingPhase.SELECTING_CASE_PARTIES,
        selections={
            "party_type_code": "53024",
            "case_type_name": "Divorce",
            "filing_codes_url": "https://example.com/filing_codes",
        },
    )
    advance_phase_after_selections(session)
    assert session.phase == FilingPhase.SELECTING_FILING_CODE

    session.selections["filing_code"] = "209523"
    advance_phase_after_selections(session)
    assert session.phase == FilingPhase.SELECTING_DOCUMENT_TYPE


def test_filing_code_selection_writes_bare_key_and_advances():
    """Regression: local match on ``selecting_filing_code`` used to store only
    ``filing_code_code``, so ``advance_phase_after_selections`` never saw a
    truthy ``filing_code`` and the flow re-prompted the same dropdown forever.
    The fix writes ``filing_code`` alongside ``filing_code_code`` and the
    phase advances to ``SELECTING_DOCUMENT_TYPE``."""
    from app.agents.utils.db_options_format import (
        filter_selections_update,
        selection_update_for_option,
    )

    options = [{"code": "136697", "name": "Petition"}]
    update = selection_update_for_option("selecting_filing_code", options[0])
    resolved = filter_selections_update(
        "selecting_filing_code", update, options
    )
    assert resolved["filing_code"] == "136697"
    assert resolved["filing_code_code"] == "136697"
    assert resolved["filing_code_name"] == "Petition"

    session = FilingSession(
        conversation_id="c1",
        user_id="u1",
        mode=FilingMode.FILING_NEW,
        phase=FilingPhase.SELECTING_FILING_CODE,
        selections={
            "party_type_code": "53024",
            "filing_codes_url": "https://example.com/filing_codes",
            **resolved,
        },
    )
    advance_phase_after_selections(session)
    assert session.phase == FilingPhase.SELECTING_DOCUMENT_TYPE
    assert session.selections["filing_code"] == "136697"


def test_filing_code_legacy_selection_still_advances():
    """A session persisted from a previous release may only have
    ``filing_code_code`` set. The flow must still advance and normalize the
    scalar to the bare ``filing_code`` key the payload assembler reads."""
    session = FilingSession(
        conversation_id="c1",
        user_id="u1",
        mode=FilingMode.FILING_NEW,
        phase=FilingPhase.SELECTING_FILING_CODE,
        selections={
            "party_type_code": "53024",
            "filing_codes_url": "https://example.com/filing_codes",
            "filing_code_code": "136697",
            "filing_code_name": "Petition",
        },
    )
    advance_phase_after_selections(session)
    assert session.phase == FilingPhase.SELECTING_DOCUMENT_TYPE
    assert session.selections["filing_code"] == "136697"


def test_new_case_skips_filing_code_when_no_url_available():
    """If a case type doesn't expose a filing_codes link, we fall back to
    the document_type phase directly so the flow doesn't stall."""
    session = FilingSession(
        conversation_id="c1",
        user_id="u1",
        mode=FilingMode.FILING_NEW,
        phase=FilingPhase.SELECTING_CASE_PARTIES,
        selections={
            "party_type_code": "53024",
            "case_type_name": "Divorce",
        },
    )
    advance_phase_after_selections(session)
    assert session.phase == FilingPhase.SELECTING_DOCUMENT_TYPE


def test_new_case_moves_through_filer_and_filing_type_phases_when_available():
    """After document_type is ready, ask for filer_type then filing_type when
    the case-type item exposed those API links."""
    session = FilingSession(
        conversation_id="c1",
        user_id="u1",
        mode=FilingMode.FILING_NEW,
        phase=FilingPhase.SELECTING_DOCUMENT_TYPE,
        selections={
            "template_questions_ready": True,
            "filer_type_codes_url": "https://example.com/filer_type_codes",
            "filing_type_url": "https://example.com/filing_type",
        },
    )

    advance_phase_after_selections(session)
    assert session.phase == FilingPhase.SELECTING_FILER_TYPE

    session.selections["filer_type"] = "18569"
    advance_phase_after_selections(session)
    assert session.phase == FilingPhase.SELECTING_FILING_TYPE

    session.selections["filing_type"] = "EFile"
    advance_phase_after_selections(session)
    assert session.phase == FilingPhase.OFFERING_DOCUMENTS


def test_new_case_skips_filer_type_when_url_missing():
    """No filer_type URL -> jump straight to filing_type when its URL is set."""
    session = FilingSession(
        conversation_id="c1",
        user_id="u1",
        mode=FilingMode.FILING_NEW,
        phase=FilingPhase.SELECTING_DOCUMENT_TYPE,
        selections={
            "template_questions_ready": True,
            "filing_type_url": "https://example.com/filing_type",
        },
    )

    advance_phase_after_selections(session)
    assert session.phase == FilingPhase.SELECTING_FILING_TYPE


def test_new_case_goes_straight_to_offering_when_neither_url_set():
    """No filer_type/filing_type link -> proceed to OFFERING_DOCUMENTS as before."""
    session = FilingSession(
        conversation_id="c1",
        user_id="u1",
        mode=FilingMode.FILING_NEW,
        phase=FilingPhase.SELECTING_DOCUMENT_TYPE,
        selections={"template_questions_ready": True},
    )

    advance_phase_after_selections(session)
    assert session.phase == FilingPhase.OFFERING_DOCUMENTS


def test_auto_pick_single_option_advances_filing_type():
    """A one-option filing_type (e.g. just ``EFile``) is auto-picked and the
    phase advances to OFFERING_DOCUMENTS without asking the user."""
    from app.agents.conversation.orchestration.helpers import auto_pick_single_option

    session = FilingSession(
        conversation_id="c1",
        user_id="u1",
        mode=FilingMode.FILING_NEW,
        phase=FilingPhase.SELECTING_FILING_TYPE,
        selections={
            "template_questions_ready": True,
            "filing_type_url": "https://example.com/filing_type",
        },
    )
    advanced = auto_pick_single_option(
        session,
        FilingPhase.SELECTING_FILING_TYPE,
        [{"code": "EFile", "name": "EFile"}],
    )
    assert advanced is True
    assert session.selections["filing_type"] == "EFile"
    assert session.phase == FilingPhase.OFFERING_DOCUMENTS


def test_auto_pick_single_option_returns_false_for_multi_option():
    """Multi-option list -> auto-pick is a no-op and the phase is unchanged."""
    from app.agents.conversation.orchestration.helpers import auto_pick_single_option

    session = FilingSession(
        conversation_id="c1",
        user_id="u1",
        mode=FilingMode.FILING_NEW,
        phase=FilingPhase.SELECTING_FILER_TYPE,
        selections={
            "filer_type_codes_url": "https://example.com/filer_type_codes",
        },
    )
    advanced = auto_pick_single_option(
        session,
        FilingPhase.SELECTING_FILER_TYPE,
        [
            {"code": "1", "name": "Attorney"},
            {"code": "2", "name": "Pro Se Filer"},
        ],
    )
    assert advanced is False
    assert "filer_type" not in session.selections
    assert session.phase == FilingPhase.SELECTING_FILER_TYPE


def test_case_type_selection_propagates_filer_and_filing_type_urls():
    """Selecting a case type should carry its filer_type_codes_url and
    filing_type_url into ``selections`` so the new phases can call them."""
    from app.agents.utils.db_options_format import (
        filter_selections_update,
        selection_update_for_option,
    )

    options = [
        {
            "code": "42311",
            "name": "Divorce",
            "party_type_codes_url": "https://example.com/party_type",
            "filing_codes_url": "https://example.com/filing_codes",
            "filer_type_codes_url": "https://example.com/filer_type_codes",
            "filing_type_url": "https://example.com/filing_type",
        }
    ]

    update = selection_update_for_option("selecting_case_type", options[0])
    resolved = filter_selections_update("selecting_case_type", update, options)

    assert resolved["case_type_code"] == "42311"
    assert resolved["filing_codes_url"] == "https://example.com/filing_codes"
    assert resolved["filer_type_codes_url"] == "https://example.com/filer_type_codes"
    assert resolved["filing_type_url"] == "https://example.com/filing_type"


def test_filer_and_filing_type_selection_updates_carry_selected_code():
    """A dropdown pick on the new phases stores the raw scalar under the
    same key the payload assembler expects (``filer_type`` / ``filing_type``)."""
    from app.agents.utils.db_options_format import (
        filter_selections_update,
        selection_update_for_option,
    )

    filer_options = [
        {"code": "18569", "name": "Attorney"},
        {"code": "9835", "name": "Pro Se Filer"},
    ]
    update = selection_update_for_option("selecting_filer_type", filer_options[1])
    resolved = filter_selections_update(
        "selecting_filer_type", update, filer_options
    )
    assert resolved["filer_type"] == "9835"
    assert resolved["filer_type_code"] == "9835"
    assert resolved["filer_type_name"] == "Pro Se Filer"

    ft_options = [
        {"code": "EFile", "name": "EFile"},
        {"code": "EFileAndServe", "name": "EFileAndServe"},
    ]
    update = selection_update_for_option("selecting_filing_type", ft_options[0])
    resolved = filter_selections_update(
        "selecting_filing_type", update, ft_options
    )
    assert resolved["filing_type"] == "EFile"
    assert resolved["filing_type_code"] == "EFile"
    assert resolved["filing_type_name"] == "EFile"


CASE_TRACKING_ID = "tyler_refugio:dc~b32ef6f1-b09c-446f-a2f6-1985923d3513~CT"
CASE_DETAIL = {
    "case_tracking_id": CASE_TRACKING_ID,
    "case_number": "20250622001",
    "case_title": "Test Case",
    "jurisdiction": "refugio:dc",
    "case_category": "44675",
    "case_type": "44680",
    "case_parties": [
        {
            "id": "2fa1ea9d-32ba-45d6-a284-091cb11017b3",
            "type": "44700",
            "first_name": "JANE",
            "last_name": "DOE",
        },
        {
            "id": "8c0d2f61-1a4e-4d9c-9a11-6b0f8de3aa22",
            "type": "44701",
            "first_name": "JOHN",
            "last_name": "DOE",
        },
    ],
    "link": {
        "filing_codes": {"link": "https://example.com/filing_codes?request_id=1"},
        "filer_type_codes": {"link": "https://example.com/filer_type_codes"},
        "filing_type": {"link": "https://example.com/filing_type"},
        "party_type_codes": {"link": "https://example.com/party_type_codes"},
    },
}


def _existing_session(phase: FilingPhase, **selections) -> FilingSession:
    return FilingSession(
        conversation_id="c1",
        user_id="u1",
        mode=FilingMode.FILING_EXISTING,
        phase=phase,
        selections=dict(selections),
    )


def test_case_detail_response_is_cached_into_selections():
    """Everything the e-file payload needs is cached from the case API call."""
    session = _existing_session(FilingPhase.EXISTING_CASE_CONFIRM)
    cache_existing_case_details(session, CASE_DETAIL)
    sel = session.selections

    assert sel["case_tracking_id"] == CASE_TRACKING_ID
    assert sel["jurisdiction_code"] == "refugio:dc"
    assert sel["case_category_code"] == "44675"
    assert sel["case_type_code"] == "44680"
    # filing_party_id must be a party id from the response, not a party type.
    assert sel["filing_party_id"] == "2fa1ea9d-32ba-45d6-a284-091cb11017b3"
    assert len(sel["existing_case_parties"]) == 2
    assert sel["filing_codes_url"] == "https://example.com/filing_codes?request_id=1"
    assert sel["filer_type_codes_url"] == "https://example.com/filer_type_codes"
    assert sel["filing_type_url"] == "https://example.com/filing_type"
    assert set(sel["case_detail_links"]) == {
        "filing_codes",
        "filer_type_codes",
        "filing_type",
        "party_type_codes",
    }


def test_cached_parties_replace_a_filing_party_id_that_is_not_a_party():
    session = _existing_session(
        FilingPhase.EXISTING_CASE_CONFIRM, filing_party_id="44700"
    )
    cache_existing_case_details(session, CASE_DETAIL)
    assert (
        session.selections["filing_party_id"]
        == "2fa1ea9d-32ba-45d6-a284-091cb11017b3"
    )


@pytest.mark.asyncio
async def test_confirmed_case_asks_for_filing_code_then_court_document_type():
    """Confirm -> filing_codes -> that filing's document_type_codes -> template."""
    session = _existing_session(FilingPhase.EXISTING_CASE_CONFIRM)
    cache_existing_case_details(session, CASE_DETAIL)

    await handle_lookup(None, session, "confirm_case", {})
    assert session.phase == FilingPhase.SELECTING_FILING_CODE

    session.selections["filing_code"] = "151320"
    session.selections["document_type_codes_url"] = (
        "https://example.com/document_type_codes"
    )
    advance_phase_after_selections(session)
    assert session.phase == FilingPhase.SELECTING_DOC_TYPE_CODE

    session.selections["doc_type_code"] = "197902"
    advance_phase_after_selections(session)
    assert session.phase == FilingPhase.SELECTING_DOCUMENT_TYPE


@pytest.mark.asyncio
async def test_confirmed_case_falls_back_to_template_when_no_filing_codes_link():
    session = _existing_session(FilingPhase.EXISTING_CASE_CONFIRM)
    await handle_lookup(None, session, "confirm_case", {})
    assert session.phase == FilingPhase.SELECTING_DOCUMENT_TYPE


def test_existing_case_asks_for_filer_type_after_template_questions():
    """The case-detail filer_type/filing_type links drive the same phases the
    new-case flow uses, so ``filer_type`` reaches the payload."""
    session = _existing_session(
        FilingPhase.SELECTING_DOCUMENT_TYPE,
        template_questions_ready=True,
        filer_type_codes_url="https://example.com/filer_type_codes",
        filing_type_url="https://example.com/filing_type",
    )
    advance_phase_after_selections(session)
    assert session.phase == FilingPhase.SELECTING_FILER_TYPE

    session.selections["filer_type"] = "40467"
    advance_phase_after_selections(session)
    assert session.phase == FilingPhase.SELECTING_FILING_TYPE

    session.selections["filing_type"] = "EFile"
    advance_phase_after_selections(session)
    assert session.phase == FilingPhase.OFFERING_DOCUMENTS


def test_court_document_type_selection_stores_code_from_selected_name():
    """The user picks a document_type_codes ``name``; we keep its ``code``."""
    from app.agents.utils.db_options_format import (
        build_selection_options_payload,
        filter_selections_update,
        selection_update_for_option,
    )

    options = [
        {"code": "197902", "name": "Lead Document"},
        {"code": "197903", "name": "Attachment"},
    ]
    payload = build_selection_options_payload("selecting_doc_type_code", options)
    assert [opt["label"] for opt in payload["options"]] == [
        "Lead Document",
        "Attachment",
    ]

    update = selection_update_for_option("selecting_doc_type_code", options[0])
    resolved = filter_selections_update(
        "selecting_doc_type_code", update, options
    )
    assert resolved["doc_type_code"] == "197902"
    assert resolved["doc_type_name"] == "Lead Document"


def test_filing_code_selection_carries_document_type_codes_link():
    from app.agents.utils.db_options_format import (
        filter_selections_update,
        selection_update_for_option,
    )

    options = [
        {
            "code": "151320",
            "name": "Notice of Appeal",
            "document_type_codes_url": "https://example.com/document_type_codes",
        }
    ]
    update = selection_update_for_option("selecting_filing_code", options[0])
    resolved = filter_selections_update("selecting_filing_code", update, options)
    assert resolved["filing_code"] == "151320"
    assert (
        resolved["document_type_codes_url"]
        == "https://example.com/document_type_codes"
    )


AUTH_TOKEN = "3f1b6c1e-6b4a-4f5e-9a2a-2f5c6a7b8c9d/GENS99/8a7b6c5d-4e3f-4a2b-9c8d-1e2f3a4b5c6d"
DETAIL_AUTH_TOKEN = (
    "8167276f-efab-410b-93f6-02ed1d4f0fb5/GENS77/a813ac2c-4347-40d8-a310-4e64c7dd8a1f"
)
DETAIL_URL = (
    "https://api-stage.uslegalpro.com/v2/tx/case/detail?"
    "case_tracking_id=tyler_refugio:dc~b32ef6f1-b09c-446f-a2f6-1985923d3513~CT"
)


@pytest.mark.asyncio
async def test_search_and_detail_use_authtoken(monkeypatch):
    calls = []

    class _Client:
        def __init__(self, auth_token=None, client_token=None):
            self.auth_token = auth_token
            self.client_token = client_token

        async def get_json(self, path, params=None):
            calls.append((self.auth_token, self.client_token, path, params))
            if "search_case" in path:
                assert self.auth_token == AUTH_TOKEN
                assert self.client_token == "GENS99"
                return {
                    "items": [
                        {
                            "case_tracking_id": "tracking~CT",
                            "case_number": "20250622001",
                            "link": {
                                "case_detail": {
                                    "method": "GET",
                                    "link": DETAIL_URL,
                                    "header": {"authtoken": DETAIL_AUTH_TOKEN},
                                }
                            },
                        }
                    ]
                }
            assert self.auth_token == DETAIL_AUTH_TOKEN
            assert self.client_token == "GENS77"
            assert path == DETAIL_URL
            return {"item": {"case_number": "20250622001", "case_parties": []}}

    monkeypatch.setattr(
        "app.services.uslegalpro_existing_case_service.USLegalProApiClient",
        _Client,
    )
    service = USLegalProExistingCaseService()

    search = await service.search_case(
        state_code="TX",
        jurisdiction_code="refugio:dc",
        case_number="20250622001",
        auth_token=AUTH_TOKEN,
    )
    search_item = service.search_items(search)[0]
    detail = await service.get_case_details(
        state_code="TX",
        case_tracking_id=search_item["case_tracking_id"],
        auth_token=AUTH_TOKEN,
        case_detail_link=service.case_detail_link(search_item),
    )

    assert calls[0][2] == (
        "/v2/tx/search_case?jurisdiction=refugio:dc&case_number=20250622001"
    )
    assert calls[1][2] == DETAIL_URL
    assert service.detail_item(detail)["case_number"] == "20250622001"
