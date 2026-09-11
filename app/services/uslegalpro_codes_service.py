"""Link-driven US Legal Pro filing-code API service."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import parse_qs, urlparse, urlunparse

from app.services.uslegalpro_api_client import USLegalProApiClient

logger = logging.getLogger(__name__)


# All the link keys the case-type item may expose. Kept in one place so both
# ``_normalize`` and ``walk_case_type_chain`` stay in sync.
CASE_TYPE_LINK_KEYS: tuple[str, ...] = (
    "case_category_codes",
    "case_type_codes",
    "case_subtype_codes",
    "party_type_codes",
    "filer_type_codes",
    "filing_codes",
    "filing_type",
    "document_type_codes",
    "optional_service_codes",
    "name_suffix_codes",
    "disclaimer_requirement_codes",
    "countries",
    "states",
    "self",
)


@dataclass
class CodeBundle:
    """Live snapshot of every code list the /efile payload can draw from."""

    state: str = ""
    jurisdiction: Dict[str, Any] = field(default_factory=dict)
    case_category: Dict[str, Any] = field(default_factory=dict)
    case_type: Dict[str, Any] = field(default_factory=dict)
    filer_types: List[Dict[str, Any]] = field(default_factory=list)
    # Normalized ``[{"code": "EFile", "name": "EFile"}, ...]`` from the
    # ``filing_type`` endpoint's dict-shaped response.
    filing_type_options: List[Dict[str, Any]] = field(default_factory=list)
    filing_codes: List[Dict[str, Any]] = field(default_factory=list)
    party_types: List[Dict[str, Any]] = field(default_factory=list)
    document_types_by_filing_code: Dict[str, List[Dict[str, Any]]] = field(
        default_factory=dict
    )
    optional_services_by_filing_code: Dict[str, List[Dict[str, Any]]] = field(
        default_factory=dict
    )
    name_suffix_codes: List[Dict[str, Any]] = field(default_factory=list)
    case_subtype_codes: List[Dict[str, Any]] = field(default_factory=list)

    # ----- helpers used by the validator and the live assembler -----

    def has_filer_type(self, code: str) -> bool:
        return _code_in_rows(self.filer_types, code)

    def has_filing_type(self, code: str) -> bool:
        return _code_in_rows(self.filing_type_options, code)

    def has_filing_code(self, code: str) -> bool:
        return _code_in_rows(self.filing_codes, code)

    def has_party_type(self, code: str) -> bool:
        return _code_in_rows(self.party_types, code)

    def has_doc_type_for_filing(self, filing_code: str, doc_type: str) -> bool:
        rows = self.document_types_by_filing_code.get(str(filing_code or "").strip())
        return _code_in_rows(rows or [], doc_type)

    def required_party_type_codes(self) -> List[str]:
        return [
            str(row.get("code") or "").strip()
            for row in self.party_types
            if row.get("is_required") is True and str(row.get("code") or "").strip()
        ]

    def to_serializable(self) -> Dict[str, Any]:
        """Session-safe snapshot (drop non-JSON ``raw`` and ``links`` fields)."""

        def _slim(row: Dict[str, Any]) -> Dict[str, Any]:
            return {
                key: value
                for key, value in (row or {}).items()
                if key not in {"raw", "links"}
            }

        return {
            "state": self.state,
            "jurisdiction": _slim(self.jurisdiction),
            "case_category": _slim(self.case_category),
            "case_type": _slim(self.case_type),
            "filer_types": [_slim(r) for r in self.filer_types],
            "filing_type_options": [_slim(r) for r in self.filing_type_options],
            "filing_codes": [_slim(r) for r in self.filing_codes],
            "party_types": [_slim(r) for r in self.party_types],
            "document_types_by_filing_code": {
                code: [_slim(r) for r in rows]
                for code, rows in self.document_types_by_filing_code.items()
            },
            "optional_services_by_filing_code": {
                code: [_slim(r) for r in rows]
                for code, rows in self.optional_services_by_filing_code.items()
            },
            "name_suffix_codes": [_slim(r) for r in self.name_suffix_codes],
            "case_subtype_codes": [_slim(r) for r in self.case_subtype_codes],
        }

    @classmethod
    def from_serializable(cls, payload: Any) -> "CodeBundle":
        data = payload if isinstance(payload, dict) else {}
        return cls(
            state=str(data.get("state") or ""),
            jurisdiction=dict(data.get("jurisdiction") or {}),
            case_category=dict(data.get("case_category") or {}),
            case_type=dict(data.get("case_type") or {}),
            filer_types=[dict(r) for r in (data.get("filer_types") or []) if isinstance(r, dict)],
            filing_type_options=[
                dict(r) for r in (data.get("filing_type_options") or []) if isinstance(r, dict)
            ],
            filing_codes=[
                dict(r) for r in (data.get("filing_codes") or []) if isinstance(r, dict)
            ],
            party_types=[
                dict(r) for r in (data.get("party_types") or []) if isinstance(r, dict)
            ],
            document_types_by_filing_code={
                str(code): [dict(r) for r in rows if isinstance(r, dict)]
                for code, rows in (data.get("document_types_by_filing_code") or {}).items()
            },
            optional_services_by_filing_code={
                str(code): [dict(r) for r in rows if isinstance(r, dict)]
                for code, rows in (data.get("optional_services_by_filing_code") or {}).items()
            },
            name_suffix_codes=[
                dict(r) for r in (data.get("name_suffix_codes") or []) if isinstance(r, dict)
            ],
            case_subtype_codes=[
                dict(r) for r in (data.get("case_subtype_codes") or []) if isinstance(r, dict)
            ],
        )


def _code_in_rows(rows: Iterable[Dict[str, Any]], code: str) -> bool:
    target = str(code or "").strip()
    if not target:
        return False
    for row in rows or []:
        if str((row or {}).get("code") or "").strip() == target:
            return True
    return False


def parse_filing_type_response(response: Any) -> List[Dict[str, Any]]:
    """
    Normalize a ``filing_type`` response into ``[{code, name}, ...]``.

    The endpoint returns ``{"message_code": 0, "item": {"EFile": "EFile"}}`` --
    a mapping of code -> display name rather than the usual ``items`` list.
    """
    rows: List[Dict[str, Any]] = []
    if isinstance(response, dict):
        item = response.get("item")
        if isinstance(item, dict):
            for code, name in item.items():
                code_s = str(code or "").strip()
                name_s = str(name or "").strip() or code_s
                if code_s:
                    rows.append({"code": code_s, "name": name_s})
            if rows:
                return rows
        items = response.get("items")
        if isinstance(items, list):
            for entry in items:
                if isinstance(entry, dict):
                    code_s = str(entry.get("code") or entry.get("key") or "").strip()
                    name_s = str(entry.get("name") or entry.get("label") or code_s).strip()
                    if code_s:
                        rows.append({"code": code_s, "name": name_s or code_s})
                elif isinstance(entry, str):
                    val = entry.strip()
                    if val:
                        rows.append({"code": val, "name": val})
            if rows:
                return rows
    if isinstance(response, list):
        for entry in response:
            if isinstance(entry, dict):
                code_s = str(entry.get("code") or "").strip()
                name_s = str(entry.get("name") or code_s).strip()
                if code_s:
                    rows.append({"code": code_s, "name": name_s or code_s})
    return rows


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
        for key in CASE_TYPE_LINK_KEYS:
            url = cls._link(links, key)
            if url:
                row[f"{key}_url"] = url
        if "is_required" in item:
            raw_required = str(item["is_required"]).strip()
            row["is_required_raw"] = raw_required
            row["is_required"] = raw_required.lower() in {
                "true",
                "yes",
                "1",
                "required",
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

    async def _get_raw(self, url: str) -> Any:
        """GET a link, preserving its ``request_id`` query params."""
        base_url, params = self.split_url_params(url)
        return await self._client.get_json(base_url, params=params)

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

    async def fetch_filing_type_by_url(self, url: str) -> List[Dict[str, Any]]:
        """Fetch a ``filing_type`` link and normalize its dict-shaped body."""
        if not url:
            return []
        try:
            response = await self._get_raw(url)
        except Exception as exc:  # noqa: BLE001
            logger.error("Filing-type API link failed url=%s: %s", url, exc)
            return []
        return parse_filing_type_response(response)

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

    async def walk_case_type_chain(
        self,
        state_code: str,
        jurisdiction_code: str,
        case_category_code: str,
        case_type_code: str,
        *,
        filing_codes: Optional[Iterable[str]] = None,
        fetch_optional_services: bool = False,
    ) -> CodeBundle:
        """
        Live-walk the full dependency chain for the selected case type.

        Returns a :class:`CodeBundle` populated by fresh API responses only --
        never from the cached court catalog whose ``request_id`` values are stale.

        ``filing_codes`` is the set of filing codes the caller intends to send
        in ``data.filings[]``; ``document_types_by_filing_code`` (and optionally
        ``optional_services_by_filing_code``) is populated only for those codes
        so we do not fan out to every filing in the catalog.
        """
        state = str(state_code or "").strip().lower()
        jur_code = str(jurisdiction_code or "").strip()
        cat_code = str(case_category_code or "").strip()
        ct_code = str(case_type_code or "").strip()

        bundle = CodeBundle(state=state)
        if not (state and jur_code and cat_code and ct_code):
            logger.warning(
                "walk_case_type_chain missing input state=%s jur=%s cat=%s type=%s",
                state,
                jur_code,
                cat_code,
                ct_code,
            )
            return bundle

        jurisdictions = await self.get_jurisdictions(state)
        jurisdiction = self._find_by_code(jurisdictions, jur_code)
        if not jurisdiction:
            logger.warning(
                "walk_case_type_chain: jurisdiction %s not found in %s response",
                jur_code,
                state,
            )
            return bundle
        bundle.jurisdiction = jurisdiction

        cat_url = str(jurisdiction.get("case_category_codes_url") or "")
        categories = await self.fetch_by_url(cat_url) if cat_url else []
        category = self._find_by_code(categories, cat_code)
        if not category:
            logger.warning(
                "walk_case_type_chain: case category %s not found under jurisdiction %s",
                cat_code,
                jur_code,
            )
            return bundle
        bundle.case_category = category

        type_url = str(category.get("case_type_codes_url") or "")
        case_types = await self.fetch_by_url(type_url) if type_url else []
        case_type = self._find_by_code(case_types, ct_code)
        if not case_type:
            logger.warning(
                "walk_case_type_chain: case type %s not found under category %s",
                ct_code,
                cat_code,
            )
            return bundle
        bundle.case_type = case_type

        # Case-type driven code lists. Each is best-effort -- a missing link
        # produces an empty list but does not abort the walk, because e.g.
        # ``name_suffix_codes`` isn't essential.
        bundle.filer_types = await self.fetch_by_url(
            str(case_type.get("filer_type_codes_url") or "")
        )
        bundle.filing_type_options = await self.fetch_filing_type_by_url(
            str(case_type.get("filing_type_url") or "")
        )
        bundle.filing_codes = await self.fetch_by_url(
            str(case_type.get("filing_codes_url") or "")
        )
        bundle.party_types = await self.fetch_by_url(
            str(case_type.get("party_type_codes_url") or "")
        )
        bundle.case_subtype_codes = await self.fetch_by_url(
            str(case_type.get("case_subtype_codes_url") or "")
        )
        bundle.name_suffix_codes = await self.fetch_by_url(
            str(case_type.get("name_suffix_codes_url") or "")
        )

        # Per-filing document types / optional services -- only for the codes
        # the caller intends to send.
        wanted = {
            str(code or "").strip()
            for code in (filing_codes or [])
            if str(code or "").strip()
        }
        for filing_code in wanted:
            filing = self._find_by_code(bundle.filing_codes, filing_code)
            if not filing:
                # The filing code the caller wants to send does not belong to
                # the selected case type; leave the map empty so the validator
                # reports it.
                logger.warning(
                    "walk_case_type_chain: filing code %s is not valid for case type %s",
                    filing_code,
                    ct_code,
                )
                bundle.document_types_by_filing_code[filing_code] = []
                if fetch_optional_services:
                    bundle.optional_services_by_filing_code[filing_code] = []
                continue
            doc_url = str(filing.get("document_type_codes_url") or "")
            bundle.document_types_by_filing_code[filing_code] = (
                await self.fetch_by_url(doc_url) if doc_url else []
            )
            if fetch_optional_services:
                opt_url = str(filing.get("optional_service_codes_url") or "")
                bundle.optional_services_by_filing_code[filing_code] = (
                    await self.fetch_by_url(opt_url) if opt_url else []
                )

        return bundle
