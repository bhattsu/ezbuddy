"""Backward-compatible wrapper around ``USLegalProClient`` adapter."""

from __future__ import annotations

from typing import Any

from app.adapters.uslegalpro.client import USLegalProClient
from app.config.settings import settings


class USLegalProApiClient:
    """GET JSON from US Legal Pro API using configured base URL and tokens."""

    def __init__(
        self,
        base_url: str | None = None,
        client_token: str | None = None,
        auth_token: str | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self._client = USLegalProClient(
            base_url=base_url,
            client_token=client_token,
            auth_token=auth_token,
            timeout_seconds=timeout_seconds,
        )
        self.base_url = self._client.base_url
        self.client_token = self._client.client_token
        self.auth_token = self._client.auth_token
        self.timeout_seconds = self._client.timeout_seconds

    async def get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return await self._client.get_json(path, params=params)

    async def get_payment_accounts(self, state: str) -> dict[str, Any]:
        return await self._client.get_payment_accounts(state)
