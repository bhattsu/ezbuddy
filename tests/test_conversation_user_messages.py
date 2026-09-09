"""Tests for conversation user-messages endpoint."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.endpoints import conversations as conversations_endpoint
from app.services.conversation_repository import ConversationRepository
from app.services.operational_user_repository import OperationalUserRepository


@pytest.fixture
def app(monkeypatch):
    test_app = FastAPI()
    test_app.include_router(conversations_endpoint.router, prefix="/api/conversations")

    async def _fake_rds(_app):
        return MagicMock()

    monkeypatch.setattr(
        "app.services.aim_factory.get_or_create_rds_repo",
        _fake_rds,
    )
    return test_app


@pytest.mark.asyncio
async def test_get_user_messages_success(app, monkeypatch):
    user_id = "11111111-1111-1111-1111-111111111111"
    conversation_id = "22222222-2222-2222-2222-222222222222"
    message_id = "33333333-3333-3333-3333-333333333333"
    created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)

    async def _fake_get_by_email_and_session(self, _email, _session_id):
        return {
            "user_id": user_id,
            "email": "user@example.com",
            "session_id": "sess-123",
        }

    async def _fake_get_user_messages_by_user_id(self, _user_id):
        return [
            {
                "message_id": message_id,
                "conversation_id": conversation_id,
                "sender": "USER",
                "message": "Hello",
                "created_at": created_at,
                "conversation_session_id": None,
                "conversation_status": "ACTIVE",
            }
        ]

    monkeypatch.setattr(
        OperationalUserRepository,
        "get_by_email_and_session",
        _fake_get_by_email_and_session,
    )
    monkeypatch.setattr(
        ConversationRepository,
        "get_user_messages_by_user_id",
        _fake_get_user_messages_by_user_id,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/conversations/user-messages",
            json={"email": "user@example.com", "session_id": "sess-123"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["user_id"] == user_id
    assert payload["email"] == "user@example.com"
    assert payload["session_id"] == "sess-123"
    assert len(payload["messages"]) == 1
    assert payload["messages"][0]["message"] == "Hello"
    assert payload["messages"][0]["message_id"] == message_id
    assert UUID(payload["messages"][0]["conversation_id"]) == UUID(conversation_id)


@pytest.mark.asyncio
async def test_get_user_messages_not_found(app, monkeypatch):
    async def _fake_get_by_email_and_session(self, _email, _session_id):
        return None

    monkeypatch.setattr(
        OperationalUserRepository,
        "get_by_email_and_session",
        _fake_get_by_email_and_session,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/conversations/user-messages",
            json={"email": "missing@example.com", "session_id": "sess-404"},
        )

    assert response.status_code == 404
