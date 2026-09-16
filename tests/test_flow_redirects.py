"""Agentic flow redirect and fallback guidance."""

from app.agents.conversation.orchestration.flow_redirects import (
    apply_flow_redirect,
    enrich_failure_with_guidance,
    looks_like_case_number_entry,
    reconcile_missed_jurisdiction_redirect,
    resolve_flow_redirect,
    sync_phase_after_court_change,
)
from app.agents.conversation.orchestration.state import FilingSession
from app.api.schemas.filing_events import FilingMode, FilingPhase


def _existing_session(**selections) -> FilingSession:
    session = FilingSession(conversation_id="c1", user_id="u1")
    session.mode = FilingMode.FILING_EXISTING
    session.phase = FilingPhase.EXISTING_ENTER_CASE_NUMBER
    session.selections.update(
        {
            "state_code": "tx",
            "jurisdiction_code": "hidalgo:dc",
            "jurisdiction_name": "Hidalgo County - District Clerk",
            **selections,
        }
    )
    return session


def test_select_jurisdiction_again_redirects():
    session = _existing_session(case_number="20250622001")
    redirect = resolve_flow_redirect(session, "select jurisdiction again")
    assert redirect is not None
    assert redirect.target_phase == FilingPhase.EXISTING_SELECTING_JURISDICTION
    msg = apply_flow_redirect(session, redirect)
    assert session.phase == FilingPhase.EXISTING_SELECTING_JURISDICTION
    assert "case_number" not in session.selections
    assert "select the court" in msg.lower()


def test_yes_after_failure_offer_redirects_to_court():
    session = _existing_session()
    session.selections["_pending_flow_redirect"] = (
        FilingPhase.EXISTING_SELECTING_JURISDICTION.value
    )
    redirect = resolve_flow_redirect(session, "yes")
    assert redirect is not None
    assert redirect.target_phase == FilingPhase.EXISTING_SELECTING_JURISDICTION


def test_search_again_from_start_is_not_case_number():
    session = _existing_session()
    assert not looks_like_case_number_entry(session, "search case again from start")
    redirect = resolve_flow_redirect(session, "search case again from start")
    assert redirect.target_phase == FilingPhase.EXISTING_SELECTING_JURISDICTION


def test_numeric_case_number_still_accepted():
    session = _existing_session()
    assert looks_like_case_number_entry(session, "20250622001")
    assert resolve_flow_redirect(session, "20250622001") is None


def test_change_case_type_keeps_jurisdiction():
    session = FilingSession(conversation_id="c1", user_id="u1")
    session.mode = FilingMode.FILING_NEW
    session.phase = FilingPhase.COLLECTING_WORKFLOW_ANSWERS
    session.selections.update(
        {
            "state_code": "tx",
            "jurisdiction_code": "dallas:dc",
            "case_category_code": "131370",
            "case_type_code": "16102",
        }
    )
    redirect = resolve_flow_redirect(session, "I want to change the case type")
    assert redirect.target_phase == FilingPhase.SELECTING_CASE_TYPE
    apply_flow_redirect(session, redirect)
    assert session.selections["jurisdiction_code"] == "dallas:dc"
    assert "case_type_code" not in session.selections


def test_change_your_court_jurisdiction_redirects():
    session = FilingSession(conversation_id="c1", user_id="u1")
    session.mode = FilingMode.FILING_NEW
    session.phase = FilingPhase.SELECTING_FILING_CODE
    session.selections.update(
        {
            "state_code": "tx",
            "filing_codes_url": "http://example/filing-codes",
            "cached_filing_codes": [{"code": "x", "name": "Old code"}],
        }
    )
    redirect = resolve_flow_redirect(session, "I want to change your court jurisdiction")
    assert redirect is not None
    apply_flow_redirect(session, redirect)
    assert session.phase == FilingPhase.SELECTING_JURISDICTION
    assert "filing_codes_url" not in session.selections
    assert "cached_filing_codes" not in session.selections


def test_reconcile_missed_jurisdiction_redirect_after_llm_reply():
    session = FilingSession(conversation_id="c1", user_id="u1")
    session.mode = FilingMode.FILING_NEW
    session.phase = FilingPhase.SELECTING_FILING_CODE
    session.selections.update({"state_code": "tx", "filing_code": "appeal"})
    assistant = (
        "Understood. You would like to change your court jurisdiction. "
        "The orchestrator will return you to the jurisdiction selection step."
    )
    msg = reconcile_missed_jurisdiction_redirect(
        session,
        "change your court jurisdiction",
        assistant_message=assistant,
    )
    assert msg
    assert session.phase == FilingPhase.SELECTING_JURISDICTION
    assert "filing_code" not in session.selections


def test_change_party_type_redirects_from_filing_code():
    session = FilingSession(conversation_id="c1", user_id="u1")
    session.mode = FilingMode.FILING_NEW
    session.phase = FilingPhase.SELECTING_FILING_CODE
    session.selections.update(
        {
            "state_code": "tx",
            "case_topic": "divorce",
            "jurisdiction_code": "harris:dc",
            "case_type_code": "209421",
            "party_type_code": "53024",
            "filing_code": "petition",
            "filing_codes_url": "http://example/filing-codes",
        }
    )
    redirect = resolve_flow_redirect(session, "i need to change party type")
    assert redirect is not None
    assert redirect.target_phase == FilingPhase.SELECTING_CASE_PARTIES
    apply_flow_redirect(session, redirect)
    assert session.phase == FilingPhase.SELECTING_CASE_PARTIES
    assert "party_type_code" not in session.selections
    assert "filing_code" not in session.selections
    assert session.selections["case_type_code"] == "209421"


