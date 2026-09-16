"""Platform and court payment verification nodes."""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Callable, Dict, Literal, Optional, Tuple
from urllib.parse import urlencode

from pydantic import BaseModel, Field

from app.agents.conversation.orchestration.context import FilingOrchestratorContext
from app.agents.utils.json_utils import parse_llm_json
from app.core.prompts.context import format_llm_prompt
from app.agents.conversation.orchestration.helpers import (
    attach_selection_options_to_result,
    classify_document_offer_reply,
    persist_system_state,
    result_from_session,
)
from app.agents.conversation.orchestration.session_manager import FilingSessionManager
from app.agents.conversation.orchestration.state import FilingGraphState
from app.api.schemas.filing_events import FilingPhase
from app.config.settings import settings
from app.services.case_type_cost_repository import CaseTypeCostRepository
from app.services.case_type_cost_service import (
    CaseTypeCostResolutionError,
    CaseTypeCostService,
)
from app.services.uslegalpro_payment_service import (
    PAYMENT_ACCOUNT_NOT_FOUND_MESSAGE,
    PAYMENT_ID_PROMPT,
    PaymentApiNotConfiguredError,
    USLegalProPaymentService,
    credit_card_items,
    extract_payment_id,
    format_braintree_cards_message,
    format_court_payment_account,
    format_court_payment_message,
    match_court_payment_account,
)

logger = logging.getLogger(__name__)

NodeFn = Callable[[FilingGraphState], Any]

_CREATE_CARD_PATH = "/payment/create_card"
_PAYMENT_SETUP_PENDING_KEY = "platform_payment_setup_pending"
_PAYMENT_SETUP_CUSTOMER_KEY = "platform_payment_setup_customer_id"
_AWAITING_SETUP_PAYMENT_ID_KEY = "platform_payment_awaiting_setup_id"
_AWAITING_CUSTOMER_NAME_KEY = "platform_payment_awaiting_customer_name"
_CUSTOMER_NAME_KEY = "platform_payment_customer_name"
_PENDING_VERIFICATION_KEY = "platform_payment_pending_verification"
SETUP_PAYMENT_ID_PROMPT = (
    "No Braintree account exists for that payment ID yet. "
    "Please enter the payment ID you want to use for your account. "
    "You choose this ID — the system does not create one for you. "
    "Use only letters, numbers, hyphens, and underscores "
    "(for example: COM-ULP-DEMO-2)."
)
CUSTOMER_NAME_PROMPT = (
    "Please enter your full name as it appears on your payment card."
)

_PAYMENT_SETUP_REPLY_PROMPT = """You classify the user's latest message during Braintree payment setup.

Context:
- The assistant sent the user to a hosted page to add a payment method.
- The user was told to return and enter their payment ID (Braintree customer ID).
- Payment IDs contain only letters, numbers, hyphens, and underscores (example: COM-ULP-DEMO-2).

Classify the message:
- returned_from_payment_setup: true when the user indicates they finished or returned from the card setup page but did NOT provide a payment ID.
- payment_id: extract the Braintree customer / payment ID when the user provides one; otherwise null.
- intent:
  - setup_complete — returned from setup without a payment ID
  - provide_payment_id — message includes a payment ID to verify
  - unclear — cannot tell; ask them to enter the payment ID

User message:
{user_message}
"""

_CUSTOMER_NAME_PROMPT = """Extract the cardholder's full name from the user message.

The assistant asked for the full name as it appears on the payment card.

Return full_name when the user provided a plausible person name (typically first and last).
Return intent unclear when the message does not contain a name.

User message:
{user_message}
"""

_PAYMENT_ID_EXTRACT_PROMPT = """Extract the Braintree payment / customer ID from the user message.

The assistant asked the user to choose a payment ID for their account.
Valid IDs contain only letters, numbers, hyphens, and underscores (example: COM-ULP-DEMO-2).

Return payment_id when the user provided a valid-looking ID.
Return intent unclear when no payment ID is present.

User message:
{user_message}
"""


class PaymentSetupReplyOutput(BaseModel):
    intent: Literal["setup_complete", "provide_payment_id", "unclear"] = "unclear"
    returned_from_payment_setup: bool = False
    payment_id: Optional[str] = Field(default=None)


class CustomerNameOutput(BaseModel):
    intent: Literal["provide_name", "unclear"] = "unclear"
    full_name: Optional[str] = Field(default=None)


class PaymentIdOutput(BaseModel):
    intent: Literal["provide_payment_id", "unclear"] = "unclear"
    payment_id: Optional[str] = Field(default=None)


_PAYMENT_AUTHORIZATION_REPLY_PROMPT = """You classify the user's latest message during payment authorization confirmation.

Context:
- The assistant asked the user to authorize a filing charge on their selected Braintree card.
- The user should reply to confirm authorization or decline and choose another account.

Classify the message:
- intent confirm — user agrees to authorize the charge (yes, proceed, authorize, go ahead, etc.)
- intent decline — user refuses or wants to choose another account (no, cancel, different card, etc.)
- intent unclear — cannot tell; ask them to reply yes or no

User message:
{user_message}
"""


