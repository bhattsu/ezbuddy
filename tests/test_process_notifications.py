"""Tests for simple process notification toasts."""

from app.api.schemas.filing_events import FilingMode, FilingPhase
from app.services.filing_assistant_service import FilingAssistantService
from app.services.process_notifications import (
    build_notification,
    loading_process_for_phase,
    merge_notifications,
    notifications_for_result,
)


def test_build_notification_uses_process_copy():
    note = build_notification("extracting_questions")
    assert note["process"] == "extracting_questions"
    assert "extracting the questions" in note["message"].lower()
    assert note["level"] == "info"

    loading = build_notification("loading_questions")
    assert "loading the questions" in loading["message"].lower()
    submit = build_notification("submitting_efile")
    assert "submitting" in submit["message"].lower()


def test_loading_process_for_phase():
    assert loading_process_for_phase(FilingPhase.SELECTING_JURISDICTION) == "loading_courts"
    assert loading_process_for_phase(FilingPhase.SELECTING_DOCUMENT_TYPE) == (
        "loading_document_types"
    )
    assert loading_process_for_phase(None) is None


def test_notifications_for_result_uses_phase_and_event():
    from_event = notifications_for_result(
        event_kind="documents.ready",
        phase=FilingPhase.COMPLETE,
    )
    assert from_event[0]["process"] == "documents_ready"
    assert from_event[0]["level"] == "success"

    from_phase = notifications_for_result(
        event_kind="assistant.message",
        phase=FilingPhase.SELECTING_DOCUMENT_TYPE,
    )
    assert from_phase[0]["process"] == "selecting_document_type"

    generic = notifications_for_result(
        event_kind="assistant.message",
        phase=FilingPhase.INTENT_PENDING,
        mode=FilingMode.GENERIC,
    )
    assert generic[0]["process"] == "generic_legal"


def test_merge_notifications_dedupes_by_process():
    merged = merge_notifications(
        [build_notification("extracting_template")],
        [
            build_notification("extracting_template", message="duplicate"),
            build_notification("questions_ready"),
        ],
    )
    assert [item["process"] for item in merged] == [
        "extracting_template",
        "questions_ready",
    ]


def test_service_emits_notification_then_main_event():
    from app.agents.conversation.orchestration.state import OrchestratorResult
    from app.api.schemas.filing_events import FilingMode

    result = OrchestratorResult(
        assistant_message="Choose a document.",
        conversation_id="c1",
        phase=FilingPhase.SELECTING_DOCUMENT_TYPE,
        mode=FilingMode.FILING_NEW,
        notifications=[build_notification("extracting_template")],
    )
    service = FilingAssistantService(orchestrator=object())
    events = service.events_from_result(result)
    types = [event["type"] for event in events]
    assert types[0] == "notification"
    assert types[-1] == "assistant.message"
    processes = [
        event["payload"]["process"]
        for event in events
        if event["type"] == "notification"
    ]
    assert "extracting_template" in processes
    assert "selecting_document_type" in processes
