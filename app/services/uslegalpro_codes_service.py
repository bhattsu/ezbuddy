"""Link-driven US Legal Pro filing-code API service."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse, urlunparse

from app.services.uslegalpro_api_client import USLegalProApiClient

logger = logging.getLogger(__name__)


class USLegalProCodesService:
    """Follow API-provided links from courts through party types."""

    def __init__(self, api_client: Optional[USLegalProApiClient] = None) -> None:
        self._client = api_client or USLegalProApiClient()
        self._jurisdiction_cache: Dict[str, List[Dict[str, Any]]] = {}
        self._party_type_cache: Dict[tuple[str, str, str, str], List[Dict[str, Any]]] = {}

    @staticmethod
    def _items(response: Any) -> List[Dict[str, Any]]:
        if isinstance(response, list):
            return [row for row in response if isinstance(row, dict)]
        if isinstance(response, dict):
            for key in ("items", "item", "data", "results"):
                value = response.get(key)
                if isinstance(value, list):
                    return [row for row in value if isinstance(row, dict)]
                if isinstance(value, dict):
                    return [value]
        return []

    @staticmethod
    def _link(links: Any, key: str) -> Optional[str]:
        if not isinstance(links, dict):
            return None
        value = links.get(key)
        if isinstance(value, dict):
            url = value.get("link") or value.get("href") or value.get("url")
            return str(url).strip() if url else None
        return str(value).strip() if isinstance(value, str) and value.strip() else None

    @classmethod
    def _normalize(cls, item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        code = str(item.get("code") or item.get("efmkey") or "").strip()
        name = str(item.get("name") or item.get("label") or "").strip()
        if not code and not name:
            return None
        links = item.get("link") if isinstance(item.get("link"), dict) else {}
        row: Dict[str, Any] = {
            "code": code or name,
            "name": name or code,
            "links": links,
            "raw": item,
        }
        for key in (
            "case_category_codes",
            "case_type_codes",
            "party_type_codes",
            "filing_codes",
            "document_type_codes",
            "self",
        ):
            url = cls._link(links, key)
            if url:
                row[f"{key}_url"] = url
        if "is_required" in item:
            row["is_required"] = str(item["is_required"]).lower() in {
                "true",
                "yes",
                "1",
                "required",
                "both",
            }
        if "fee" in item:
            row["fee"] = item["fee"]
        return row

    @classmethod
    def _normalize_response(cls, response: Any) -> List[Dict[str, Any]]:
        rows = []
        for item in cls._items(response):
            row = cls._normalize(item)
            if row:
                rows.append(row)
        return rows

    @staticmethod
    def split_url_params(url: str) -> tuple[str, Dict[str, str]]:
        parsed = urlparse(str(url).strip())
        params = {
            key: values[0]
            for key, values in parse_qs(
                parsed.query, keep_blank_values=True
            ).items()
            if values
        }
        return urlunparse(parsed._replace(query="", fragment="")), params

    async def fetch_by_url(self, url: str) -> List[Dict[str, Any]]:
        if not url:
            return []
        base_url, params = self.split_url_params(url)
        try:
            response = await self._client.get_json(base_url, params=params)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Codes API link failed url=%s request_id=%s: %s",
                base_url,
                params.get("request_id"),
                exc,
            )
            return []
        return self._normalize_response(response)

    async def get_jurisdictions(self, state_code: str) -> List[Dict[str, Any]]:
        state = str(state_code or "").strip().lower()
        if not state:
            return []
        if state in self._jurisdiction_cache:
            return self._jurisdiction_cache[state]
        try:
            response = await self._client.get_json(
                f"/v2/{state}/code/jurisdiction_codes",
                params={"is_initial": "true", "court_system": "tyler"},
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("Jurisdiction codes API failed state=%s: %s", state, exc)
            return []
        rows = self._normalize_response(response)
        if rows:
            self._jurisdiction_cache[state] = rows
        return rows

    async def get_case_categories_from_jurisdiction_link(
        self, url: str
    ) -> List[Dict[str, Any]]:
        return await self.fetch_by_url(url)

    async def get_case_types_from_category_link(
        self, url: str
    ) -> List[Dict[str, Any]]:
        return await self.fetch_by_url(url)

    @staticmethod
    def _find_by_code(rows: List[Dict[str, Any]], code: str) -> Optional[Dict[str, Any]]:
        target = str(code or "").strip()
        if not target:
            return None
        for row in rows:
            if str(row.get("code") or "").strip() == target:
                return row
        return None

    async def get_party_types_for_case_type(
        self,
        state_code: str,
        jurisdiction_code: str,
        case_category_code: str,
        case_type_code: str,
    ) -> List[Dict[str, Any]]:
        """
        Resolve party types by walking live API links from the jurisdiction down.

        The cached court catalog stores ``request_id`` tokens the API rejects,
        so each level's link must come from a live response.
        """
        key = (
            str(state_code or "").strip().lower(),
            str(jurisdiction_code or "").strip(),
            str(case_category_code or "").strip(),
            str(case_type_code or "").strip(),
        )
        if not all(key):
            return []
        if key in self._party_type_cache:
            return self._party_type_cache[key]

        steps = (
            ("jurisdiction", jurisdiction_code, "case_category_codes_url"),
            ("case category", case_category_code, "case_type_codes_url"),
            ("case type", case_type_code, "party_type_codes_url"),
        )
        rows = await self.get_jurisdictions(state_code)
        for label, code, link_key in steps:
            match = self._find_by_code(rows, code)
            if not match:
                logger.warning(
                    "Party type lookup stopped: %s %s not found in live API response",
                    label,
                    code,
                )
                return []
            url = str(match.get(link_key) or "")
            if not url:
                logger.warning(
                    "Party type lookup stopped: %s %s has no %s link",
                    label,
                    code,
                    link_key,
                )
                return []
            rows = await self.fetch_by_url(url)

        if rows:
            self._party_type_cache[key] = rows
        return rows