def test_change_the_jurisdiction_redirects():
    session = FilingSession(conversation_id="c1", user_id="u1")
    session.mode = FilingMode.FILING_NEW
    session.phase = FilingPhase.SELECTING_DOCUMENT_TYPE
    session.selections.update(
        {
            "state_code": "tx",
            "case_topic": "divorce",
            "jurisdiction_code": "andrews:dc",
            "case_type_code": "123",
            "template_questions_ready": True,
        }
    )
    redirect = resolve_flow_redirect(session, "I want to change the jurisdiction")
    assert redirect is not None
    assert redirect.target_phase == FilingPhase.SELECTING_JURISDICTION
    apply_flow_redirect(session, redirect)
    assert session.phase == FilingPhase.SELECTING_JURISDICTION
    assert "jurisdiction_code" not in session.selections
    assert "case_type_code" not in session.selections
    assert "template_questions_ready" not in session.selections


def test_yes_after_court_confirm_restarts_category_selection():
    session = FilingSession(conversation_id="c1", user_id="u1")
    session.mode = FilingMode.FILING_NEW
    session.phase = FilingPhase.SELECTING_DOCUMENT_TYPE
    session.selections.update(
        {
            "state_code": "tx",
            "case_topic": "divorce",
            "jurisdiction_code": "harris:dc",
            "jurisdiction_name": "Harris County - District Clerk",
            "case_type_code": "old-type",
            "template_questions_ready": True,
        }
    )
    last = (
        "Please confirm you would like to proceed with Harris County - District Clerk "
        "for your divorce with children filing."
    )
    redirect = resolve_flow_redirect(session, "yes", last_assistant_message=last)
    assert redirect is not None
    assert redirect.label == "confirm_court_change"
    assert redirect.target_phase == FilingPhase.SELECTING_CASE_CATEGORY
    apply_flow_redirect(session, redirect)
    assert session.phase == FilingPhase.SELECTING_CASE_CATEGORY
    assert session.selections["jurisdiction_code"] == "harris:dc"
    assert "case_type_code" not in session.selections
    assert "template_questions_ready" not in session.selections


def test_sync_phase_after_court_change_clears_stale_selections():
    session = FilingSession(conversation_id="c1", user_id="u1")
    session.mode = FilingMode.FILING_NEW
    session.phase = FilingPhase.SELECTING_DOCUMENT_TYPE
    session.selections.update(
        {
            "state_code": "tx",
            "jurisdiction_code": "harris:dc",
            "case_type_code": "old-type",
            "filing_code": "old-code",
            "template_questions_ready": True,
        }
    )
    assert sync_phase_after_court_change(session, "andrews:dc")
    assert session.phase == FilingPhase.SELECTING_CASE_CATEGORY
    assert session.selections["jurisdiction_code"] == "harris:dc"
    assert "case_type_code" not in session.selections
    assert "filing_code" not in session.selections


def test_typo_different_jurisdiction_redirects():
    session = FilingSession(conversation_id="c1", user_id="u1")
    session.mode = FilingMode.FILING_NEW
    session.phase = FilingPhase.SELECTING_FILING_CODE
    session.selections.update(
        {
            "state_code": "tx",
            "case_topic": "small claims",
            "jurisdiction_code": "harrison:jp1",
            "jurisdiction_name": "Harrison County - JP Precinct 1",
            "filing_code": "petition",
        }
    )
    redirect = resolve_flow_redirect(session, "I need to chose diiferent jurisdiction")
    assert redirect is not None
    assert redirect.target_phase == FilingPhase.SELECTING_JURISDICTION
    apply_flow_redirect(session, redirect)
    assert "jurisdiction_code" not in session.selections
    assert session.selections["case_topic"] == "small claims"


def test_divorce_topic_change_restarts_court_selection():
    session = FilingSession(conversation_id="c1", user_id="u1")
    session.mode = FilingMode.FILING_NEW
    session.phase = FilingPhase.SELECTING_DOC_TYPE_CODE
    session.selections.update(
        {
            "state_code": "tx",
            "case_topic": "small claims",
            "jurisdiction_code": "harrison:jp1",
            "filing_code": "petition",
        }
    )
    redirect = resolve_flow_redirect(session, "I need to file a divorce case")
    assert redirect is not None
    assert redirect.label == "change_case_topic"
    assert redirect.target_phase == FilingPhase.SELECTING_JURISDICTION
    apply_flow_redirect(session, redirect)
    assert session.selections["case_topic"] == "divorce"
    assert "jurisdiction_code" not in session.selections


def test_failure_guidance_sets_pending_redirect():
    session = _existing_session()
    msg = enrich_failure_with_guidance(
        session,
        "No case was found for that case number and jurisdiction.",
        context="case_search",
    )
    assert "different court" in msg.lower()
    assert session.selections["_pending_flow_redirect"] == (
        FilingPhase.EXISTING_SELECTING_JURISDICTION.value
    )
