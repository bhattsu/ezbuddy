from __future__ import annotations

import pytest

from app.agents.conversation.orchestration.nodes import (
    _envelope_status_message,
    _extract_envelope_id,
    _handle_status_check,
    _is_valid_envelope_id,
)
from app.agents.conversation.orchestration.state import FilingSession
from app.api.schemas.filing_events import FilingMode, FilingPhase


@pytest.mark.asyncio
async def test_handle_status_check_updates_submission_tracking():
    calls = {"updated": False}

    class _ConversationRepo:
        async def get_conversation(self, _conversation_id):
            return {"session_id": "44444444-4444-4444-4444-444444444444"}

    class _EFileService:
        async def envelope_status(self, **kwargs):
            assert kwargs["envelope_id"] == "325900"
            return {
                "item": {
                    "status": "ACCEPTED",
                    "case_number": "2026-0001",
                    "case_tracking_id": "trk-22",
                }
            }

    class _SubmissionRepo:
        async def latest_for_session(self, _session_id):
            return {"submission_id": "55555555-5555-5555-5555-555555555555"}

        async def by_reference(self, _reference):
            return None

        async def update_submission_status(self, **kwargs):
            calls["updated"] = True
            assert kwargs["submission_status"] == "ACCEPTED"
            return {"submission_id": kwargs["submission_id"]}

    class _Ctx:
        def __init__(self):
            self.conversation_repo = _ConversationRepo()
            self.efile_service = _EFileService()
            self.submission_repo = _SubmissionRepo()

        async def notify(self, _process, message=None, level=None):
            return {"process": _process, "message": message, "level": level}

    session = FilingSession(
        conversation_id="conv-1",
        user_id="u1",
        mode=FilingMode.FILING_EXISTING,
        phase=FilingPhase.COMPLETE,
        selections={"state_code": "tx", "reference_id": "REF-123"},
    )
    message, meta = await _handle_status_check(
        _Ctx(),
        session,
        "what is status for envelope 325900",
        {},
    )
    assert "325900" in message
    assert "status 'ACCEPTED'" in message
    assert meta["envelope_status"] == "ACCEPTED"
    assert session.selections["case_tracking_id"] == "trk-22"
    assert calls["updated"] is True


@pytest.mark.asyncio
async def test_handle_status_check_ignores_case_word_from_status_phrase():
    class _Ctx:
        conversation_repo = None
        efile_service = None
        submission_repo = None

        async def notify(self, *_args, **_kwargs):
            return {}

    session = FilingSession(
        conversation_id="conv-3",
        user_id="u1",
        mode=FilingMode.GENERIC,
        phase=FilingPhase.INTENT_PENDING,
        selections={"state_code": "tx"},
    )
    message, meta = await _handle_status_check(
        _Ctx(),
        session,
        "need to check my case status",
        {"envelope_id": "case"},
    )
    assert "envelope ID" in message
    assert meta == {}
    assert session.selections["pending_envelope_status_check"] is True


def test_extract_envelope_id_numeric_only():
    assert _extract_envelope_id("need to check my case status") == ""
    assert _extract_envelope_id("what is status for envelope 325900") == "325900"
    assert _extract_envelope_id("325900") == "325900"
    assert not _is_valid_envelope_id("case")
    assert _is_valid_envelope_id("326710")


@pytest.mark.asyncio
async def test_handle_status_check_asks_for_envelope_id_and_sets_pending_flag():
    class _Ctx:
        conversation_repo = None
        efile_service = None
        submission_repo = None

        async def notify(self, *_args, **_kwargs):
            return {}

    session = FilingSession(
        conversation_id="conv-2",
        user_id="u1",
        mode=FilingMode.GENERIC,
        phase=FilingPhase.COMPLETE,
        selections={"state_code": "dc"},
    )
    message, meta = await _handle_status_check(
        _Ctx(),
        session,
        "what is the status?",
        {},
    )
    assert "envelope ID" in message
    assert meta == {}
    assert session.selections["pending_envelope_status_check"] is True


def test_envelope_status_message_interprets_api_response():
    item = {
        "submitter": {"full_name": "Necile, Jane"},
        "submitted_on": "2026-09-11T07:44:21.0Z",
        "filings": [
            {
                "file_name": "case_document.pdf",
                "reviewer_comment": "",
                "status_reason": "",
                "status": "submitted",
            }
        ],
        "case_number": "",
        "case_tracking_id": "tyler_harris:dc~f417f07f-85b5-4c4f-8473-9a5261ab1fb5~CT",
        "status": "submitted",
    }
    message = _envelope_status_message(item, "326710")
    assert "326710" in message
    assert "status 'submitted'" in message
    assert "Necile, Jane" in message
    assert "case_document.pdf" in message
    assert "has status 'submitted'" in message
    assert "has not provided a reviewer comment or status reason yet" in message
    assert "Case tracking ID" in message
    assert "not been assigned yet" in message


def test_envelope_status_message_includes_court_notes_when_present():
    item = {
        "status": "returned",
        "filings": [
            {
                "file_name": "motion.pdf",
                "reviewer_comment": "Missing signature on page 2.",
                "status_reason": "Deficiency",
                "status": "returned",
            }
        ],
    }
    message = _envelope_status_message(item, "327001")
    assert "status 'returned'" in message
    assert "reviewer comment: Missing signature on page 2." in message
    assert "status reason: Deficiency" in message
