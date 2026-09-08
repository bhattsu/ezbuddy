"""Platform and court payment verification nodes (Python only, no LLM)."""

from __future__ import annotations

import logging
import re
from typing import Any, Callable, Dict, Optional

from app.agents.conversation.orchestration.context import FilingOrchestratorContext
from app.agents.conversation.orchestration.helpers import (
    attach_selection_options_to_result,
    classify_document_offer_reply,
    persist_system_state,
    result_from_session,
)
from app.agents.conversation.orchestration.session_manager import FilingSessionManager
from app.agents.conversation.orchestration.state import FilingGraphState
from app.api.schemas.filing_events import FilingPhase
from app.services.uslegalpro_payment_service import (
    PAYMENT_ID_PROMPT,
    PaymentApiNotConfiguredError,
    USLegalProPaymentService,
    extract_payment_id,
    format_court_payment_account,
    format_court_payment_message,
    match_court_payment_account,
)

logger = logging.getLogger(__name__)

NodeFn = Callable[[FilingGraphState], Any]


def _with_account_options(result, accounts):
    return attach_selection_options_to_result(
        result,
        FilingPhase.VERIFYING_COURT_PAYMENT,
        accounts,
    )


def build_payment_nodes(ctx: FilingOrchestratorContext) -> Dict[str, NodeFn]:
    service = USLegalProPaymentService(user_repo=ctx.user_repo)

    async def verify_payment_node(state: FilingGraphState) -> FilingGraphState:
        cid = state["conversation_id"]
        session = FilingSessionManager.get(cid)
        if not session:
            raise RuntimeError(f"No session for conversation {cid}")

        user_message = str(state.get("user_message") or "")
        if session.phase == FilingPhase.CONFIRMING_EFILE:
            return await _handle_efile_confirm(ctx, state, session, user_message)
        if session.phase == FilingPhase.VERIFYING_COURT_PAYMENT:
            return await _handle_court_payment(
                ctx, service, state, session, user_message
            )
        return await _handle_platform_payment(
            ctx, service, state, session, user_message
        )

    return {"verify_payment": verify_payment_node}


