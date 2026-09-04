"""US Legal Pro existing-case search and detail retrieval."""

from __future__ import annotations

import logging
from typing import Any, Dict, List
from urllib.parse import quote, urlencode

from app.adapters.uslegalpro.tokens import client_token_from_auth_token
from app.services.uslegalpro_api_client import USLegalProApiClient

logger = logging.getLogger(__name__)


class USLegalProExistingCaseService:
    """Call authenticated existing-case APIs with the logged-in user's token."""

    @staticmethod
    def _client(auth_token: str) -> USLegalProApiClient:
        client_token = client_token_from_auth_token(auth_token)
        return USLegalProApiClient(
            auth_token=auth_token,
            client_token=client_token or None,
        )

    async def search_case(
        self,
        *,
        state_code: str,
        jurisdiction_code: str,
        case_number: str,
        auth_token: str,
    ) -> Dict[str, Any]:
        client = self._client(auth_token)
        state = state_code.strip().lower()
        # The API rejects a percent-encoded colon in jurisdiction codes such as
        # "refugio:dc", so the query string is built with the colon left intact.
        query = urlencode(
            {
                "jurisdiction": jurisdiction_code.strip(),
                "case_number": case_number.strip(),
            },
            safe=":",
        )
        logger.info(
            "Existing-case search state=%s query=%s clienttoken=%s authtoken=%s",
            state,
            query,
            client.client_token or "<missing>",
            "present" if client.auth_token else "<missing>",
        )
        response = await client.get_json(f"/v2/{state}/search_case?{query}")
        return dict(response) if isinstance(response, dict) else {"items": []}

    async def get_case_details(
        self,
        *,
        state_code: str,
        case_tracking_id: str,
        auth_token: str,
        case_detail_url: str = "",
    ) -> Dict[str, Any]:
        client = self._client(auth_token)
        state = state_code.strip().lower()
        tracking_id = quote(case_tracking_id.strip(), safe="~:-_")
        try:
            response = await client.get_json(f"/v2/{state}/case/{tracking_id}")
        except Exception:
            if not case_detail_url:
                raise
            logger.info(
                "Case path lookup failed; following search response case_detail link"
            )
            response = await client.get_json(case_detail_url)
        return dict(response) if isinstance(response, dict) else {}

    @staticmethod
    def search_items(response: Dict[str, Any]) -> List[Dict[str, Any]]:
        items = response.get("items")
        return [dict(item) for item in items or [] if isinstance(item, dict)]

    @staticmethod
    def detail_item(response: Dict[str, Any]) -> Dict[str, Any]:
        item = response.get("item")
        if isinstance(item, dict):
            return dict(item)
        items = response.get("items")
        if isinstance(items, list) and items and isinstance(items[0], dict):
            return dict(items[0])
        return {}
