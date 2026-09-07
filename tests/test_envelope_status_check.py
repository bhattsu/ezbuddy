from __future__ import annotations

import pytest

from app.agents.conversation.orchestration.nodes import _handle_status_check
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
    assert "ACCEPTED" in message
    assert meta["envelope_status"] == "ACCEPTED"
    assert session.selections["case_tracking_id"] == "trk-22"
    assert calls["updated"] is True
