"""Tests for existing-case auth scoping helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agents.conversation.orchestration.helpers import (
    auth_token_for_user,
    format_existing_case_api_error,
)
from app.services.uslegalpro_codes_service import USLegalProCodesService


def test_format_existing_case_api_error():
    msg = format_existing_case_api_error(
        Exception("Location 'refugio:dc' not found.")
    )
    assert "refugio:dc" in msg
    assert "case number" in msg


@pytest.mark.asyncio
async def test_auth_token_for_user_returns_stored_token():
    repo = MagicMock()
    repo.get_by_user_id = AsyncMock(
        return_value={"auth_token": "uid/GENS77/session"}
    )
    token = await auth_token_for_user(repo, "user-1")
    assert token == "uid/GENS77/session"


@pytest.mark.asyncio
async def test_auth_token_for_user_missing_token():
    repo = MagicMock()
    repo.get_by_user_id = AsyncMock(return_value={})
    assert await auth_token_for_user(repo, "user-1") == ""


def test_codes_service_for_auth_token_uses_user_client():
    service = USLegalProCodesService.for_auth_token("uid/GENS77/session")
    assert service._client.auth_token == "uid/GENS77/session"
    assert service._client.client_token == "GENS77"
