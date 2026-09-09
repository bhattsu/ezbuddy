"""US Legal Pro payment account retrieval and platform subscription checks."""

from __future__ import annotations

import logging
import re
from datetime import date
from typing import Any, Dict, List, Optional

from app.adapters.uslegalpro.client import USLegalProApiError, extract_auth_token
from app.adapters.uslegalpro.tokens import (
    client_token_from_auth_token,
    resolve_auth_token,
)
from app.config.settings import settings
from app.services.operational_user_repository import OperationalUserRepository
from app.services.uslegalpro_api_client import USLegalProApiClient

logger = logging.getLogger(__name__)

_PAYMENT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
SUBSCRIBE_MESSAGE = "Please subscribe to continue."
PAYMENT_ID_PROMPT = "Please enter the payment ID to verify the payment."
COURT_ACCOUNT_THANKS = "Thank you, we will use that account."


class PaymentApiNotConfiguredError(RuntimeError):
    """The payment host does not serve the Braintree customer/card endpoints."""


def _is_route_missing(exc: USLegalProApiError) -> bool:
    status = exc.response.status_code
    return status == 404 or "api not found" in str(exc).lower()


def extract_payment_id(text: str) -> str:
    raw = str(text or "").strip().strip('"').strip("'")
    if _PAYMENT_ID_RE.match(raw):
        return raw
    candidates = [
        token
        for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]{2,}", raw)
        if _PAYMENT_ID_RE.match(token)
    ]
    if not candidates:
        return ""
    candidates.sort(key=lambda token: (("-" in token or "_" in token), len(token)), reverse=True)
    return candidates[0]


def is_card_expired(
    expire_month: Any,
    expire_year: Any,
    today: Optional[date] = None,
) -> bool:
    """True when the card month/year is before the current calendar month."""
    current = today or date.today()
    try:
        month = int(str(expire_month).strip())
        year = int(str(expire_year).strip())
    except (TypeError, ValueError):
        return True
    if month < 1 or month > 12:
        return True
    if year < 100:
        year += 2000
    return (year, month) < (current.year, current.month)


def subscription_status(
    cards: List[Dict[str, Any]],
    today: Optional[date] = None,
) -> str:
    """Return ``verified`` if any stored card is still current, else ``ended``."""
    current = today or date.today()
    for card in cards:
        if not isinstance(card, dict):
            continue
        if not is_card_expired(
            card.get("expire_month"),
            card.get("expire_year"),
            today=current,
        ):
            return "verified"
    return "ended"


def customer_id_from_find_response(response: Dict[str, Any]) -> Optional[str]:
    item = response.get("item")
    if isinstance(item, dict):
        found = item.get("id")
        if found:
            return str(found).strip()
    found = response.get("id")
    if found:
        return str(found).strip()
    return None


def credit_card_items(response: Dict[str, Any]) -> List[Dict[str, Any]]:
    items = response.get("items")
    if isinstance(items, list):
        return [dict(item) for item in items if isinstance(item, dict)]
    item = response.get("item")
    if isinstance(item, dict):
        return [dict(item)]
    return []


def format_court_payment_account(item: Dict[str, Any], today: Optional[date] = None) -> Dict[str, Any]:
    expired_raw = str(item.get("is_expired") or "").strip().lower()
    expired = expired_raw in {"true", "1", "yes"}
    if not expired and item.get("expire_year") not in (None, ""):
        expired = is_card_expired(
            item.get("expire_month"),
            item.get("expire_year"),
            today=today,
        )
    card_type = str(item.get("card_type") or item.get("type_code") or "").strip()
    last4 = str(item.get("last4_digit") or item.get("last4") or "").strip()
    name = str(item.get("name") or "").strip()
    account_id = str(item.get("id") or "").strip()
    status = "expired" if expired else "active"
    if last4:
        label = f"{name or account_id} — {card_type or 'account'} ending {last4} ({status})"
    else:
        label = f"{name or account_id} — {card_type or 'account'} ({status})"
    return {
        "id": account_id,
        "name": name,
        "card_type": card_type,
        "is_expired": expired,
        "last4_digit": last4,
        "expire_month": item.get("expire_month"),
        "expire_year": item.get("expire_year"),
        "type_code": item.get("type_code"),
        "code": account_id,
        "label": label,
    }


def format_court_payment_message(accounts: List[Dict[str, Any]]) -> str:
    if not accounts:
        return "No court payment accounts were found. " + SUBSCRIBE_MESSAGE
    lines = ["Court payment accounts:"]
    for index, account in enumerate(accounts, start=1):
        expired = "yes" if account.get("is_expired") else "no"
        lines.extend(
            [
                f"{index}. id: {account.get('id') or ''}",
                f"   name: {account.get('name') or ''}",
                f"   card type: {account.get('card_type') or ''}",
                f"   expired: {expired}",
            ]
        )
    lines.append("Which payment account should we use?")
    return "\n".join(lines)


