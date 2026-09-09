"""E-file JSON is shown first; POST happens only after the user confirms."""

from __future__ import annotations

import pytest

from app.agents.conversation.orchestration.payment_nodes import (
    _handle_efile_confirm,
    _show_efile_preview,
    _submit_efile_after_payment,
)
from app.agents.conversation.orchestration.state import FilingSession
from app.api.schemas.filing_events import FilingMode, FilingPhase
from app.services.efile_validator import (
    EFilePayloadValidationError,
    ValidationResult,
)
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


class _ValidatorFailingEfileService(_EfileService):
    """Simulate a service whose ``build_submit_payload`` fails validation."""

    async def build_submit_payload(self, **kwargs):
        raise EFilePayloadValidationError(
            ValidationResult(
                is_valid=False,
                errors=[
                    "data.filer_type '' is not one of the filer types for case type 209421 (allowed: 54325)",
                    "data.filings[0].doc_type '99999' is not a valid document type for filing code '209523' (allowed: 53689)",
                ],
                warnings=[],
            )
        )


class _SubmitFailingEfileService(_EfileService):
    """Simulate a service that raises validation during ``submit``."""

    async def submit(self, **kwargs):  # type: ignore[override]
        raise EFilePayloadValidationError(
            ValidationResult(
                is_valid=False,
                errors=["data.filing_party_id 'Party_missing' does not match any case_parties[].id (available: ['Party_1'])"],
                warnings=[],
            )
        )


@pytest.mark.asyncio
async def test_preview_surfaces_validation_errors():
    ctx = _Ctx()
    ctx.efile_service = _ValidatorFailingEfileService()
    session = _session()
    state = {"conversation_id": "conv-1", "user_id": "user-1"}
    result_state = await _show_efile_preview(ctx, state, session)
    message = result_state["result"].assistant_message
    assert "I cannot build a valid e-file request" in message
    assert "filer_type" in message
    assert "doc_type" in message
    # Session did not advance past VERIFYING_COURT_PAYMENT.
    assert session.phase == FilingPhase.VERIFYING_COURT_PAYMENT


@pytest.mark.asyncio
async def test_submit_surfaces_validation_errors_via_selections():
    ctx = _Ctx()
    ctx.efile_service = _SubmitFailingEfileService()
    session = _session()
    submit_result = await _submit_efile_after_payment(ctx, session)
    assert submit_result is None
    error_message = session.selections.get("efile_submit_error") or ""
    assert "filing_party_id" in error_message
    assert "I cannot build a valid e-file request" in error_message
    assert isinstance(session.selections.get("efile_validation_errors"), list)