class PaymentAuthorizationReplyOutput(BaseModel):
    intent: Literal["confirm", "decline", "unclear"] = "unclear"


_BRAINTREE_CARD_SELECTION_PROMPT = """You identify which Braintree payment card the user selected.

Available cards (JSON):
{cards_json}

The assistant asked the user to choose one Braintree card for filing authorization.

Return:
- card_id: the card ``id`` from the list when the user clearly selected one; otherwise null
- intent: selected when a card is identified; unclear when you cannot tell

Accept list numbers (e.g. "1"), card ids, last4 digits, cardholder names, or natural language
such as "use this card", "7777", "select it", or "use the same" when only one card is listed.
If exactly one card is available and the user wants to proceed with it, return that card's id.
"""


class BraintreeCardSelectionOutput(BaseModel):
    intent: Literal["selected", "unclear"] = "unclear"
    card_id: Optional[str] = Field(default=None)


_SINGLE_CARD_AFFIRMATIVES = {
    "yes",
    "y",
    "ok",
    "okay",
    "use it",
    "use this",
    "select it",
    "use the same",
    "same",
    "this one",
    "that one",
    "go ahead",
    "proceed",
}


def _single_card_selection(message: str, cards: list[Dict[str, Any]]) -> Optional[BraintreeCardSelectionOutput]:
    if len(cards) != 1:
        return None
    if str(message or "").strip().lower() not in _SINGLE_CARD_AFFIRMATIVES:
        return None
    card_id = str(cards[0].get("id") or "").strip()
    if not card_id:
        return None
    return BraintreeCardSelectionOutput(intent="selected", card_id=card_id)


def _payment_return_base() -> str:
    explicit = (
        os.environ.get("USLEGALPRO_PAYMENT_RETURN_URL", "").strip()
        or os.environ.get("CHATBOT_APP_BASE_URL", "").strip()
    )
    if explicit:
        return explicit.rstrip("/")
    payment_base = str(settings.USLEGALPRO_PAYMENT_API_BASE_URL or "").strip().rstrip("/")
    return payment_base or "https://example.com"


def _payment_flow_urls(conversation_id: str) -> Tuple[str, str]:
    base = _payment_return_base()
    callback = f"{base}?{urlencode({'payment_setup': 'done', 'conversation_id': conversation_id})}"
    referer = f"{base}?{urlencode({'payment_setup': 'cancel', 'conversation_id': conversation_id})}"
    return callback, referer


async def _user_email_from_db(
    ctx: FilingOrchestratorContext,
    session,
) -> str:
    user_id = str(session.user_id or "").strip()
    user_repo = ctx.user_repo
    if not user_repo or not user_id:
        return ""

    try:
        row = await user_repo.get_by_user_id(user_id)
        return str((row or {}).get("email") or "").strip()
    except Exception as exc:  # noqa: BLE001
        logger.warning("User email lookup failed for user_id=%s: %s", user_id, exc)
        return ""


def _resolved_customer_name(session) -> str:
    return str(session.selections.get(_CUSTOMER_NAME_KEY) or "").strip()


async def _customer_profile_from_user(
    ctx: FilingOrchestratorContext,
    session,
    payment_id: str,
) -> Tuple[str, str]:
    name = _resolved_customer_name(session)
    email = await _user_email_from_db(ctx, session)
    if not email:
        email = f"{payment_id}@uslegalpro.local"
    return name, email


def _build_create_card_url(
    *,
    payment_domain: str,
    customer_id: str,
    callback: str,
    referer: str,
) -> str:
    domain = payment_domain.rstrip("/")
    query = urlencode(
        {
            "callback": callback,
            "referer": referer,
            "customer_id": customer_id,
        }
    )
    return f"{domain}{_CREATE_CARD_PATH}?{query}"


async def _ensure_braintree_customer(
    ctx: FilingOrchestratorContext,
    service: USLegalProPaymentService,
    payment_id: str,
    session,
) -> None:
    name, email = await _customer_profile_from_user(ctx, session, payment_id)
    if not name:
        raise ValueError("Customer name is required before creating a Braintree customer.")
    await service.create_customer(
        customer_id=payment_id,
        name=name,
        email=email,
    )


