"""Existing-case flow must not rewind jurisdiction / case-number steps."""

from app.agents.conversation.orchestration.flow_redirects import resolve_flow_redirect
from app.agents.conversation.orchestration.helpers import advance_mode_from_intent
from app.agents.conversation.orchestration.state import FilingSession
from app.api.schemas.filing_events import FilingMode, FilingPhase


def _session(phase: FilingPhase, **selections) -> FilingSession:
    session = FilingSession(conversation_id="c1", user_id="u1")
    session.mode = FilingMode.FILING_EXISTING
    session.phase = phase
    session.selections.update(
        {
            "state_code": "tx",
            "jurisdiction_code": "hidalgo:dc",
            "jurisdiction_name": "Hidalgo County - District Clerk",
            **selections,
        }
    )
    return session


def test_filing_existing_intent_does_not_rewind_jurisdiction_step():
    session = _session(FilingPhase.EXISTING_ENTER_CASE_NUMBER, case_number="20250622001")
    advance_mode_from_intent(session, "filing_existing")
    assert session.phase == FilingPhase.EXISTING_ENTER_CASE_NUMBER


def test_filing_existing_intent_does_not_rewind_case_confirm():
    session = _session(
        FilingPhase.EXISTING_CASE_CONFIRM,
        case_number="20250622001",
        case_tracking_id="abc123",
    )
    advance_mode_from_intent(session, "filing_existing")
    assert session.phase == FilingPhase.EXISTING_CASE_CONFIRM


def test_filing_existing_intent_still_starts_from_intent_pending():
    session = FilingSession(conversation_id="c1", user_id="u1")
    session.phase = FilingPhase.INTENT_PENDING
    session.selections["state_code"] = "tx"
    advance_mode_from_intent(session, "filing_existing")
    assert session.mode == FilingMode.FILING_EXISTING
    assert session.phase == FilingPhase.EXISTING_SELECTING_JURISDICTION


def test_yes_at_case_confirm_does_not_apply_stale_pending_redirect():
    session = _session(
        FilingPhase.EXISTING_CASE_CONFIRM,
        case_number="20250622001",
        case_tracking_id="tyler_refugio:dc~abc",
    )
    session.selections["_pending_flow_redirect"] = (
        FilingPhase.EXISTING_SELECTING_JURISDICTION.value
    )
    redirect = resolve_flow_redirect(session, "yes")
    assert redirect is None
    assert session.phase == FilingPhase.EXISTING_CASE_CONFIRM
    assert "_pending_flow_redirect" not in session.selections


def test_filing_existing_sets_mode_on_state_selection_turn():
    session = FilingSession(conversation_id="c1", user_id="u1")
    session.phase = FilingPhase.SELECTING_STATE
    advance_mode_from_intent(session, "filing_existing")
    assert session.mode == FilingMode.FILING_EXISTING
    assert session.phase == FilingPhase.SELECTING_STATE
