"""Tests for filled-PDF upload matching and skipped questions."""

from app.agents.conversation.orchestration.helpers import is_new_filing_prefill_phase
from app.agents.conversation.orchestration.prefill import (
    heuristic_map_analysis_to_answers,
)
from app.agents.conversation.orchestration.state import FilingSession
from app.api.schemas.filing_events import FilingMode, FilingPhase


def test_heuristic_maps_only_filled_values_and_aliases():
    questions = [
        {
            "field_name": "CAUSE_NUMBER",
            "pdf_field": "Case Number",
            "mapping_source": "CAUSE_NUMBER",
        },
        {
            "field_name": "PETITIONER_FIRST_NAME",
            "field_label": "Petitioner First Name",
        },
        {"field_name": "MARRIAGE_DATE"},
    ]
    analyses = [
        {
            "extracted_fields": {
                "case_number": "123",
                "petitioner.first_name": "Jane",
                "marriage_date": "",
                "blank_box": None,
            },
            "user_details": {"cause_number": "123"},
        }
    ]
    mapped = heuristic_map_analysis_to_answers(questions, analyses, {})
    assert mapped["CAUSE_NUMBER"] == "123"
    assert mapped["PETITIONER_FIRST_NAME"] == "Jane"
    assert "MARRIAGE_DATE" not in mapped


def test_heuristic_does_not_overwrite_existing_answers():
    questions = [{"field_name": "CAUSE_NUMBER", "pdf_field": "Case Number"}]
    mapped = heuristic_map_analysis_to_answers(
        questions,
        [{"extracted_fields": {"case_number": "999"}}],
        {"CAUSE_NUMBER": "123"},
    )
    assert mapped == {}


def test_prefill_phase_after_template_questions():
    session = FilingSession(
        conversation_id="c1",
        user_id="u1",
        mode=FilingMode.FILING_NEW,
        phase=FilingPhase.OFFERING_DOCUMENTS,
    )
    session.selections["template_questions_ready"] = True
    assert is_new_filing_prefill_phase(session) is True

    session.phase = FilingPhase.COLLECTING_WORKFLOW_ANSWERS
    assert is_new_filing_prefill_phase(session) is True

    session.phase = FilingPhase.SELECTING_STATE
    session.selections["template_questions_ready"] = False
    assert is_new_filing_prefill_phase(session) is False