async def _classify_payment_id_reply(
    ctx: FilingOrchestratorContext,
    user_message: str,
) -> PaymentIdOutput:
    message = str(user_message or "").strip()
    if not message:
        return PaymentIdOutput(intent="unclear")

    payment_id = extract_payment_id(message)
    bedrock = ctx.bedrock
    if bedrock is None:
        if payment_id:
            return PaymentIdOutput(intent="provide_payment_id", payment_id=payment_id)
        return PaymentIdOutput(intent="unclear")

    prompt = format_llm_prompt(
        _PAYMENT_ID_EXTRACT_PROMPT,
        user_message=message[:2000],
    )
    try:
        parsed = await bedrock.invoke_structured_prompt(prompt, PaymentIdOutput)
        if parsed.payment_id:
            cleaned = extract_payment_id(str(parsed.payment_id))
            if cleaned:
                return PaymentIdOutput(intent="provide_payment_id", payment_id=cleaned)
        if parsed.intent == "provide_payment_id" and payment_id:
            return PaymentIdOutput(intent="provide_payment_id", payment_id=payment_id)
        return parsed
    except Exception as exc:  # noqa: BLE001
        logger.warning("Payment ID extraction failed: %s", exc)

    try:
        body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 256,
            "temperature": 0,
            "messages": [{"role": "user", "content": prompt}],
        }
        raw = await bedrock.invoke_prompt_with_timeout(body)
        parsed_json = parse_llm_json(raw)
        parsed = PaymentIdOutput.model_validate(parsed_json)
        if parsed.payment_id:
            cleaned = extract_payment_id(str(parsed.payment_id))
            if cleaned:
                return PaymentIdOutput(intent="provide_payment_id", payment_id=cleaned)
        if parsed.intent == "provide_payment_id" and payment_id:
            return PaymentIdOutput(intent="provide_payment_id", payment_id=payment_id)
        return parsed
    except Exception as exc:  # noqa: BLE001
        logger.warning("Payment ID extraction fallback failed: %s", exc)
        if payment_id:
            return PaymentIdOutput(intent="provide_payment_id", payment_id=payment_id)
        return PaymentIdOutput(intent="unclear")


async def _classify_customer_name_reply(
    ctx: FilingOrchestratorContext,
    user_message: str,
) -> CustomerNameOutput:
    message = str(user_message or "").strip()
    if not message:
        return CustomerNameOutput(intent="unclear")

    bedrock = ctx.bedrock
    if bedrock is None:
        if len(message.split()) >= 2:
            return CustomerNameOutput(intent="provide_name", full_name=message)
        return CustomerNameOutput(intent="unclear")

    prompt = format_llm_prompt(
        _CUSTOMER_NAME_PROMPT,
        user_message=message[:2000],
    )
    try:
        return await bedrock.invoke_structured_prompt(prompt, CustomerNameOutput)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Customer name extraction failed: %s", exc)

    try:
        body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 256,
            "temperature": 0,
            "messages": [{"role": "user", "content": prompt}],
        }
        raw = await bedrock.invoke_prompt_with_timeout(body)
        parsed = parse_llm_json(raw)
        return CustomerNameOutput.model_validate(parsed)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Customer name extraction fallback failed: %s", exc)
        if len(message.split()) >= 2:
            return CustomerNameOutput(intent="provide_name", full_name=message)
        return CustomerNameOutput(intent="unclear")


def _cost_service(ctx: FilingOrchestratorContext) -> CaseTypeCostService:
    rds = getattr(ctx.filing_repo, "rds", None)
    if rds is None and ctx.conversation_repo is not None:
        rds = getattr(ctx.conversation_repo, "rds", None)
    return CaseTypeCostService(
        repo=CaseTypeCostRepository(rds=rds),
        bedrock=ctx.bedrock,
    )


def _card_by_id(cards: list[Dict[str, Any]], card_id: str) -> Optional[Dict[str, Any]]:
    needle = str(card_id or "").strip().lower()
    if not needle:
        return None
    for card in cards:
        if str(card.get("id") or "").strip().lower() == needle:
            return card
    return None


async def _classify_braintree_card_selection(
    ctx: FilingOrchestratorContext,
    user_message: str,
    cards: list[Dict[str, Any]],
) -> BraintreeCardSelectionOutput:
    message = str(user_message or "").strip()
    if not message or not cards:
        return BraintreeCardSelectionOutput(intent="unclear")

    matched = match_court_payment_account(cards, message)
    if matched:
        return BraintreeCardSelectionOutput(
            intent="selected",
            card_id=str(matched.get("id") or "").strip() or None,
        )

    single_card = _single_card_selection(message, cards)
    if single_card:
        return single_card

    bedrock = ctx.bedrock
    if bedrock is None:
        return BraintreeCardSelectionOutput(intent="unclear")

    cards_json = json.dumps(
        [
            {
                "id": card.get("id"),
                "name": card.get("name"),
                "last4": card.get("last4_digit"),
                "card_type": card.get("card_type"),
                "label": card.get("label"),
            }
            for card in cards
        ],
        default=str,
        indent=2,
    )
    prompt = format_llm_prompt(
        _BRAINTREE_CARD_SELECTION_PROMPT,
        cards_json=cards_json,
        user_message=message[:2000],
    )
    try:
        parsed = await bedrock.invoke_structured_prompt(prompt, BraintreeCardSelectionOutput)
        if parsed.card_id:
            cleaned = str(parsed.card_id).strip()
            if _card_by_id(cards, cleaned):
                return BraintreeCardSelectionOutput(intent="selected", card_id=cleaned)
        return parsed
    except Exception as exc:  # noqa: BLE001
        logger.warning("Braintree card selection classification failed: %s", exc)

    try:
        body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 256,
            "temperature": 0,
            "messages": [{"role": "user", "content": prompt}],
        }
        raw = await bedrock.invoke_prompt_with_timeout(body)
        parsed_json = parse_llm_json(raw)
        parsed = BraintreeCardSelectionOutput.model_validate(parsed_json)
        if parsed.card_id and _card_by_id(cards, str(parsed.card_id)):
            return BraintreeCardSelectionOutput(
                intent="selected",
                card_id=str(parsed.card_id).strip(),
            )
        return parsed
    except Exception as exc:  # noqa: BLE001
        logger.warning("Braintree card selection fallback failed: %s", exc)

    single_card = _single_card_selection(message, cards)
    if single_card:
        return single_card
    return BraintreeCardSelectionOutput(intent="unclear")


