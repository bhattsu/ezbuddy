"""US Legal Pro payment account retrieval."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.adapters.uslegalpro.tokens import (
    client_token_from_auth_token,
    resolve_auth_token,
)
from app.services.operational_user_repository import OperationalUserRepository
from app.services.uslegalpro_api_client import USLegalProApiClient

logger = logging.getLogger(__name__)


class USLegalProPaymentService:
    """Fetch payment accounts using the logged-in user's stored auth token."""

    def __init__(self, user_repo: Optional[OperationalUserRepository] = None) -> None:
        self.user_repo = user_repo

    @staticmethod
    def _client(auth_token: str) -> USLegalProApiClient:
        client_token = client_token_from_auth_token(auth_token)
        return USLegalProApiClient(
            auth_token=auth_token,
            client_token=client_token or None,
        )

    async def _auth_token_for_user(self, user_id: str) -> str:
        user_row = (
            await self.user_repo.get_by_user_id(user_id)
            if self.user_repo
            else None
        )
        return resolve_auth_token(user_row)

    async def get_payment_accounts(
        self,
        *,
        state_code: str,
        auth_token: str,
    ) -> Dict[str, Any]:
        client = self._client(auth_token)
        state = state_code.strip().lower()
        logger.info(
            "Payment accounts lookup state=%s clienttoken=%s authtoken=%s",
            state,
            client.client_token or "<missing>",
            "present" if client.auth_token else "<missing>",
        )
        return await client.get_payment_accounts(state)

    async def get_payment_accounts_for_user(
        self,
        *,
        state_code: str,
        user_id: str,
    ) -> Dict[str, Any]:
        auth_token = await self._auth_token_for_user(user_id)
        logger.info(
            "Payment accounts auth token source=operational.users platform_user=%s",
            auth_token.split("/")[0],
        )
        return await self.get_payment_accounts(
            state_code=state_code,
            auth_token=auth_token,
        )

    @staticmethod
    def payment_account_items(response: Dict[str, Any]) -> List[Dict[str, Any]]:
        items = response.get("items")
        if isinstance(items, list):
            return [dict(item) for item in items if isinstance(item, dict)]
        item = response.get("item")
        if isinstance(item, dict):
            return [dict(item)]
        return []