def match_court_payment_account(
    accounts: List[Dict[str, Any]],
    text: str,
) -> Optional[Dict[str, Any]]:
    raw = str(text or "").strip()
    if not raw or not accounts:
        return None
    if raw.isdigit():
        index = int(raw) - 1
        if 0 <= index < len(accounts):
            return accounts[index]
    needle = re.sub(r"[^a-z0-9]+", " ", raw.lower()).strip()
    matches: List[Dict[str, Any]] = []
    for account in accounts:
        haystack = " ".join(
            str(account.get(key) or "")
            for key in ("id", "name", "card_type", "last4_digit", "label", "code")
        )
        normalized = re.sub(r"[^a-z0-9]+", " ", haystack.lower()).strip()
        if needle == normalized or needle == str(account.get("id") or "").lower():
            return account
        if needle and needle in normalized:
            matches.append(account)
    return matches[0] if len(matches) == 1 else None


class USLegalProPaymentService:
    """Fetch payment accounts using the logged-in user's stored auth token."""

    def __init__(self, user_repo: Optional[OperationalUserRepository] = None) -> None:
        self.user_repo = user_repo

    @staticmethod
    def _client(auth_token: str = "", *, payment: bool = False) -> USLegalProApiClient:
        token = str(auth_token or "").strip()
        client_token = client_token_from_auth_token(token) or settings.USLEGALPRO_CLIENT_TOKEN
        base_url = None
        if payment:
            base_url = str(settings.USLEGALPRO_PAYMENT_API_BASE_URL or "").strip() or None
        kwargs = {
            "auth_token": token or None,
            "client_token": client_token or None,
        }
        if base_url:
            kwargs["base_url"] = base_url
        return USLegalProApiClient(**kwargs)

    async def _auth_token_for_user(self, user_id: str) -> str:
        user_row = (
            await self.user_repo.get_by_user_id(user_id)
            if self.user_repo
            else None
        )
        return resolve_auth_token(user_row)

    @staticmethod
    def _route_error(client: USLegalProApiClient, path: str) -> PaymentApiNotConfiguredError:
        configured = str(settings.USLEGALPRO_PAYMENT_API_BASE_URL or "").strip()
        detail = (
            f"{client.base_url}{path} is not a valid endpoint."
            if configured
            else (
                f"USLEGALPRO_PAYMENT_API_BASE_URL is not set, so the court API host "
                f"({client.base_url}) was used and it does not serve {path}."
            )
        )
        return PaymentApiNotConfiguredError(
            "The payment service is not configured correctly. "
            + detail
            + " Set USLEGALPRO_PAYMENT_API_BASE_URL to the Braintree payment domain."
        )

    async def find_customer(self, customer_id: str) -> Dict[str, Any]:
        client = self._client(payment=True)
        try:
            return await client.find_customer(customer_id)
        except USLegalProApiError as exc:
            if _is_route_missing(exc):
                raise self._route_error(
                    client, settings.USLEGALPRO_FIND_CUSTOMER_PATH
                ) from exc
            raise

    async def get_credit_cards(self, customer_id: str) -> Dict[str, Any]:
        client = self._client(payment=True)
        try:
            return await client.get_credit_cards(customer_id)
        except USLegalProApiError as exc:
            if _is_route_missing(exc):
                raise self._route_error(
                    client, settings.USLEGALPRO_CREDIT_CARDS_PATH
                ) from exc
            raise

    async def verify_platform_payment(self, payment_id: str) -> Dict[str, Any]:
        """Look up Braintree customer + cards and decide subscription with Python only."""
        customer = await self.find_customer(payment_id)
        found_id = customer_id_from_find_response(customer)
        if not found_id:
            return {
                "verified": False,
                "status": "not_found",
                "customer_id": None,
                "cards": [],
                "message": (
                    "This payment ID was not found. "
                    + SUBSCRIBE_MESSAGE
                ),
            }
        cards_response = await self.get_credit_cards(found_id)
        cards = credit_card_items(cards_response)
        status = subscription_status(cards)
        if status == "verified":
            return {
                "verified": True,
                "status": "verified",
                "customer_id": found_id,
                "cards": cards,
                "message": "Payment is verified.",
            }
        return {
            "verified": False,
            "status": "ended",
            "customer_id": found_id,
            "cards": cards,
            "message": "Subscription ended. " + SUBSCRIBE_MESSAGE,
        }

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
            getattr(client, "client_token", None) or "<missing>",
            "present" if getattr(client, "auth_token", None) else "<missing>",
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

    async def authenticate_and_get_payment_accounts(
        self,
        *,
        state_code: str,
        user_id: str,
    ) -> Dict[str, Any]:
        """Authenticate when credentials are configured, else use the stored token."""
        username = str(settings.USLEGALPRO_USERNAME or "").strip()
        password = str(settings.USLEGALPRO_PASSWORD or "")
        if username and password:
            client = self._client()
            payload = await client.authenticate(state_code, username, password)
            auth_token = extract_auth_token(payload)
            if not auth_token:
                raise ValueError("Authentication response missing auth_token")
            return await self.get_payment_accounts(
                state_code=state_code,
                auth_token=auth_token,
            )
        return await self.get_payment_accounts_for_user(
            state_code=state_code,
            user_id=user_id,
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