async def _classify_payment_authorization_reply(
    ctx: FilingOrchestratorContext,
    user_message: str,
) -> PaymentAuthorizationReplyOutput:
    message = str(user_message or "").strip()
    if not message:
        return PaymentAuthorizationReplyOutput(intent="unclear")

    bedrock = ctx.bedrock
    if bedrock is None:
        lowered = message.lower()
        if lowered in {"yes", "y", "confirm", "authorize", "proceed", "ok", "okay"}:
            return PaymentAuthorizationReplyOutput(intent="confirm")
        if lowered in {"no", "n", "cancel", "stop"}:
            return PaymentAuthorizationReplyOutput(intent="decline")
        return PaymentAuthorizationReplyOutput(intent="unclear")

    prompt = format_llm_prompt(
        _PAYMENT_AUTHORIZATION_REPLY_PROMPT,
        user_message=message[:2000],
    )
    try:
        return await bedrock.invoke_structured_prompt(prompt, PaymentAuthorizationReplyOutput)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Payment authorization reply classification failed: %s", exc)

    try:
        body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 256,
            "temperature": 0,
            "messages": [{"role": "user", "content": prompt}],
        }
        raw = await bedrock.invoke_prompt_with_timeout(body)
        parsed = parse_llm_json(raw)
        return PaymentAuthorizationReplyOutput.model_validate(parsed)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Payment authorization reply fallback failed: %s", exc)
        return PaymentAuthorizationReplyOutput(intent="unclear")


async def _classify_payment_setup_reply(
    ctx: FilingOrchestratorContext,
    user_message: str,
) -> PaymentSetupReplyOutput:
    message = str(user_message or "").strip()
    if not message:
        return PaymentSetupReplyOutput(intent="unclear")

    bedrock = ctx.bedrock
    if bedrock is None:
        payment_id = extract_payment_id(message)
        if payment_id:
            return PaymentSetupReplyOutput(
                intent="provide_payment_id",
                payment_id=payment_id,
            )
        return PaymentSetupReplyOutput(intent="unclear")

    prompt = format_llm_prompt(
        _PAYMENT_SETUP_REPLY_PROMPT,
        user_message=message[:2000],
    )
    try:
        return await bedrock.invoke_structured_prompt(prompt, PaymentSetupReplyOutput)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Payment setup reply classification failed: %s", exc)

    try:
        body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 256,
            "temperature": 0,
            "messages": [{"role": "user", "content": prompt}],
        }
        raw = await bedrock.invoke_prompt_with_timeout(body)
        parsed = parse_llm_json(raw)
        return PaymentSetupReplyOutput.model_validate(parsed)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Payment setup reply fallback failed: %s", exc)
        payment_id = extract_payment_id(message)
        if payment_id:
            return PaymentSetupReplyOutput(
                intent="provide_payment_id",
                payment_id=payment_id,
            )
        return PaymentSetupReplyOutput(intent="unclear")


