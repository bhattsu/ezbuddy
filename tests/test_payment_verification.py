"""Python-only platform and court payment verification."""

from __future__ import annotations

from datetime import date

import pytest

from app.services.uslegalpro_payment_service import (
    COURT_ACCOUNT_THANKS,
    USLegalProPaymentService,
    extract_payment_id,
    format_court_payment_account,
    format_court_payment_message,
    is_card_expired,
    match_court_payment_account,
    subscription_status,
)


def test_extract_payment_id():
    assert extract_payment_id("COM-ULP-DEMO-1") == "COM-ULP-DEMO-1"
    assert extract_payment_id('  "COM-ULP-DEMO-1"  ') == "COM-ULP-DEMO-1"
    assert extract_payment_id("please use COM-ULP-DEMO-1 thanks") == "COM-ULP-DEMO-1"
    assert extract_payment_id("") == ""


def test_card_expiry_uses_current_date():
    today = date(2026, 9, 6)
    assert is_card_expired("05", "2030", today=today) is False
    assert is_card_expired("8", "2026", today=today) is True
    assert is_card_expired("9", "2026", today=today) is False
    assert is_card_expired("12", "2025", today=today) is True


def test_subscription_status_from_cards():
    today = date(2026, 9, 6)
    assert (
        subscription_status(
            [{"expire_month": "05", "expire_year": "2030"}],
            today=today,
        )
        == "verified"
    )
    assert (
        subscription_status(
            [{"expire_month": "01", "expire_year": "2026"}],
            today=today,
        )
        == "ended"
    )
    assert subscription_status([], today=today) == "ended"


def test_format_and_match_court_accounts():
    accounts = [
        format_court_payment_account(
            {
                "id": "01eb044f-9e41-4966-93e2-31a5f8d9c01b",
                "name": "SAM",
                "type_code": "CC",
                "card_type": "MASTERCARD",
                "last4_digit": "5454",
                "expire_month": "12",
                "expire_year": "2026",
                "is_expired": "false",
            },
            today=date(2026, 9, 6),
        ),
        format_court_payment_account(
            {
                "id": "9c3e9ac1-0da5-42b4-96b0-b7da5bf5ad87",
                "name": "DD",
                "type_code": "WV",
                "is_active": "true",
            }
        ),
    ]
    message = format_court_payment_message(accounts)
    assert "id: 01eb044f-9e41-4966-93e2-31a5f8d9c01b" in message
    assert "name: SAM" in message
    assert "card type: MASTERCARD" in message
    assert "expired: no" in message
    assert match_court_payment_account(accounts, "2")["name"] == "DD"
    assert match_court_payment_account(accounts, "SAM")["id"].startswith("01eb")
    assert COURT_ACCOUNT_THANKS.startswith("Thank you")


@pytest.mark.asyncio
async def test_verify_platform_payment_found_and_current(monkeypatch):
    class _Client:
        def __init__(self, auth_token=None, client_token=None, base_url=None):
            self.auth_token = auth_token
            self.client_token = client_token
            self.base_url = base_url

        async def find_customer(self, customer_id):
            assert customer_id == "COM-ULP-DEMO-1"
            return {"message_code": 0, "item": {"id": "COM-ULP-DEMO-1"}}

        async def get_credit_cards(self, customer_id):
            assert customer_id == "COM-ULP-DEMO-1"
            return {
                "message_code": 0,
                "items": [
                    {
                        "last4": "7777",
                        "name": "JANE DOE",
                        "expire_month": "05",
                        "id": "1m1xrwdt",
                        "expire_year": "2030",
                    }
                ],
                "count": 1,
            }

    monkeypatch.setattr(
        "app.services.uslegalpro_payment_service.USLegalProApiClient",
        _Client,
    )
    result = await USLegalProPaymentService().verify_platform_payment("COM-ULP-DEMO-1")
    assert result["verified"] is True
    assert result["status"] == "verified"
    assert "verified" in result["message"].lower()


@pytest.mark.asyncio
async def test_verify_platform_payment_missing_customer(monkeypatch):
    class _Client:
        def __init__(self, auth_token=None, client_token=None, base_url=None):
            pass

        async def find_customer(self, customer_id):
            return {"message_code": 0, "item": {"id": None}}

        async def get_credit_cards(self, customer_id):
            raise AssertionError("cards should not be requested")

    monkeypatch.setattr(
        "app.services.uslegalpro_payment_service.USLegalProApiClient",
        _Client,
    )
    result = await USLegalProPaymentService().verify_platform_payment("missing")
    assert result["verified"] is False
    assert result["status"] == "not_found"
    assert "subscribe" in result["message"].lower()


@pytest.mark.asyncio
async def test_missing_payment_route_reports_configuration(monkeypatch):
    import httpx

    from app.adapters.uslegalpro.client import USLegalProApiError
    from app.services.uslegalpro_payment_service import PaymentApiNotConfiguredError

    class _Client:
        def __init__(self, auth_token=None, client_token=None, base_url=None):
            self.base_url = base_url or "https://api-stage.uslegalpro.com"

        async def find_customer(self, customer_id):
            request = httpx.Request("POST", f"{self.base_url}/payment/find_customer")
            response = httpx.Response(
                400,
                request=request,
                json={"message_code": 400, "message": "API not found"},
            )
            raise USLegalProApiError(
                "API not found", request=request, response=response
            )

    monkeypatch.setattr(
        "app.services.uslegalpro_payment_service.USLegalProApiClient",
        _Client,
    )
    monkeypatch.setattr(
        "app.services.uslegalpro_payment_service.settings.USLEGALPRO_PAYMENT_API_BASE_URL",
        None,
    )

    with pytest.raises(PaymentApiNotConfiguredError, match="USLEGALPRO_PAYMENT_API_BASE_URL"):
        await USLegalProPaymentService().verify_platform_payment("COM-ULP-DEMO-1")


@pytest.mark.asyncio
async def test_authenticate_and_get_payment_accounts(monkeypatch):
    calls = []

    class _Client:
        def __init__(self, auth_token=None, client_token=None, base_url=None):
            self.auth_token = auth_token

        async def authenticate(self, state, username, password):
            calls.append(("auth", state, username))
            return {
                "data": {
                    "auth_token": "3f1b6c1e-6b4a-4f5e-9a2a-2f5c6a7b8c9d/GENS77/8a7b6c5d-4e3f-4a2b-9c8d-1e2f3a4b5c6d"
                }
            }

        async def get_payment_accounts(self, state):
            calls.append(("accounts", state, self.auth_token.split("/")[1]))
            return {
                "items": [
                    {
                        "id": "acct-1",
                        "name": "Sam I Am",
                        "card_type": "MASTERCARD",
                        "is_expired": "false",
                    }
                ]
            }

    monkeypatch.setattr(
        "app.services.uslegalpro_payment_service.USLegalProApiClient",
        _Client,
    )
    monkeypatch.setattr(
        "app.services.uslegalpro_payment_service.settings.USLEGALPRO_USERNAME",
        "lapen@mailinator.com",
    )
    monkeypatch.setattr(
        "app.services.uslegalpro_payment_service.settings.USLEGALPRO_PASSWORD",
        "secret",
    )

    service = USLegalProPaymentService()
    response = await service.authenticate_and_get_payment_accounts(
        state_code="IL",
        user_id="u1",
    )
    assert calls[0][0] == "auth"
    assert service.payment_account_items(response)[0]["name"] == "Sam I Am"
