"""Cached court catalog from integration.jurisdiction_api_data."""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from app.services.legal_filing_repository import LegalFilingRepository

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FALLBACK_FILE = _REPO_ROOT / "court api data.txt"

_FILLING_NOISE = re.compile(
    r"\b(i|i'd|id|want|would|like|to|file|filing|a|an|the|new|court|case|"
    r"please|help|me|for|in|on|with|my|our|look|up|existing|assistant|need|"
    r"start|begin|get)\b",
    re.I,
)


def normalize_catalog_text(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def infer_case_topic(text: str) -> str:
    """Pull a searchable case topic out of natural language."""
    cleaned = text or ""
    for phrase in ("like that", "like this", "something like"):
        cleaned = re.sub(re.escape(phrase), " ", cleaned, flags=re.I)
    cleaned = _FILLING_NOISE.sub(" ", cleaned)
    cleaned = normalize_catalog_text(cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def summarize_courts_from_payload(payload: Any) -> List[Dict[str, Any]]:
    """
    Build a compact court list from raw jurisdiction_api_data JSON.

    Each item: {"code": "...", "name": "...", "case_types": ["Divorce", ...]}
    """
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return []
    if not isinstance(payload, dict):
        return []
    summaries: List[Dict[str, Any]] = []
    for court in payload.get("courts") or []:
        if not isinstance(court, dict):
            continue
        code = str(court.get("code") or "").strip()
        name = str(court.get("name") or code).strip()
        if not code:
            continue
        case_types: List[str] = []
        seen: set[str] = set()
        for category in court.get("categories") or []:
            if not isinstance(category, dict):
                continue
            for case_type in category.get("case_types") or []:
                if not isinstance(case_type, dict):
                    continue
                type_name = str(case_type.get("name") or "").strip()
                if not type_name:
                    continue
                key = normalize_catalog_text(type_name)
                if key in seen:
                    continue
                seen.add(key)
                case_types.append(type_name)
        summaries.append({"code": code, "name": name, "case_types": case_types})
    return summaries


def summarize_courts_from_rows(rows: Iterable[Dict[str, str]]) -> List[Dict[str, Any]]:
    """Build court summaries from flattened catalog rows."""
    by_court: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        code = str(row.get("jurisdiction_code") or "").strip()
        if not code:
            continue
        if code not in by_court:
            by_court[code] = {
                "code": code,
                "name": str(row.get("jurisdiction_name") or code).strip(),
                "case_types": [],
            }
        type_name = str(row.get("case_type_name") or "").strip()
        if not type_name:
            continue
        key = normalize_catalog_text(type_name)
        existing = {
            normalize_catalog_text(name) for name in by_court[code]["case_types"]
        }
        if key not in existing:
            by_court[code]["case_types"].append(type_name)
    return list(by_court.values())


def flatten_jurisdiction_data(payload: Any) -> List[Dict[str, str]]:
    """
    Flatten nested courts → categories → case_types into LLM-ready rows.

    Party code/name are empty here because the cached JSON only stores
    party_type_codes_url. Parties are loaded after a case type is chosen.
    """
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return []
    if not isinstance(payload, dict):
        return []
    rows: List[Dict[str, str]] = []
    for court in payload.get("courts") or []:
        if not isinstance(court, dict):
            continue
        j_code = str(court.get("code") or "").strip()
        j_name = str(court.get("name") or j_code).strip()
        if not j_code:
            continue
        for category in court.get("categories") or []:
            if not isinstance(category, dict):
                continue
            c_code = str(category.get("code") or "").strip()
            c_name = str(category.get("name") or c_code).strip()
            for case_type in category.get("case_types") or []:
                if not isinstance(case_type, dict):
                    continue
                t_code = str(case_type.get("code") or "").strip()
                t_name = str(case_type.get("name") or t_code).strip()
                if not t_code and not t_name:
                    continue
                rows.append(
                    {
                        "jurisdiction_code": j_code,
                        "jurisdiction_name": j_name,
                        "case_category_code": c_code,
                        "case_category_name": c_name,
                        "case_type_code": t_code,
                        "case_type_name": t_name,
                        "party_code": "",
                        "party_name": "",
                        "party_type_codes_url": str(
                            case_type.get("party_type_codes_url") or ""
                        ),
                        "case_subtype_codes_url": str(
                            case_type.get("case_subtype_codes_url") or ""
                        ),
                        "filing_codes_url": str(case_type.get("filing_codes_url") or ""),
                    }
                )
    return rows


def search_catalog_rows(rows: Iterable[Dict[str, str]], query: str) -> List[Dict[str, str]]:
    topic = normalize_catalog_text(query)
    if not topic:
        return list(rows)
    tokens = set(topic.split())
    matched: List[Dict[str, str]] = []
    for row in rows:
        type_name = normalize_catalog_text(row.get("case_type_name"))
        category_name = normalize_catalog_text(row.get("case_category_name"))
        blob = f"{type_name} {category_name}"
        if topic not in blob and not tokens.issubset(set(blob.split())):
            continue
        if tokens == {"divorce"} or topic == "divorce":
            if "no divorce" in type_name or "non divorce" in type_name:
                continue
            if "divorce" not in type_name:
                continue
        matched.append(row)
    return matched


def jurisdiction_options(rows: Iterable[Dict[str, str]]) -> List[Dict[str, str]]:
    seen: Dict[str, Dict[str, str]] = {}
    for row in rows:
        code = row.get("jurisdiction_code") or ""
        if code and code not in seen:
            seen[code] = {
                "code": code,
                "name": row.get("jurisdiction_name") or code,
            }
    return list(seen.values())


def category_options(rows: Iterable[Dict[str, str]]) -> List[Dict[str, str]]:
    seen: Dict[str, Dict[str, str]] = {}
    for row in rows:
        code = row.get("case_category_code") or ""
        if code and code not in seen:
            seen[code] = {
                "code": code,
                "name": row.get("case_category_name") or code,
            }
    return list(seen.values())


def case_type_options(rows: Iterable[Dict[str, str]]) -> List[Dict[str, str]]:
    seen: Dict[str, Dict[str, str]] = {}
    for row in rows:
        code = row.get("case_type_code") or ""
        if code and code not in seen:
            seen[code] = {
                "code": code,
                "name": row.get("case_type_name") or code,
                "party_type_codes_url": row.get("party_type_codes_url") or "",
                "case_subtype_codes_url": row.get("case_subtype_codes_url") or "",
                "filing_codes_url": row.get("filing_codes_url") or "",
                "case_category_code": row.get("case_category_code") or "",
                "case_category_name": row.get("case_category_name") or "",
            }
    return list(seen.values())


def filter_court(rows: Iterable[Dict[str, str]], jurisdiction_code: str) -> List[Dict[str, str]]:
    code = str(jurisdiction_code or "").strip()
    return [row for row in rows if row.get("jurisdiction_code") == code]


def filter_court_category(
    rows: Iterable[Dict[str, str]],
    jurisdiction_code: str,
    category_code: str,
) -> List[Dict[str, str]]:
    j_code = str(jurisdiction_code or "").strip()
    c_code = str(category_code or "").strip()
    return [
        row
        for row in rows
        if row.get("jurisdiction_code") == j_code
        and row.get("case_category_code") == c_code
    ]


def match_jurisdiction_from_text(
    rows: Iterable[Dict[str, str]], text: str
) -> Optional[Dict[str, str]]:
    needle = normalize_catalog_text(text)
    if not needle:
        return None
    options = jurisdiction_options(rows)
    exact = [
        row
        for row in options
        if normalize_catalog_text(row.get("name")) == needle
        or normalize_catalog_text(row.get("code")) == needle
    ]
    if len(exact) == 1:
        return exact[0]
    partial = [
        row
        for row in options
        if needle in normalize_catalog_text(row.get("name"))
        or normalize_catalog_text(row.get("code")) in needle
    ]
    if len(partial) == 1:
        return partial[0]
    return None


class CourtCatalogService:
    """In-memory cache of flattened court / category / case-type rows per state."""

    def __init__(self, fallback_path: Optional[Path] = None) -> None:
        self._cache: Dict[str, List[Dict[str, str]]] = {}
        if fallback_path is not None:
            self._fallback_path = fallback_path
        elif os.getenv("COURT_CATALOG_FILE_FALLBACK", "").strip().lower() in {
            "1",
            "true",
            "yes",
        }:
            self._fallback_path = _FALLBACK_FILE
        else:
            self._fallback_path = Path()

    async def rows_for_state(
        self,
        state_code: str,
        filing_repo: Optional[LegalFilingRepository] = None,
    ) -> List[Dict[str, str]]:
        key = str(state_code or "").strip().upper()
        if not key:
            return []
        if key in self._cache:
            return self._cache[key]
        payload = None
        if filing_repo is not None:
            row = await filing_repo.get_jurisdiction_api_data(key)
            if row:
                payload = row.get("jurisdiction_data") or row.get("api_response")
        if payload is None and self._fallback_path.is_file():
            try:
                raw = json.loads(
                    self._fallback_path.read_text(encoding="utf-8")
                )
                file_state = str(raw.get("state_code") or "").strip().upper()
                if not file_state or file_state == key:
                    payload = raw
                    logger.info(
                        "Loaded court catalog fallback file for %s (%s rows pending flatten)",
                        key,
                        self._fallback_path.name,
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Court catalog fallback file failed: %s", exc)
        rows = flatten_jurisdiction_data(payload)
        if rows:
            self._cache[key] = rows
            logger.info("Cached %s court catalog rows for %s", len(rows), key)
        return rows


_catalog: Optional[CourtCatalogService] = None


def get_court_catalog() -> CourtCatalogService:
    global _catalog
    if _catalog is None:
        _catalog = CourtCatalogService()
    return _catalog


def reset_court_catalog(service: Optional[CourtCatalogService] = None) -> None:
    """Replace the process-wide catalog (tests / cache refresh)."""
    global _catalog
    _catalog = service
