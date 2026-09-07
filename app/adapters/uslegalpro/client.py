"""HTTP client for US Legal Pro platform APIs (auth, codes, reference data)."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config.settings import settings
from app.core.ingestion.excel.api_options_config import normalize_api_endpoint

logger = logging.getLogger(__name__)


class USLegalProAuthError(Exception):
    """Authentication failed against US Legal Pro API."""


class USLegalProApiError(httpx.HTTPStatusError):
    """API returned an error status, carrying the platform's own message."""

    def __init__(self, message: str, *, request: httpx.Request, response: httpx.Response):
        super().__init__(message, request=request, response=response)


def extract_auth_token(payload: dict[str, Any]) -> str:
    item = payload.get("item") or {}
    nested = payload.get("data") or {}
    token = ""
    if isinstance(item, dict):
        token = str(item.get("auth_token") or "").strip()
    if not token and isinstance(nested, dict):
        token = str(nested.get("auth_token") or "").strip()
    return token


def _api_error_message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        text = str(body.get("message") or "").strip()
        if text:
            return text
    return f"US Legal Pro API returned HTTP {response.status_code}"


class USLegalProClient:
    """Adapter for US Legal Pro REST APIs."""

    def __init__(
        self,
        base_url: str | None = None,
        client_token: str | None = None,
        auth_token: str | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self.base_url = (base_url or settings.USLEGALPRO_API_BASE_URL).rstrip("/")
        self.client_token = (
            client_token if client_token is not None else settings.USLEGALPRO_CLIENT_TOKEN
        )
        self.auth_token = auth_token if auth_token is not None else settings.USLEGALPRO_AUTH_TOKEN
        self.timeout_seconds = timeout_seconds or settings.USLEGALPRO_API_TIMEOUT_SECONDS

    def _headers(self, *, include_auth: bool = True) -> dict[str, str]:
        headers: dict[str, str] = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if self.client_token:
            headers["clienttoken"] = self.client_token
        if include_auth and self.auth_token:
            headers["authtoken"] = self.auth_token
        return headers

    async def authenticate(
        self,
        state: str,
        username: str,
        password: str,
    ) -> dict[str, Any]:
        """
        POST /v2/{state}/user/authenticate

        Returns the full JSON body; ``item.auth_token`` is the long-lived token.
        """
        state_code = (state or "ca").strip().lower()
        url = f"{self.base_url}/v2/{state_code}/user/authenticate"
        payload = {"data": {"username": username.strip(), "password": password}}

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(url, headers=self._headers(include_auth=False), json=payload)

        if response.status_code >= 400:
            logger.warning(
                "US Legal Pro authenticate failed status=%s body=%s",
                response.status_code,
                response.text[:500],
            )
            response.raise_for_status()

        data = response.json()
        message_code = data.get("message_code", 0)
        if message_code not in (0, None):
            raise USLegalProAuthError(
                f"Authentication rejected (message_code={message_code})"
            )

        auth_token = extract_auth_token(data)
        if not auth_token:
            raise USLegalProAuthError("Authentication response missing auth_token")

        return data

    async def get_payment_accounts(self, state: str) -> dict[str, Any]:
        """
        GET /v2/{state}/payment_accounts

        Returns payment accounts for the authenticated user (requires ``authtoken``).
        """
        state_code = (state or "ca").strip().lower()
        response = await self.get_json(f"/v2/{state_code}/payment_accounts")
        return dict(response) if isinstance(response, dict) else {"items": response or []}

    async def find_customer(self, customer_id: str) -> dict[str, Any]:
        """POST {payment_domain}/payment/find_customer with ``[{"id": customer_id}]``."""
        response = await self.post_json(
            settings.USLEGALPRO_FIND_CUSTOMER_PATH,
            [{"id": customer_id}],
            include_auth=False,
        )
        return dict(response) if isinstance(response, dict) else {"item": response}

    async def get_credit_cards(self, customer_id: str) -> dict[str, Any]:
        """POST {payment_domain}/payment/credit_cards with ``[customer_id]``."""
        response = await self.post_json(
            settings.USLEGALPRO_CREDIT_CARDS_PATH,
            [customer_id],
            include_auth=False,
        )
        return dict(response) if isinstance(response, dict) else {"items": response or []}

    async def post_json(
        self,
        path: str,
        payload: Any,
        *,
        include_auth: bool = True,
    ) -> Any:
        raw = path.strip()
        if raw.startswith("http://") or raw.startswith("https://"):
            url = raw
        else:
            normalized = normalize_api_endpoint(raw)
            url = f"{self.base_url}{normalized}"

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                url,
                headers=self._headers(include_auth=include_auth),
                json=payload,
            )
            if response.status_code >= 400:
                logger.warning(
                    "US Legal Pro API POST error status=%s url=%s body=%s",
                    response.status_code,
                    url,
                    response.text[:500],
                )
                raise USLegalProApiError(
                    _api_error_message(response),
                    request=response.request,
                    response=response,
                )
            return response.json()

    async def get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        raw = path.strip()
        if raw.startswith("http://") or raw.startswith("https://"):
            url = raw
        else:
            normalized = normalize_api_endpoint(raw)
            url = f"{self.base_url}{normalized}"

        headers = {
            "Accept": "application/json",
            **{k: v for k, v in self._headers().items() if k != "Content-Type"},
        }

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.get(
                url,
                # Preserve a query string already present on an API link.
                params=params if params else None,
                headers=headers,
            )
            if response.status_code >= 400:
                logger.warning(
                    "US Legal Pro API error status=%s url=%s sent_headers=%s body=%s",
                    response.status_code,
                    url,
                    sorted(headers),
                    response.text[:500],
                )
                raise USLegalProApiError(
                    _api_error_message(response),
                    request=response.request,
                    response=response,
                )
            return response.json()
