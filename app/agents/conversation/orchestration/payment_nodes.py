"""Platform and court payment verification nodes (Python only, no LLM)."""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict

from app.agents.conversation.orchestration.context import FilingOrchestratorContext
from app.agents.conversation.orchestration.helpers import (
    attach_selection_options_to_result,
    persist_system_state,
    result_from_session,
)
from app.agents.conversation.orchestration.session_manager import FilingSessionManager
from app.agents.conversation.orchestration.state import FilingGraphState
from app.api.schemas.filing_events import FilingPhase
from app.services.uslegalpro_payment_service import (
    COURT_ACCOUNT_THANKS,
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
    session.phase = FilingPhase.COMPLETE
    await ctx.conversation_repo.complete_conversation(session.conversation_id)
    await persist_system_state(ctx.conversation_repo, session)
    result = result_from_session(
        session,
        COURT_ACCOUNT_THANKS,
        event_kind="documents.ready",
        metadata={
            "generated_documents": session.generated_documents,
            "court_payment_account": chosen,
        },
    )
    return {
        **state,
        "phase": session.phase.value,
        "result": result,
        "next_node": "persist",
    }
