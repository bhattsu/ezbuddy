"""Braintree card selection and payment authorization flow."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.agents.conversation.orchestration.payment_nodes import (
    _resolve_braintree_card_choice,
    BraintreeCardSelectionOutput,
    PaymentAuthorizationReplyOutput,
    _classify_braintree_card_selection,
    _classify_payment_authorization_reply,
    _handle_braintree_card_selection,
    _handle_court_payment,
    _handle_payment_authorization_confirm,
)
from app.agents.conversation.orchestration.state import FilingSession
from app.agents.utils.db_options_format import build_selection_options_payload
from app.api.schemas.filing_events import FilingMode, FilingPhase
from app.services.case_type_cost_service import CaseTypeCostService
from app.services.uslegalpro_payment_service import format_braintree_cards_message


class _Repo:
    async def insert_system_message(self, *args, **kwargs):
        return None

    async def get_conversation(self, *args, **kwargs):
        return {"session_id": "sess-1"}


class _UserRepo:
    async def get_by_user_id(self, user_id):
        return {"email": "user@example.com"}


class _EfileService:
    def __init__(self) -> None:
        self.preview = {"data": {"reference_id": "DRAFT-2026-10034"}}

    async def build_submit_payload(self, **kwargs):
        return self.preview


class _PaymentService:
    def __init__(self) -> None:
        self.authorize_calls = 0
        self.cards = [
            {
                "id": "1m1xrwdt",
                "name": "JANE DOE",
                "last4": "7777",
                "expire_month": "05",
                "expire_year": "2030",
            }
        ]

    async def get_credit_cards(self, customer_id):
        assert customer_id == "COM-ULP-DEMO-1"
        return {"items": self.cards}

    async def authorize_payment(self, **kwargs):
        self.authorize_calls += 1
        assert kwargs["payment_account_id"] == "1m1xrwdt"
        assert kwargs["amount"] == "10.00"
        assert kwargs["additional_info"]["source"] == "USLP-AI"
        return {"message_code": 0, "item": {"status": "authorized"}}


class _FilingRepo:
    rds = object()


class _Ctx:
    def __init__(self) -> None:
        self.conversation_repo = _Repo()
        self.filing_repo = _FilingRepo()
        self.user_repo = _UserRepo()
        self.efile_service = _EfileService()
        self.bedrock = None
        self.notices: list[str] = []

    async def notify(self, process: str):
        self.notices.append(process)


def _session() -> FilingSession:
    session = FilingSession(conversation_id="conv-1", user_id="user-1")
    session.mode = FilingMode.FILING_NEW
    session.phase = FilingPhase.VERIFYING_COURT_PAYMENT
    session.selections = {
        "state_code": "tx",
        "case_type_name": "Divorce",
        "platform_payment_customer_id": "COM-ULP-DEMO-1",
        "court_payment_accounts": [
            {
                "id": "court-acct-1",
                "name": "Court Card",
                "label": "Court Card",
            }
        ],
    }
    return session


@pytest.fixture(autouse=True)
def _mock_cost_service(monkeypatch):
    async def _resolve_amount(self, selections):
        return {
            "matched_case_type": "Divorce",
            "amount": "10.00",
            "currency": "USD",
            "confidence": "high",
        }

    monkeypatch.setattr(CaseTypeCostService, "resolve_amount", _resolve_amount)


def test_braintree_selection_options_payload():
    cards = [
        {
            "id": "1m1xrwdt",
            "name": "JANE DOE",
            "code": "1m1xrwdt",
            "label": "JANE DOE — account ending 7777 (active)",
            "last4_digit": "7777",
        }
    ]
    payload = build_selection_options_payload("selecting_braintree_card", cards)
    assert payload is not None
    assert payload["type"] == "dropdown"
    assert payload["options"][0]["code"] == "1m1xrwdt"


def test_resolve_braintree_card_from_dropdown_label():
    cards = [
        {
            "id": "1m1xrwdt",
            "name": "JANE DOE",
            "code": "1m1xrwdt",
            "label": "JANE DOE — account ending 7777 (active)",
            "last4_digit": "7777",
        }
    ]
    chosen = _resolve_braintree_card_choice(
        cards, "JANE DOE — account ending 7777 (active)"
    )
    assert chosen is not None
    assert chosen["id"] == "1m1xrwdt"


def test_braintree_cards_message_prompts_dropdown():
    message = format_braintree_cards_message(
        [{"id": "1m1xrwdt", "name": "JANE DOE", "last4_digit": "7777", "is_expired": False}]
    )
    assert "dropdown" in message.lower()

    multi = format_braintree_cards_message(
        [
            {"id": "a", "name": "A", "last4_digit": "1111", "is_expired": False},
            {"id": "b", "name": "B", "last4_digit": "2222", "is_expired": False},
        ]
    )
    assert "dropdown" in multi.lower()


@pytest.mark.asyncio
async def test_single_card_use_it_selects_without_llm_match():
    cards = [
        {
            "id": "1m1xrwdt",
            "name": "JANE DOE",
            "last4_digit": "7777",
            "label": "JANE DOE — account ending 7777 (active)",
        }
    ]

    class _Bedrock:
        async def invoke_structured_prompt(self, prompt, model):
            return BraintreeCardSelectionOutput(intent="unclear")

    ctx = _Ctx()
    ctx.bedrock = _Bedrock()
    reply = await _classify_braintree_card_selection(ctx, "use it", cards)
    assert reply.intent == "selected"
    assert reply.card_id == "1m1xrwdt"


@pytest.mark.asyncio
async def test_braintree_card_selection_uses_llm_for_natural_language():
    cards = [
        {
            "id": "1m1xrwdt",
            "name": "JANE DOE",
            "last4_digit": "7777",
            "label": "JANE DOE — account ending 7777 (active)",
        }
    ]

    class _Bedrock:
        async def invoke_structured_prompt(self, prompt, model):
            assert "7777" in prompt
            return BraintreeCardSelectionOutput(intent="selected", card_id="1m1xrwdt")

    ctx = _Ctx()
    ctx.bedrock = _Bedrock()
    reply = await _classify_braintree_card_selection(ctx, "7777, use this card", cards)
    assert reply.intent == "selected"
    assert reply.card_id == "1m1xrwdt"


@pytest.mark.asyncio
async def test_payment_authorization_reply_uses_llm():
    class _Bedrock:
        async def invoke_structured_prompt(self, prompt, model):
            assert "authorize" in prompt.lower()
            return PaymentAuthorizationReplyOutput(intent="confirm")

    ctx = _Ctx()
    ctx.bedrock = _Bedrock()
    reply = await _classify_payment_authorization_reply(ctx, "please go ahead and authorize it")
    assert reply.intent == "confirm"


@pytest.mark.asyncio
async def test_court_payment_leads_to_braintree_card_selection():
    ctx = _Ctx()
    service = _PaymentService()
    session = _session()
    state = {"conversation_id": "conv-1"}

    output = await _handle_court_payment(
        ctx, service, state, session, "court-acct-1"
    )
    assert session.phase == FilingPhase.SELECTING_BRAINTREE_CARD
    assert session.selections["court_payment_account_id"] == "court-acct-1"
    assert "Braintree payment cards" in output["result"].assistant_message


@pytest.mark.asyncio
async def test_braintree_card_selection_then_authorize_then_efile_preview():
    ctx = _Ctx()
    service = _PaymentService()
    session = _session()
    state = {"conversation_id": "conv-1"}

    await _handle_court_payment(ctx, service, state, session, "court-acct-1")
    output = await _handle_braintree_card_selection(
        ctx, service, state, session, "1m1xrwdt"
    )
    assert session.phase == FilingPhase.CONFIRMING_PAYMENT_AUTHORIZATION
    assert session.selections["braintree_payment_account_id"] == "1m1xrwdt"
    assert "Authorize" in output["result"].assistant_message

    output = await _handle_payment_authorization_confirm(
        ctx, service, state, session, "yes"
    )
    assert service.authorize_calls == 1
    assert session.phase == FilingPhase.CONFIRMING_EFILE
    assert session.selections["payment_authorization_response"]["item"]["status"] == "authorized"
    assert "Review the e-file request below" in output["result"].assistant_message
