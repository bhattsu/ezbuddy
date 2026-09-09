"""E-file JSON is shown first; POST happens only after the user confirms."""

from __future__ import annotations

import pytest

from app.agents.conversation.orchestration.payment_nodes import (
    _handle_efile_confirm,
    _show_efile_preview,
)
from app.agents.conversation.orchestration.state import FilingSession
from app.api.schemas.filing_events import FilingMode, FilingPhase
from app.services.uslegalpro_efile_service import EFileSubmitResult


class _Repo:
    async def insert_system_message(self, *args, **kwargs):
        return None

    async def complete_conversation(self, *args, **kwargs):
        return None

    async def get_conversation(self, *args, **kwargs):
        return {"session_id": "sess-1"}


class _SubmissionRepo:
    async def resolve_provider_id(self, *args, **kwargs):
        return ""

    async def insert_submission(self, *args, **kwargs):
        return None


class _EfileService:
    def __init__(self) -> None:
        self.submit_calls = 0
        self.preview = {"data": {"reference_id": "DRAFT-2026-10034"}}

    async def build_submit_payload(self, **kwargs):
        return self.preview

    async def submit(self, **kwargs):
        self.submit_calls += 1
        return EFileSubmitResult(
            envelope_id="326710",
            reference_id="DRAFT-2026-10034",
            status="submitted",
            message="ok",
            raw={},
        )


class _Ctx:
    def __init__(self) -> None:
        self.conversation_repo = _Repo()
        self.submission_repo = _SubmissionRepo()
        self.efile_service = _EfileService()
        self.notices: list[str] = []

    async def notify(self, process: str):
        self.notices.append(process)


def _session():
    session = FilingSession(conversation_id="conv-1", user_id="user-1")
    session.mode = FilingMode.FILING_NEW
    session.phase = FilingPhase.VERIFYING_COURT_PAYMENT
    session.selections = {"court_payment_account_id": "CC_1"}
    return session


@pytest.mark.asyncio
async def test_preview_does_not_submit():
    ctx = _Ctx()
    session = _session()
    state = {"conversation_id": "conv-1", "user_id": "user-1"}
    result_state = await _show_efile_preview(ctx, state, session)
    assert ctx.efile_service.submit_calls == 0
    assert session.phase == FilingPhase.CONFIRMING_EFILE
    assert "This is the e-file request JSON" in result_state["result"].assistant_message


@pytest.mark.asyncio
async def test_yes_submits_preview_payload():
    ctx = _Ctx()
    session = _session()
    session.phase = FilingPhase.CONFIRMING_EFILE
    session.selections["efile_payload_preview"] = ctx.efile_service.preview
    state = {"conversation_id": "conv-1", "user_id": "user-1"}
    await _handle_efile_confirm(ctx, state, session, "yes")
    assert ctx.efile_service.submit_calls == 1
    assert session.selections["efile_payload_override"] == ctx.efile_service.preview
    assert session.phase == FilingPhase.COMPLETE


@pytest.mark.asyncio
async def test_no_cancels_without_submit():
    ctx = _Ctx()
    session = _session()
    session.phase = FilingPhase.CONFIRMING_EFILE
    session.selections["efile_payload_preview"] = ctx.efile_service.preview
    state = {"conversation_id": "conv-1", "user_id": "user-1"}
    await _handle_efile_confirm(ctx, state, session, "no")
    assert ctx.efile_service.submit_calls == 0
    assert session.phase == FilingPhase.VERIFYING_COURT_PAYMENT