async def _handle_platform_payment(ctx, service, state, session, user_message):
    payment_id = extract_payment_id(user_message)
    if not payment_id:
        result = result_from_session(
            session,
            PAYMENT_ID_PROMPT,
            event_kind="payment.platform",
        )
        await persist_system_state(ctx.conversation_repo, session)
        return {
            **state,
            "phase": session.phase.value,
            "result": result,
            "next_node": "persist",
        }

    await ctx.notify("verifying_platform_payment")
    try:
        verification = await service.verify_platform_payment(payment_id)
    except PaymentApiNotConfiguredError as exc:
        logger.error("Payment API misconfigured: %s", exc)
        result = result_from_session(
            session,
            str(exc),
            event_kind="payment.platform",
        )
        await persist_system_state(ctx.conversation_repo, session)
        return {
            **state,
            "phase": session.phase.value,
            "result": result,
            "next_node": "persist",
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("Platform payment verification failed")
        result = result_from_session(
            session,
            f"I could not verify that payment ID. {exc}\n{PAYMENT_ID_PROMPT}",
            event_kind="payment.platform",
        )
        await persist_system_state(ctx.conversation_repo, session)
        return {
            **state,
            "phase": session.phase.value,
            "result": result,
            "next_node": "persist",
        }

    session.selections["platform_payment_id"] = payment_id
    session.selections["platform_payment_customer_id"] = verification.get("customer_id")
    session.selections["platform_payment_status"] = verification.get("status")
    session.selections["platform_payment_verified"] = bool(verification.get("verified"))

    if not verification.get("verified"):
        result = result_from_session(
            session,
            str(verification.get("message") or PAYMENT_ID_PROMPT),
            event_kind="payment.platform",
            metadata={"platform_payment": verification},
        )
        await persist_system_state(ctx.conversation_repo, session)
        return {
            **state,
            "phase": session.phase.value,
            "result": result,
            "next_node": "persist",
        }

    return await _load_court_payment_accounts(
        ctx,
        service,
        state,
        session,
        prefix=str(verification.get("message") or "Payment is verified."),
    )


async def _load_court_payment_accounts(ctx, service, state, session, prefix: str):
    session.phase = FilingPhase.VERIFYING_COURT_PAYMENT
    await ctx.notify("verifying_court_payment")
    state_code = str(session.selections.get("state_code") or "il")
    try:
        response = await service.authenticate_and_get_payment_accounts(
            state_code=state_code,
            user_id=session.user_id,
        )
        accounts = [
            format_court_payment_account(item)
            for item in service.payment_account_items(response)
        ]
    except Exception as exc:  # noqa: BLE001
        logger.exception("Court payment account lookup failed")
        result = result_from_session(
            session,
            f"{prefix}\nI could not load court payment accounts. {exc}",
            event_kind="payment.court",
        )
        await persist_system_state(ctx.conversation_repo, session)
        return {
            **state,
            "phase": session.phase.value,
            "result": result,
            "next_node": "persist",
        }

    session.selections["court_payment_accounts"] = accounts
    message = prefix + "\n\n" + format_court_payment_message(accounts)
    result = result_from_session(
        session,
        message,
        event_kind="payment.court",
        metadata={"court_payment_accounts": accounts},
    )
    result = _with_account_options(result, accounts)
    await persist_system_state(ctx.conversation_repo, session)
    return {
        **state,
        "phase": session.phase.value,
        "result": result,
        "next_node": "persist",
    }


async def _handle_court_payment(ctx, service, state, session, user_message):
    accounts = list(session.selections.get("court_payment_accounts") or [])
    if not accounts:
        return await _load_court_payment_accounts(
            ctx, service, state, session, prefix="Payment is verified."
        )

    chosen = match_court_payment_account(accounts, user_message)
    if not chosen:
        result = result_from_session(
            session,
            "Please choose one of the listed court payment accounts.\n\n"
            + format_court_payment_message(accounts),
            event_kind="payment.court",
            metadata={"court_payment_accounts": accounts},
        )
        result = _with_account_options(result, accounts)
        await persist_system_state(ctx.conversation_repo, session)
        return {
            **state,
            "phase": session.phase.value,
            "result": result,
            "next_node": "persist",
        }

    session.selections["court_payment_account_id"] = chosen.get("id")
    session.selections["court_payment_account"] = chosen
    return await _show_efile_preview(ctx, state, session)


async def _show_efile_preview(ctx, state, session):
    from app.services.uslegalpro_efile_service import format_efile_preview_message

    await ctx.notify("confirming_efile")
    try:
        payload = await ctx.efile_service.build_submit_payload(
            mode=session.mode.value,
            selections=session.selections,
            collected_answers=session.collected_answers,
            generated_documents=session.generated_documents,
            workflow_questions=session.workflow_questions,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("E-file preview mapping failed")
        result = result_from_session(
            session,
            f"I could not build the e-file request yet. {exc}",
            event_kind="payment.court",
        )
        await persist_system_state(ctx.conversation_repo, session)
        return {
            **state,
            "phase": session.phase.value,
            "result": result,
            "next_node": "persist",
        }

    session.selections["efile_payload_preview"] = payload
    session.phase = FilingPhase.CONFIRMING_EFILE
    result = result_from_session(
        session,
        format_efile_preview_message(payload),
        event_kind="efile.confirm",
        metadata={"efile_payload_preview": payload},
    )
    await persist_system_state(ctx.conversation_repo, session)
    return {
        **state,
        "phase": session.phase.value,
        "result": result,
        "next_node": "persist",
    }


async def _handle_efile_confirm(ctx, state, session, user_message):
    from app.services.uslegalpro_efile_service import (
        format_efile_preview_message,
        format_efile_success_message,
    )

    preview = session.selections.get("efile_payload_preview")
    if not isinstance(preview, dict) or not preview:
        return await _show_efile_preview(ctx, state, session)

    intent = classify_document_offer_reply(user_message)
    if re.search(r"\b(confirm|submit|efile|e-file)\b", user_message or "", re.I):
        intent = "yes"
    if intent == "done":
        intent = "yes"
    if intent == "no":
        session.selections.pop("efile_payload_preview", None)
        session.selections.pop("efile_payload_override", None)
        session.phase = FilingPhase.VERIFYING_COURT_PAYMENT
        result = result_from_session(
            session,
            "E-file submission was cancelled. Choose a court payment account "
            "again if you want to continue.",
            event_kind="payment.court",
            metadata={
                "court_payment_accounts": session.selections.get(
                    "court_payment_accounts"
                ),
            },
        )
        result = _with_account_options(
            result, list(session.selections.get("court_payment_accounts") or [])
        )
        await persist_system_state(ctx.conversation_repo, session)
        return {
            **state,
            "phase": session.phase.value,
            "result": result,
            "next_node": "persist",
        }

    if intent != "yes":
        result = result_from_session(
            session,
            format_efile_preview_message(preview),
            event_kind="efile.confirm",
            metadata={"efile_payload_preview": preview},
        )
        await persist_system_state(ctx.conversation_repo, session)
        return {
            **state,
            "phase": session.phase.value,
            "result": result,
            "next_node": "persist",
        }

    session.selections["efile_payload_override"] = preview
    await ctx.notify("submitting_efile")
    submit_result = await _submit_efile_after_payment(ctx, session)
    if submit_result is None:
        error = str(session.selections.get("efile_submit_error") or "").strip()
        result = result_from_session(
            session,
            "I could not submit the filing."
            + (f" {error}" if error else "")
            + "\nReply yes to try again, or no to cancel.",
            event_kind="efile.confirm",
            metadata={"efile_payload_preview": preview},
        )
        await persist_system_state(ctx.conversation_repo, session)
        return {
            **state,
            "phase": session.phase.value,
            "result": result,
            "next_node": "persist",
        }

    session.selections["reference_id"] = submit_result.reference_id
    session.selections["envelope_id"] = submit_result.envelope_id
    session.selections["case_tracking_id"] = (
        submit_result.case_tracking_id or session.selections.get("case_tracking_id")
    )
    session.selections["efile_submit_filings"] = submit_result.filings
    session.selections["efile_submit_status"] = submit_result.status
    session.selections["efile_submit_message"] = submit_result.message
    session.phase = FilingPhase.COMPLETE
    await ctx.conversation_repo.complete_conversation(session.conversation_id)
    await persist_system_state(ctx.conversation_repo, session)
    result = result_from_session(
        session,
        format_efile_success_message(submit_result),
        event_kind="documents.ready",
        metadata={
            "generated_documents": session.generated_documents,
            "court_payment_account": session.selections.get("court_payment_account"),
            "envelope_id": submit_result.envelope_id,
            "reference_id": submit_result.reference_id,
            "case_tracking_id": submit_result.case_tracking_id,
            "efile_submit_filings": submit_result.filings,
            "efile_submit_status": submit_result.status,
            "efile_response": submit_result.raw,
        },
    )
    return {
        **state,
        "phase": session.phase.value,
        "result": result,
        "next_node": "persist",
    }


async def _submit_efile_after_payment(ctx, session) -> Optional[Any]:
    try:
        submitted = await ctx.efile_service.submit(
            user_id=session.user_id,
            mode=session.mode.value,
            selections=session.selections,
            collected_answers=session.collected_answers,
            generated_documents=session.generated_documents,
            workflow_questions=session.workflow_questions,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("E-file submission failed")
        session.selections["efile_submit_error"] = str(exc)
        return None

    conv = await ctx.conversation_repo.get_conversation(session.conversation_id)
    session_id = str((conv or {}).get("session_id") or "").strip()
    provider_id = (
        str(session.selections.get("efile_provider_id") or "").strip()
        or await ctx.submission_repo.resolve_provider_id(("EFILE", "COURT_EFILE", "TYLER"))
        or ""
    )
    if provider_id:
        session.selections["efile_provider_id"] = provider_id

    if session_id and provider_id:
        record = await ctx.submission_repo.insert_submission(
            session_id=session_id,
            provider_id=provider_id,
            reference_number=submitted.reference_id,
            submission_status=submitted.status,
            response_message={
                "envelope_id": submitted.envelope_id,
                "status": submitted.status,
                "message": submitted.message,
                "raw": submitted.raw,
            },
        )
        if record:
            session.selections["submission_id"] = str(record.get("submission_id") or "")

    return submitted
