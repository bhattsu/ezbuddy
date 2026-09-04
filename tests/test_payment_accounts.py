"""Tests for authenticated payment-accounts API integration."""

from __future__ import annotations

import pytest

from app.adapters.uslegalpro.tokens import resolve_auth_token
from app.services.uslegalpro_payment_service import USLegalProPaymentService


AUTH_TOKEN = "3f1b6c1e-6b4a-4f5e-9a2a-2f5c6a7b8c9d/GENS99/8a7b6c5d-4e3f-4a2b-9c8d-1e2f3a4b5c6d"


def test_resolve_auth_token_prefers_stored_user_token(monkeypatch):
    monkeypatch.setattr(
        "app.adapters.uslegalpro.tokens.settings.USLEGALPRO_AUTH_TOKEN",
        "",
    )
    token = resolve_auth_token({"auth_token": AUTH_TOKEN})
    assert token == AUTH_TOKEN


def test_resolve_auth_token_missing_raises(monkeypatch):
    monkeypatch.setattr(
        "app.adapters.uslegalpro.tokens.settings.USLEGALPRO_AUTH_TOKEN",
        "",
    )
    with pytest.raises(ValueError, match="No US Legal Pro authentication token"):
        resolve_auth_token(None)


@pytest.mark.asyncio
async def test_get_payment_accounts_uses_authtoken(monkeypatch):
    calls = []

    class _Client:
        def __init__(self, auth_token=None, client_token=None):
            assert auth_token == AUTH_TOKEN
            assert client_token == "GENS99"
            self.auth_token = auth_token
            self.client_token = client_token

        async def get_payment_accounts(self, state):
            calls.append(state)
            return {
                "items": [
                    {
                        "id": "acct-1",
                        "name": "Default Card",
                        "payment_method": "credit_card",
                    }
                ]
            }

    monkeypatch.setattr(
        "app.services.uslegalpro_payment_service.USLegalProApiClient",
        _Client,
    )
    service = USLegalProPaymentService()

    response = await service.get_payment_accounts(
        state_code="TX",
        auth_token=AUTH_TOKEN,
    )
    items = service.payment_account_items(response)

    assert calls == ["tx"]
    assert items[0]["id"] == "acct-1"


@pytest.mark.asyncio
async def test_get_payment_accounts_for_user_reads_token_from_db(monkeypatch):
    class _UserRepo:
        async def get_by_user_id(self, user_id):
            assert user_id == "u1"
            return {"auth_token": AUTH_TOKEN}

    class _Client:
        def __init__(self, auth_token=None, client_token=None):
            assert auth_token == AUTH_TOKEN
            self.auth_token = auth_token
            self.client_token = client_token

        async def get_payment_accounts(self, state):
            return {"items": [{"id": "acct-db"}]}

    monkeypatch.setattr(
        "app.services.uslegalpro_payment_service.USLegalProApiClient",
        _Client,
    )
    monkeypatch.setattr(
        "app.adapters.uslegalpro.tokens.settings.USLEGALPRO_AUTH_TOKEN",
        "",
    )

    service = USLegalProPaymentService(user_repo=_UserRepo())
    response = await service.get_payment_accounts_for_user(
        state_code="ca",
        user_id="u1",
    )

    assert service.payment_account_items(response)[0]["id"] == "acct-db"
