"""Auth token helpers for US Legal Pro platform."""

from __future__ import annotations

import uuid
from typing import Any

from app.config.settings import settings


def parse_auth_token(auth_token: str) -> tuple[str, str, str]:
    """
    Parse ``{user_id}/{client_token}/{session_id}`` from authenticate response.

    Returns:
        (user_id, client_token, session_id)
    """
    parts = (auth_token or "").strip().split("/")
    if len(parts) != 3:
        raise ValueError("Invalid auth_token format; expected user_id/client_token/session_id")
    user_id, client_token, session_id = parts
    uuid.UUID(user_id)
    uuid.UUID(session_id)
    return user_id, client_token, session_id


def client_token_from_auth_token(auth_token: str) -> str:
    """Return the client token embedded in an auth token, or "" if absent.

    Authenticated calls must present the same client token the session was
    issued under, which is not always the one configured in settings.
    """
    parts = (auth_token or "").strip().split("/")
    return parts[1].strip() if len(parts) == 3 else ""


def resolve_auth_token(
    user_row: dict[str, Any] | None,
    *,
    override: str | None = None,
) -> str:
    """Return the auth token to use for authenticated US Legal Pro API calls.

    Prefers ``override`` or ``USLEGALPRO_AUTH_TOKEN`` (for local/dev), then the
    token stored on ``operational.users``.
    """
    configured = str(override or settings.USLEGALPRO_AUTH_TOKEN or "").strip()
    if configured:
        return configured
    stored = str((user_row or {}).get("auth_token") or "").strip()
    if not stored:
        raise ValueError(
            "No US Legal Pro authentication token is available. Please sign in again."
        )
    return stored
