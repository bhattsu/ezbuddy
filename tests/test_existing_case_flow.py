"""Tests for authenticated existing-case API navigation."""

from __future__ import annotations

import pytest

from app.agents.conversation.orchestration.helpers import (
    advance_phase_after_selections,
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


AUTH_TOKEN = "3f1b6c1e-6b4a-4f5e-9a2a-2f5c6a7b8c9d/GENS99/8a7b6c5d-4e3f-4a2b-9c8d-1e2f3a4b5c6d"


@pytest.mark.asyncio
async def test_search_and_detail_use_authtoken(monkeypatch):
    calls = []

    class _Client:
        def __init__(self, auth_token=None, client_token=None):
            assert auth_token == AUTH_TOKEN
            # The session's own client token must be used, not the configured one.
            assert client_token == "GENS99"
            self.auth_token = auth_token
            self.client_token = client_token

        async def get_json(self, path, params=None):
            calls.append((path, params))
            if "search_case" in path:
                return {
                    "items": [
                        {
                            "case_tracking_id": "tracking~CT",
                            "case_number": "20250622001",
                        }
                    ]
                }
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
    tracking_id = service.search_items(search)[0]["case_tracking_id"]
    detail = await service.get_case_details(
        state_code="TX",
        case_tracking_id=tracking_id,
        auth_token=AUTH_TOKEN,
    )

    assert calls[0] == (
        "/v2/tx/search_case?jurisdiction=refugio:dc&case_number=20250622001",
        None,
    )
    assert calls[1][0] == "/v2/tx/case/tracking~CT"
    assert service.detail_item(detail)["case_number"] == "20250622001"