async def _prompt_setup_payment_id(
    ctx,
    state,
    session,
    verification: Dict[str, Any],
):
    session.selections[_AWAITING_SETUP_PAYMENT_ID_KEY] = True
    session.selections[_PENDING_VERIFICATION_KEY] = verification
    session.selections.pop(_PAYMENT_SETUP_CUSTOMER_KEY, None)
    session.selections["platform_payment_customer_id"] = None
    session.selections["platform_payment_status"] = verification.get("status")
    session.selections["platform_payment_verified"] = False

    result = result_from_session(
        session,
        SETUP_PAYMENT_ID_PROMPT,
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


async def _prompt_customer_name(
    ctx,
    state,
    session,
    payment_id: str,
    verification: Dict[str, Any],
):
    session.selections[_AWAITING_CUSTOMER_NAME_KEY] = True
    session.selections[_PAYMENT_SETUP_CUSTOMER_KEY] = payment_id
    session.selections[_PENDING_VERIFICATION_KEY] = verification
    session.selections["platform_payment_id"] = payment_id
    session.selections["platform_payment_customer_id"] = None
    session.selections["platform_payment_status"] = verification.get("status")
    session.selections["platform_payment_verified"] = False

    message = (
        f"Your payment ID will be registered as {payment_id}. "
        + CUSTOMER_NAME_PROMPT
    )
    result = result_from_session(
        session,
        message,
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


async def _begin_payment_setup(
    ctx,
    service: USLegalProPaymentService,
    state,
    session,
    verification: Dict[str, Any],
):
    session.selections[_PENDING_VERIFICATION_KEY] = verification
    setup_payment_id = str(session.selections.get(_PAYMENT_SETUP_CUSTOMER_KEY) or "").strip()
    if not setup_payment_id:
        return await _prompt_setup_payment_id(ctx, state, session, verification)

    name = _resolved_customer_name(session)
    if not name:
        return await _prompt_customer_name(
            ctx, state, session, setup_payment_id, verification
        )
    return await _redirect_to_payment_setup(
        ctx,
        service,
        state,
        session,
        setup_payment_id,
        verification,
    )


async def _redirect_to_payment_setup(
    ctx,
    service: USLegalProPaymentService,
    state,
    session,
    payment_id: str,
    verification: Dict[str, Any],
    *,
    create_customer: bool = True,
):
    payment_domain = str(settings.USLEGALPRO_PAYMENT_API_BASE_URL or "").strip()
    if not payment_domain:
        result = result_from_session(
            session,
            (
                "This payment ID was not found, but the payment service is not configured. "
                "Set USLEGALPRO_PAYMENT_API_BASE_URL to continue."
            ),
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

    if create_customer:
        try:
            await _ensure_braintree_customer(ctx, service, payment_id, session)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Braintree create_customer failed for payment_id=%s: %s",
                payment_id,
                exc,
            )

    callback, referer = _payment_flow_urls(session.conversation_id)
    create_card_url = _build_create_card_url(
        payment_domain=payment_domain,
        customer_id=payment_id,
        callback=callback,
        referer=referer,
    )
    session.selections[_PAYMENT_SETUP_PENDING_KEY] = True
    session.selections[_PAYMENT_SETUP_CUSTOMER_KEY] = payment_id
    session.selections["platform_payment_id"] = payment_id
    session.selections["platform_payment_customer_id"] = None
    session.selections["platform_payment_status"] = verification.get("status")
    session.selections["platform_payment_verified"] = False

    message = (
        f"{PAYMENT_ACCOUNT_NOT_FOUND_MESSAGE}\n"
        "Please add a payment method using this link:\n"
        f"{create_card_url}\n\n"
        "After you finish, return here and enter your payment ID to continue."
    )
    result = result_from_session(
        session,
        message,
        event_kind="payment.platform",
        metadata={
            "platform_payment": verification,
            "redirect_url": create_card_url,
            "payment_setup": {
                "customer_id": payment_id,
                "create_card_url": create_card_url,
                "callback": callback,
                "referer": referer,
                "pending": True,
            },
        },
    )
    await persist_system_state(ctx.conversation_repo, session)
    return {
        **state,
        "phase": session.phase.value,
        "result": result,
        "next_node": "persist",
    }


def _with_account_options(result, accounts):
    return attach_selection_options_to_result(
        result,
        FilingPhase.VERIFYING_COURT_PAYMENT,
        accounts,
    )


def _with_braintree_card_options(result, cards):
    return attach_selection_options_to_result(
        result,
        FilingPhase.SELECTING_BRAINTREE_CARD,
        cards,
    )


def _selected_braintree_card(session) -> Optional[Dict[str, Any]]:
    card_id = str(session.selections.get("braintree_payment_account_id") or "").strip()
    if not card_id:
        return None
    for card in session.selections.get("platform_payment_cards") or []:
        if str(card.get("id") or "").strip() == card_id:
            return card
    return {"id": card_id}


def _format_braintree_card(card: Dict[str, Any]) -> Dict[str, Any]:
    return format_court_payment_account(
        {
            "id": card.get("id"),
            "name": card.get("name"),
            "card_type": card.get("card_type"),
            "last4_digit": card.get("last4_digit") or card.get("last4"),
            "expire_month": card.get("expire_month"),
            "expire_year": card.get("expire_year"),
        }
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
        if session.phase == FilingPhase.CONFIRMING_PAYMENT_AUTHORIZATION:
            return await _handle_payment_authorization_confirm(
                ctx, service, state, session, user_message
            )
        if session.phase == FilingPhase.SELECTING_BRAINTREE_CARD:
            return await _handle_braintree_card_selection(
                ctx, service, state, session, user_message
            )
        if session.phase == FilingPhase.VERIFYING_COURT_PAYMENT:
            return await _handle_court_payment(
                ctx, service, state, session, user_message
            )
        return await _handle_platform_payment(
            ctx, service, state, session, user_message
        )

    return {"verify_payment": verify_payment_node}


def _clear_payment_setup_pending(session) -> None:
    session.selections.pop(_PAYMENT_SETUP_PENDING_KEY, None)
    session.selections.pop(_PAYMENT_SETUP_CUSTOMER_KEY, None)
    session.selections.pop(_PENDING_VERIFICATION_KEY, None)


def _clear_awaiting_setup_payment_id(session) -> None:
    session.selections.pop(_AWAITING_SETUP_PAYMENT_ID_KEY, None)


def _clear_awaiting_customer_name(session) -> None:
    session.selections.pop(_AWAITING_CUSTOMER_NAME_KEY, None)


async def _prompt_platform_payment_id(ctx, state, session):
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


async def _prompt_setup_payment_id_again(ctx, state, session):
    result = result_from_session(
        session,
        SETUP_PAYMENT_ID_PROMPT,
        event_kind="payment.platform",
    )
    await persist_system_state(ctx.conversation_repo, session)
    return {
        **state,
        "phase": session.phase.value,
        "result": result,
        "next_node": "persist",
    }


async def _prompt_customer_name_again(ctx, state, session):
    payment_id = str(session.selections.get(_PAYMENT_SETUP_CUSTOMER_KEY) or "").strip()
    message = CUSTOMER_NAME_PROMPT
    if payment_id:
        message = (
            f"Your payment ID will be registered as {payment_id}. "
            + CUSTOMER_NAME_PROMPT
        )
    result = result_from_session(
        session,
        message,
        event_kind="payment.platform",
    )
    await persist_system_state(ctx.conversation_repo, session)
    return {
        **state,
        "phase": session.phase.value,
        "result": result,
        "next_node": "persist",
    }


async def _handle_awaiting_setup_payment_id(ctx, service, state, session, user_message):
    id_reply = await _classify_payment_id_reply(ctx, user_message)
    payment_id = str(id_reply.payment_id or "").strip()
    if id_reply.intent != "provide_payment_id" or not payment_id:
        return await _prompt_setup_payment_id_again(ctx, state, session)

    verification = session.selections.get(_PENDING_VERIFICATION_KEY) or {
        "verified": False,
        "status": "not_found",
    }
    session.selections[_PAYMENT_SETUP_CUSTOMER_KEY] = payment_id
    session.selections["platform_payment_id"] = payment_id
    _clear_awaiting_setup_payment_id(session)
    return await _begin_payment_setup(ctx, service, state, session, verification)


async def _handle_awaiting_customer_name(ctx, service, state, session, user_message):
    name_reply = await _classify_customer_name_reply(ctx, user_message)
    full_name = str(name_reply.full_name or "").strip()
    if name_reply.intent != "provide_name" or not full_name:
        return await _prompt_customer_name_again(ctx, state, session)

    session.selections[_CUSTOMER_NAME_KEY] = full_name
    payment_id = str(session.selections.get(_PAYMENT_SETUP_CUSTOMER_KEY) or "").strip()
    verification = session.selections.get(_PENDING_VERIFICATION_KEY) or {
        "verified": False,
        "status": "not_found",
    }
    _clear_awaiting_customer_name(session)
    if not payment_id:
        return await _prompt_platform_payment_id(ctx, state, session)
    return await _redirect_to_payment_setup(
        ctx,
        service,
        state,
        session,
        payment_id,
        verification,
    )


async def _handle_platform_payment(ctx, service, state, session, user_message):
    if session.selections.get(_AWAITING_SETUP_PAYMENT_ID_KEY):
        return await _handle_awaiting_setup_payment_id(
            ctx, service, state, session, user_message
        )
    if session.selections.get(_AWAITING_CUSTOMER_NAME_KEY):
        return await _handle_awaiting_customer_name(
            ctx, service, state, session, user_message
        )

    pending_setup = bool(session.selections.get(_PAYMENT_SETUP_PENDING_KEY))
    payment_id = ""

    if pending_setup:
        setup_reply = await _classify_payment_setup_reply(ctx, user_message)
        if setup_reply.intent == "setup_complete" or (
            setup_reply.returned_from_payment_setup and not setup_reply.payment_id
        ):
            _clear_payment_setup_pending(session)
            return await _prompt_platform_payment_id(ctx, state, session)
        if setup_reply.payment_id:
            payment_id = str(setup_reply.payment_id).strip()
        else:
            payment_id = extract_payment_id(user_message)
        if setup_reply.intent == "unclear" and not payment_id:
            _clear_payment_setup_pending(session)
            return await _prompt_platform_payment_id(ctx, state, session)
        _clear_payment_setup_pending(session)
    else:
        payment_id = extract_payment_id(user_message)

    if not payment_id:
        return await _prompt_platform_payment_id(ctx, state, session)

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
        if verification.get("status") == "not_found":
            session.selections.pop(_PAYMENT_SETUP_CUSTOMER_KEY, None)
            return await _begin_payment_setup(
                ctx,
                service,
                state,
                session,
                verification,
            )
        if verification.get("status") == "ended":
            return await _redirect_to_payment_setup(
                ctx,
                service,
                state,
                session,
                payment_id,
                verification,
                create_customer=False,
            )
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
    return await _begin_braintree_authorization_flow(ctx, service, state, session)


async def _begin_braintree_authorization_flow(ctx, service, state, session):
    card_id = str(session.selections.get("braintree_payment_account_id") or "").strip()
    if card_id:
        return await _show_payment_authorization_confirm(ctx, service, state, session)
    return await _load_braintree_cards(ctx, service, state, session)


async def _load_braintree_cards(ctx, service, state, session):
    customer_id = str(session.selections.get("platform_payment_customer_id") or "").strip()
    if not customer_id:
        payment_id = str(session.selections.get("platform_payment_id") or "").strip()
        customer_id = payment_id
    if not customer_id:
        result = result_from_session(
            session,
            "I could not find your Braintree customer ID. Please verify your payment ID first.",
            event_kind="payment.platform",
        )
        await persist_system_state(ctx.conversation_repo, session)
        return {
            **state,
            "phase": session.phase.value,
            "result": result,
            "next_node": "persist",
        }

    await ctx.notify("selecting_braintree_card")
    try:
        response = await service.get_credit_cards(customer_id)
        cards = [
            _format_braintree_card(item)
            for item in credit_card_items(response)
        ]
    except Exception as exc:  # noqa: BLE001
        logger.exception("Braintree card lookup failed")
        result = result_from_session(
            session,
            f"I could not load your Braintree payment cards. {exc}",
            event_kind="payment.platform",
        )
        await persist_system_state(ctx.conversation_repo, session)
        return {
            **state,
            "phase": session.phase.value,
            "result": result,
            "next_node": "persist",
        }

    if not cards:
        result = result_from_session(
            session,
            "No Braintree payment cards were found. Please add a card and try again.",
            event_kind="payment.platform",
        )
        await persist_system_state(ctx.conversation_repo, session)
        return {
            **state,
            "phase": session.phase.value,
            "result": result,
            "next_node": "persist",
        }

    session.selections["platform_payment_cards"] = cards
    session.phase = FilingPhase.SELECTING_BRAINTREE_CARD
    message = format_braintree_cards_message(cards)
    result = result_from_session(
        session,
        message,
        event_kind="payment.platform",
        metadata={"platform_payment_cards": cards},
    )
    result = _with_braintree_card_options(result, cards)
    await persist_system_state(ctx.conversation_repo, session)
    return {
        **state,
        "phase": session.phase.value,
        "result": result,
        "next_node": "persist",
    }


async def _handle_braintree_card_selection(ctx, service, state, session, user_message):
    cards = list(session.selections.get("platform_payment_cards") or [])
    if not cards:
        return await _load_braintree_cards(ctx, service, state, session)

    existing_id = str(session.selections.get("braintree_payment_account_id") or "").strip()
    selection = await _classify_braintree_card_selection(ctx, user_message, cards)
    chosen = _card_by_id(cards, str(selection.card_id or "")) if selection.intent == "selected" else None

    if not chosen and existing_id:
        return await _show_payment_authorization_confirm(ctx, service, state, session)

    if not chosen:
        result = result_from_session(
            session,
            "Please choose one of the listed Braintree cards "
            "(reply with the list number, e.g. 1, 2, 3).\n\n"
            + format_braintree_cards_message(cards),
            event_kind="payment.platform",
            metadata={"platform_payment_cards": cards},
        )
        result = _with_braintree_card_options(result, cards)
        await persist_system_state(ctx.conversation_repo, session)
        return {
            **state,
            "phase": session.phase.value,
            "result": result,
            "next_node": "persist",
        }

    session.selections["braintree_payment_account_id"] = chosen.get("id")
    return await _show_payment_authorization_confirm(ctx, service, state, session)


async def _show_payment_authorization_confirm(ctx, service, state, session):
    card = _selected_braintree_card(session) or {}
    card_id = str(card.get("id") or session.selections.get("braintree_payment_account_id") or "").strip()
    if not card_id:
        return await _load_braintree_cards(ctx, service, state, session)

    cost_service = _cost_service(ctx)
    try:
        cost_match = await cost_service.resolve_amount(session.selections)
    except CaseTypeCostResolutionError as exc:
        session.phase = FilingPhase.CONFIRMING_PAYMENT_AUTHORIZATION
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

    amount = str(cost_match.get("amount") or "").strip()
    currency = str(cost_match.get("currency") or "USD").strip().upper()
    session.selections["filing_authorize_amount"] = amount
    session.selections["filing_cost_match"] = cost_match
    session.phase = FilingPhase.CONFIRMING_PAYMENT_AUTHORIZATION
    await ctx.notify("confirming_payment_authorization")

    last4 = str(card.get("last4_digit") or "").strip()
    display_amount = f"${amount}" if currency == "USD" else f"{amount} {currency}"
    card_hint = f" ending **{last4}**" if last4 else ""
    message = (
        f"Authorize **{display_amount}** on the card{card_hint}? "
        "Reply **yes** to confirm or **no** to choose another account."
    )
    result = result_from_session(
        session,
        message,
        event_kind="payment.authorize",
        metadata={
            "filing_authorize_amount": amount,
            "filing_cost_match": cost_match,
            "braintree_payment_account_id": card_id,
        },
    )
    await persist_system_state(ctx.conversation_repo, session)
    return {
        **state,
        "phase": session.phase.value,
        "result": result,
        "next_node": "persist",
    }


async def _handle_payment_authorization_confirm(ctx, service, state, session, user_message):
    if not str(session.selections.get("filing_authorize_amount") or "").strip():
        return await _show_payment_authorization_confirm(ctx, service, state, session)

    auth_reply = await _classify_payment_authorization_reply(ctx, user_message)
    intent = auth_reply.intent

    if intent == "decline":
        session.selections.pop("braintree_payment_account_id", None)
        session.selections.pop("filing_authorize_amount", None)
        session.selections.pop("filing_cost_match", None)
        session.phase = FilingPhase.VERIFYING_COURT_PAYMENT
        accounts = list(session.selections.get("court_payment_accounts") or [])
        result = result_from_session(
            session,
            "Payment authorization was cancelled. Choose a court payment account again.\n\n"
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

    if intent != "confirm":
        card = _selected_braintree_card(session) or {}
        amount = str(session.selections.get("filing_authorize_amount") or "").strip()
        last4 = str(card.get("last4_digit") or "").strip()
        card_hint = f" ending **{last4}**" if last4 else ""
        message = (
            f"Authorize **${amount}** on the card{card_hint}? "
            "Reply **yes** to confirm or **no** to choose another account."
        )
        result = result_from_session(
            session,
            message,
            event_kind="payment.authorize",
            metadata={
                "filing_authorize_amount": amount,
                "filing_cost_match": session.selections.get("filing_cost_match"),
                "braintree_payment_account_id": session.selections.get(
                    "braintree_payment_account_id"
                ),
            },
        )
        await persist_system_state(ctx.conversation_repo, session)
        return {
            **state,
            "phase": session.phase.value,
            "result": result,
            "next_node": "persist",
        }

    card_id = str(session.selections.get("braintree_payment_account_id") or "").strip()
    amount = str(session.selections.get("filing_authorize_amount") or "").strip()
    if not card_id or not amount:
        return await _show_payment_authorization_confirm(ctx, service, state, session)

    email = await _user_email_from_db(ctx, session)
    await ctx.notify("authorizing_payment")
    try:
        response = await service.authorize_payment(
            payment_account_id=card_id,
            amount=amount,
            additional_info={
                "email": email,
                "source": "USLP-AI",
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Payment authorization failed")
        result = result_from_session(
            session,
            f"I could not authorize the payment. {exc}\n"
            "Reply **yes** to try again or **no** to choose another account.",
            event_kind="payment.authorize",
            metadata={
                "filing_authorize_amount": amount,
                "braintree_payment_account_id": card_id,
            },
        )
        await persist_system_state(ctx.conversation_repo, session)
        return {
            **state,
            "phase": session.phase.value,
            "result": result,
            "next_node": "persist",
        }

    session.selections["payment_authorization_response"] = response
    return await _show_efile_preview(ctx, state, session)


def _format_validation_error(exc) -> str:
    errors = getattr(exc, "errors", None)
    if not errors:
        return str(exc)
    bullet = "\n  - ".join(errors)
    return (
        "I cannot build a valid e-file request for this case. "
        "Please fix these before continuing:\n  - "
        + bullet
    )


async def _show_efile_preview(ctx, state, session):
    from app.services.efile_validator import EFilePayloadValidationError
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
    except EFilePayloadValidationError as exc:
        logger.warning("E-file payload validation failed: %s", exc.errors)
        result = result_from_session(
            session,
            _format_validation_error(exc),
            event_kind="payment.court",
            metadata={"efile_validation_errors": exc.errors},
        )
        await persist_system_state(ctx.conversation_repo, session)
        return {
            **state,
            "phase": session.phase.value,
            "result": result,
            "next_node": "persist",
        }
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
    from app.services.efile_validator import EFilePayloadValidationError

    try:
        submitted = await ctx.efile_service.submit(
            user_id=session.user_id,
            mode=session.mode.value,
            selections=session.selections,
            collected_answers=session.collected_answers,
            generated_documents=session.generated_documents,
            workflow_questions=session.workflow_questions,
        )
    except EFilePayloadValidationError as exc:
        logger.warning("E-file payload validation failed at submit: %s", exc.errors)
        session.selections["efile_submit_error"] = _format_validation_error(exc)
        session.selections["efile_validation_errors"] = exc.errors
        return None
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
