"""Authenticate against US Legal Pro and persist users in operational.users."""

from __future__ import annotations

import logging
from typing import Optional

import httpx

from app.adapters.uslegalpro.client import USLegalProAuthError, USLegalProClient
from app.adapters.uslegalpro.tokens import parse_auth_token
from app.api.schemas.auth import LoginResponse
from app.services.operational_user_repository import OperationalUserRepository

logger = logging.getLogger(__name__)


class AuthService:
    def __init__(
        self,
        user_repo: OperationalUserRepository,
        api_client: Optional[USLegalProClient] = None,
    ):
        self.user_repo = user_repo
        self.api_client = api_client or USLegalProClient()

    async def login(self, username: str, password: str, state: str = "ca") -> LoginResponse:
        email = username.strip().lower()
        if not email:
            raise ValueError("Username is required")

        try:
            payload = await self.api_client.authenticate(state, email, password)
        except httpx.HTTPStatusError as exc:
            logger.warning("US Legal Pro login HTTP error: %s", exc)
            raise ValueError("Invalid username or password") from exc
        except USLegalProAuthError as exc:
            raise ValueError(str(exc)) from exc
        except httpx.HTTPError as exc:
            logger.error("US Legal Pro login unreachable: %s", exc)
            raise RuntimeError("Authentication service unavailable") from exc

        item = payload.get("item") or {}
        auth_token = str(item.get("auth_token") or "")
        user_id, _, session_id = parse_auth_token(auth_token)

        row = await self.user_repo.upsert_on_login(
            user_id=user_id,
            email=email,
            auth_token=auth_token,
            session_id=session_id,
        )
        if not row:
            raise RuntimeError("Failed to persist user in database")

        return LoginResponse(
            user_id=str(row.get("user_id") or user_id),
            email=str(row.get("email") or email),
            session_id=str(row.get("session_id") or session_id),
            auth_token=str(row.get("auth_token") or auth_token),
        )
