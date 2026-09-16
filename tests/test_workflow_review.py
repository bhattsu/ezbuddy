"""Pre-generation workflow answer review."""

from app.agents.conversation.orchestration.helpers import build_checklist_from_questions
from app.agents.conversation.orchestration.state import FilingSession
from app.agents.conversation.orchestration.workflow_review import (
    begin_workflow_review,
    format_workflow_review_message,
    looks_like_proceed_to_generation,
    looks_like_review_decline,
)
from app.api.schemas.filing_events import FilingPhase


def _session_with_answers() -> FilingSession:
    session = FilingSession(conversation_id="c1", user_id="u1")
    session.workflow_questions = [
        {
            "field_name": "PETITIONER_NAME",
            "field_label": "Petitioner name",
            "question_type": "TEXT",
            "required": True,
            "sort_order": 1,
        },
        {
            "field_name": "PETITIONER_PHONE",
            "field_label": "Phone number",
            "question_type": "TEXT",
            "required": True,
            "sort_order": 2,
        },
    ]
    session.checklist = build_checklist_from_questions(None, session.workflow_questions)
    session.collected_answers = {
        "PETITIONER_NAME": "Jane Doe",
        "PETITIONER_PHONE": "555-0100",
    }
    for item in session.checklist.items:
        if item.field_name in session.collected_answers:
            item.status = "answered"
    return session


def test_format_workflow_review_lists_collected_answers():
    session = _session_with_answers()
    message = format_workflow_review_message(session)
    assert "Jane Doe" in message
    assert "555-0100" in message
    assert "reply yes to generate" in message.lower()


def test_begin_workflow_review_sets_phase():
    session = _session_with_answers()
    begin_workflow_review(session)
    assert session.phase == FilingPhase.CONFIRMING_WORKFLOW_ANSWERS


def test_proceed_phrases():
    assert looks_like_proceed_to_generation("yes")
    assert looks_like_proceed_to_generation("generate the document")
    assert not looks_like_proceed_to_generation("no")
    assert looks_like_review_decline("no")
