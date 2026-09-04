"""
Weekly sync: Tyler jurisdiction codes API → integration.jurisdiction_api_data.

Follows link-driven cascade:
  jurisdiction_codes → case_category_codes → case_type_codes

Usage:
    python scripts/sync_jurisdiction_api_data.py
    python scripts/sync_jurisdiction_api_data.py --state TX
    python scripts/sync_jurisdiction_api_data.py --env-file .env --dry-run
    python scripts/sync_jurisdiction_api_data.py --concurrency 30 --force

Required environment variables:
    RDS_HOST, RDS_DATABASE, RDS_USERNAME, RDS_PASSWORD
    USLEGALPRO_API_BASE_URL, USLEGALPRO_CLIENT_TOKEN, USLEGALPRO_AUTH_TOKEN

Weekly scheduling (Linux cron — every Sunday 3 AM):
    0 3 * * 0 cd /path/to/repo && python scripts/sync_jurisdiction_api_data.py >> /var/log/jurisdiction_sync.log 2>&1

Windows Task Scheduler: same command, weekly trigger on Sunday 3:00 AM.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import asyncpg
import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logger = logging.getLogger(__name__)

DEFAULT_CONCURRENCY = 40
MAX_RETRIES = 3
JURISDICTION_DATA_VERSION = 1

SQL_FETCH_ACTIVE_STATES = """
SELECT code AS state_code, name AS state_name
FROM configuration.states
WHERE is_active = TRUE
  AND ($1::text IS NULL OR code = $1)
ORDER BY name
"""

SQL_FETCH_EXISTING = """
SELECT id, api_response, jurisdiction_data
FROM integration.jurisdiction_api_data
WHERE state_code = $1
"""

SQL_UPSERT_DATA = """
INSERT INTO integration.jurisdiction_api_data (state, state_code, api_response, jurisdiction_data)
VALUES ($1, $2, $3::jsonb, $4::jsonb)
ON CONFLICT (state_code) DO UPDATE SET
  state = EXCLUDED.state,
  api_response = EXCLUDED.api_response,
  jurisdiction_data = EXCLUDED.jurisdiction_data,
  updated_at = CURRENT_TIMESTAMP
RETURNING id
"""

SQL_INSERT_SYNC_LOG = """
INSERT INTO integration.jurisdiction_api_sync_logs (
  jurisdiction_api_data_id, state, state_code, sync_status,
  previous_response, new_response, changes_detected, error_message
) VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb, $7::jsonb, $8)
"""


# ---------------------------------------------------------------------------
# Pure helpers (testable without HTTP/DB)
# ---------------------------------------------------------------------------


def extract_items(response: Any) -> List[Dict[str, Any]]:
    if isinstance(response, list):
        return [row for row in response if isinstance(row, dict)]
    if isinstance(response, dict):
        items = response.get("items")
        if isinstance(items, list):
            return [row for row in items if isinstance(row, dict)]
    return []


def extract_link(item: Dict[str, Any], key: str) -> Optional[str]:
    links = item.get("link") or {}
    if not isinstance(links, dict):
        return None
    node = links.get(key) or {}
    if isinstance(node, dict):
        url = node.get("link") or node.get("href") or node.get("url")
        return str(url).strip() if url else None
    if isinstance(node, str) and node.strip():
        return node.strip()
    return None


def parse_court_item(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "code": str(item.get("code") or "").strip(),
        "name": str(item.get("name") or "").strip(),
        "case_category_codes_url": extract_link(item, "case_category_codes"),
        "_raw": item,
    }


def parse_category_item(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "code": str(item.get("code") or "").strip(),
        "name": str(item.get("name") or "").strip(),
        "ecf_case_type": item.get("ecf_case_type"),
        "case_type_codes_url": extract_link(item, "case_type_codes"),
        "_raw": item,
    }


def parse_case_type_item(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "code": str(item.get("code") or "").strip(),
        "name": str(item.get("name") or "").strip(),
        "fee": item.get("fee"),
        "initial": item.get("initial"),
        "party_type_codes_url": extract_link(item, "party_type_codes"),
        "filing_codes_url": extract_link(item, "filing_codes"),
        "case_subtype_codes_url": extract_link(item, "case_subtype_codes"),
    }


def build_court_tree(
    court_item: Dict[str, Any],
    categories_response: Any,
    case_types_by_category_url: Dict[str, Any],
) -> Dict[str, Any]:
    """Build one court node from pre-fetched API responses (fixture-friendly)."""
    court = parse_court_item(court_item)
    categories: List[Dict[str, Any]] = []
    for cat_item in extract_items(categories_response):
        cat = parse_category_item(cat_item)
        type_url = cat.get("case_type_codes_url") or ""
        types_response = case_types_by_category_url.get(type_url, {})
        case_types = [
            parse_case_type_item(t) for t in extract_items(types_response)
        ]
        categories.append(
            {
                "code": cat["code"],
                "name": cat["name"],
                "ecf_case_type": cat.get("ecf_case_type"),
                "case_types": case_types,
            }
        )
    return {
        "code": court["code"],
        "name": court["name"],
        "categories": categories,
    }


def _count_tree_nodes(courts: List[Dict[str, Any]]) -> Tuple[int, int]:
    category_count = 0
    case_type_count = 0
    for court in courts:
        for category in court.get("categories") or []:
            category_count += 1
            case_type_count += len(category.get("case_types") or [])
    return category_count, case_type_count


def build_jurisdiction_data(
    state_code: str,
    state_name: str,
    courts: List[Dict[str, Any]],
    *,
    api_calls: int = 0,
    duration_seconds: float = 0.0,
    courts_skipped_no_categories: int = 0,
) -> Dict[str, Any]:
    category_count, case_type_count = _count_tree_nodes(courts)
    return {
        "version": JURISDICTION_DATA_VERSION,
        "state_code": state_code.upper(),
        "state_name": state_name,
        "court_system": "tyler",
        "synced_at": datetime.now(timezone.utc).isoformat(),
        "courts": courts,
        "stats": {
            "court_count": len(courts),
            "category_count": category_count,
            "case_type_count": case_type_count,
            "api_calls": api_calls,
            "duration_seconds": round(duration_seconds, 2),
            "courts_skipped_no_categories": courts_skipped_no_categories,
        },
    }


def _strip_volatile_fields(data: Dict[str, Any]) -> Dict[str, Any]:
    cleaned = copy.deepcopy(data)
    cleaned.pop("synced_at", None)
    stats = cleaned.get("stats")
    if isinstance(stats, dict):
        stats.pop("duration_seconds", None)
        stats.pop("api_calls", None)
    return cleaned


def compute_changes(
    old: Optional[Dict[str, Any]], new: Dict[str, Any]
) -> Dict[str, Any]:
    if not old:
        return {"initial_sync": True}

    old_cmp = _strip_volatile_fields(old)
    new_cmp = _strip_volatile_fields(new)
    if old_cmp == new_cmp:
        return {}

    def _index_courts(data: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        return {c["code"]: c for c in data.get("courts") or [] if c.get("code")}

    def _index_categories(court: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        return {
            c["code"]: c for c in court.get("categories") or [] if c.get("code")
        }

    def _index_case_types(category: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        return {
            t["code"]: t for t in category.get("case_types") or [] if t.get("code")
        }

    old_courts = _index_courts(old_cmp)
    new_courts = _index_courts(new_cmp)
    changes: Dict[str, Any] = {
        "courts_added": sorted(set(new_courts) - set(old_courts)),
        "courts_removed": sorted(set(old_courts) - set(new_courts)),
        "categories": {"added": [], "removed": [], "case_types": {"added": [], "removed": []}},
    }

    for code in sorted(set(old_courts) & set(new_courts)):
        old_cats = _index_categories(old_courts[code])
        new_cats = _index_categories(new_courts[code])
        for cat_code in set(new_cats) - set(old_cats):
            changes["categories"]["added"].append(
                {"court_code": code, "category_code": cat_code, "name": new_cats[cat_code].get("name")}
            )
        for cat_code in set(old_cats) - set(new_cats):
            changes["categories"]["removed"].append(
                {"court_code": code, "category_code": cat_code, "name": old_cats[cat_code].get("name")}
            )
        for cat_code in set(old_cats) & set(new_cats):
            old_types = _index_case_types(old_cats[cat_code])
            new_types = _index_case_types(new_cats[cat_code])
            for type_code in set(new_types) - set(old_types):
                changes["categories"]["case_types"]["added"].append(
                    {
                        "court_code": code,
                        "category_code": cat_code,
                        "case_type_code": type_code,
                        "name": new_types[type_code].get("name"),
                    }
                )
            for type_code in set(old_types) - set(new_types):
                changes["categories"]["case_types"]["removed"].append(
                    {
                        "court_code": code,
                        "category_code": cat_code,
                        "case_type_code": type_code,
                        "name": old_types[type_code].get("name"),
                    }
                )

    if (
        not changes["courts_added"]
        and not changes["courts_removed"]
        and not changes["categories"]["added"]
        and not changes["categories"]["removed"]
        and not changes["categories"]["case_types"]["added"]
        and not changes["categories"]["case_types"]["removed"]
    ):
        return {"metadata_changed_only": True}
    return changes


def courts_matching_case_type(
    jurisdiction_data: Dict[str, Any], query: str
) -> List[Dict[str, Any]]:
    """Find courts that support a case type matching query (Phase 2 preview)."""
    needle = (query or "").strip().lower()
    if not needle:
        return []
    matches: List[Dict[str, Any]] = []
    for court in jurisdiction_data.get("courts") or []:
        matched_types: List[Dict[str, Any]] = []
        matched_categories: List[Dict[str, Any]] = []
        for category in court.get("categories") or []:
            cat_hits = [
                ct
                for ct in category.get("case_types") or []
                if needle in str(ct.get("name") or "").lower()
            ]
            if cat_hits:
                matched_categories.append(
                    {
                        "code": category.get("code"),
                        "name": category.get("name"),
                        "case_types": cat_hits,
                    }
                )
                matched_types.extend(cat_hits)
        if matched_types:
            matches.append(
                {
                    "code": court.get("code"),
                    "name": court.get("name"),
                    "categories": matched_categories,
                    "matched_case_types": matched_types,
                }
            )
    return matches


def hash_json(data: Any) -> str:
    payload = json.dumps(data, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _jsonb(value: Any) -> str:
    return json.dumps(value, default=str)


# ---------------------------------------------------------------------------
# HTTP + async crawl
# ---------------------------------------------------------------------------


class ApiClient:
    def __init__(
        self,
        base_url: str,
        client_token: str,
        auth_token: str,
        *,
        timeout: float = 30.0,
        concurrency: int = DEFAULT_CONCURRENCY,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.client_token = client_token
        self.auth_token = auth_token
        self.timeout = timeout
        self.semaphore = asyncio.Semaphore(concurrency)
        self._client: Optional[httpx.AsyncClient] = None
        self.api_calls = 0

    def _headers(self) -> Dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.client_token:
            headers["clienttoken"] = self.client_token
        if self.auth_token:
            headers["authtoken"] = self.auth_token
        return headers

    async def __aenter__(self) -> "ApiClient":
        self._client = httpx.AsyncClient(timeout=self.timeout)
        return self

    async def __aexit__(self, *args: Any) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def fetch_json(self, url: str) -> Any:
        assert self._client is not None
        full_url = url if url.startswith("http") else f"{self.base_url}{url}"
        delay = 1.0
        for attempt in range(1, MAX_RETRIES + 1):
            async with self.semaphore:
                self.api_calls += 1
                response = await self._client.get(full_url, headers=self._headers())
            if response.status_code == 429 and attempt < MAX_RETRIES:
                await asyncio.sleep(delay)
                delay *= 2
                continue
            response.raise_for_status()
            return response.json()
        raise RuntimeError(f"API failed after retries: {full_url}")


async def fetch_jurisdiction_codes(
    api: ApiClient, state_code: str
) -> Dict[str, Any]:
    state = state_code.strip().lower()
    url = (
        f"{api.base_url}/v2/{state}/code/jurisdiction_codes"
        f"?is_initial=true&court_system=tyler"
    )
    result = await api.fetch_json(url)
    if isinstance(result, dict):
        return result
    return {"items": result}


async def crawl_state(
    api: ApiClient,
    state_code: str,
    state_name: str,
    api_response: Dict[str, Any],
) -> Dict[str, Any]:
    started = time.monotonic()
    calls_before = api.api_calls
    courts_raw = extract_items(api_response)
    courts_skipped = 0
    court_trees: List[Dict[str, Any]] = []

    async def _fetch_categories(court: Dict[str, Any]) -> Tuple[Dict[str, Any], Any]:
        url = extract_link(court, "case_category_codes")
        if not url:
            return court, {"items": []}
        return court, await api.fetch_json(url)

    category_results = await asyncio.gather(
        *[_fetch_categories(c) for c in courts_raw],
        return_exceptions=True,
    )

    category_tasks: List[Tuple[Dict[str, Any], Any]] = []
    for result in category_results:
        if isinstance(result, Exception):
            logger.warning("Category fetch failed: %s", result)
            continue
        category_tasks.append(result)

    async def _fetch_types_for_court(
        court: Dict[str, Any], categories_response: Any
    ) -> Dict[str, Any]:
        nonlocal courts_skipped
        categories = extract_items(categories_response)
        if not categories and not extract_link(court, "case_category_codes"):
            courts_skipped += 1
        case_types_by_url: Dict[str, Any] = {}

        async def _fetch_types(cat: Dict[str, Any]) -> Tuple[str, Any]:
            url = extract_link(cat, "case_type_codes") or ""
            if not url:
                return url, {"items": []}
            return url, await api.fetch_json(url)

        type_results = await asyncio.gather(
            *[_fetch_types(c) for c in categories],
            return_exceptions=True,
        )
        for tr in type_results:
            if isinstance(tr, Exception):
                logger.warning("Case type fetch failed: %s", tr)
                continue
            url, resp = tr
            if url:
                case_types_by_url[url] = resp
        return build_court_tree(court, categories_response, case_types_by_url)

    tree_results = await asyncio.gather(
        *[
            _fetch_types_for_court(court, cats)
            for court, cats in category_tasks
        ],
        return_exceptions=True,
    )
    for tree in tree_results:
        if isinstance(tree, Exception):
            logger.warning("Court tree build failed: %s", tree)
            continue
        court_trees.append(tree)

    duration = time.monotonic() - started
    return build_jurisdiction_data(
        state_code,
        state_name,
        court_trees,
        api_calls=api.api_calls - calls_before,
        duration_seconds=duration,
        courts_skipped_no_categories=courts_skipped,
    )


# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------


async def create_pool() -> asyncpg.Pool:
    host = os.environ.get("RDS_HOST") or os.environ.get("rds_host")
    port = int(os.environ.get("RDS_PORT") or os.environ.get("rds_port") or 5432)
    database = os.environ.get("RDS_DATABASE") or os.environ.get("rds_database")
    user = os.environ.get("RDS_USERNAME") or os.environ.get("rds_username")
    password = os.environ.get("RDS_PASSWORD") or os.environ.get("rds_password")
    if not all([host, database, user, password]):
        raise RuntimeError(
            "Set RDS_HOST, RDS_DATABASE, RDS_USERNAME, RDS_PASSWORD in environment"
        )
    return await asyncpg.create_pool(
        host=host,
        port=port,
        database=database,
        user=user,
        password=password,
        min_size=1,
        max_size=4,
    )


async def fetch_active_states(
    pool: asyncpg.Pool, state_filter: Optional[str]
) -> List[Dict[str, str]]:
    code = state_filter.strip().upper() if state_filter else None
    async with pool.acquire() as conn:
        rows = await conn.fetch(SQL_FETCH_ACTIVE_STATES, code)
    return [{"state_code": r["state_code"], "state_name": r["state_name"]} for r in rows]


async def sync_state(
    pool: asyncpg.Pool,
    api: ApiClient,
    state_code: str,
    state_name: str,
    *,
    dry_run: bool = False,
    force: bool = False,
) -> str:
    async with pool.acquire() as conn:
        existing = await conn.fetchrow(SQL_FETCH_EXISTING, state_code.upper())
    existing_id = existing["id"] if existing else None
    prev_api = existing["api_response"] if existing else None
    prev_data = existing["jurisdiction_data"] if existing else None
    if isinstance(prev_api, str):
        prev_api = json.loads(prev_api)
    if isinstance(prev_data, str):
        prev_data = json.loads(prev_data)

    api_response = await fetch_jurisdiction_codes(api, state_code)
    if (
        not force
        and prev_api is not None
        and hash_json(prev_api) == hash_json(api_response)
    ):
        changes: Dict[str, Any] = {}
        status = "NO_CHANGES"
        jurisdiction_data = prev_data or {}
        print(f"  {state_code}: NO_CHANGES (jurisdiction_codes unchanged)")
        if dry_run:
            return status
        async with pool.acquire() as conn:
            await conn.execute(
                SQL_INSERT_SYNC_LOG,
                existing_id,
                state_name,
                state_code.upper(),
                status,
                _jsonb(prev_data),
                _jsonb(jurisdiction_data),
                _jsonb(changes),
                None,
            )
        return status

    jurisdiction_data = await crawl_state(
        api, state_code, state_name, api_response
    )
    changes = compute_changes(prev_data if isinstance(prev_data, dict) else None, jurisdiction_data)
    if not changes and prev_data:
        status = "NO_CHANGES"
    else:
        status = "SUCCESS"

    stats = jurisdiction_data.get("stats") or {}
    print(
        f"  {state_code}: {status} — courts={stats.get('court_count')} "
        f"categories={stats.get('category_count')} "
        f"case_types={stats.get('case_type_count')} "
        f"api_calls={stats.get('api_calls')} "
        f"duration={stats.get('duration_seconds')}s"
    )

    if dry_run:
        return status

    async with pool.acquire() as conn:
        if status == "SUCCESS":
            row = await conn.fetchrow(
                SQL_UPSERT_DATA,
                state_name,
                state_code.upper(),
                _jsonb(api_response),
                _jsonb(jurisdiction_data),
            )
            existing_id = row["id"]
        await conn.execute(
            SQL_INSERT_SYNC_LOG,
            existing_id,
            state_name,
            state_code.upper(),
            status,
            _jsonb(prev_data),
            _jsonb(jurisdiction_data),
            _jsonb(changes),
            None,
        )
    return status


async def run_sync(
    *,
    state_filter: Optional[str],
    dry_run: bool,
    force: bool,
    concurrency: int,
) -> int:
    base_url = os.environ.get("USLEGALPRO_API_BASE_URL", "").strip()
    client_token = os.environ.get("USLEGALPRO_CLIENT_TOKEN", "").strip()
    auth_token = os.environ.get("USLEGALPRO_AUTH_TOKEN", "").strip()
    if not base_url or not client_token:
        raise RuntimeError(
            "Set USLEGALPRO_API_BASE_URL and USLEGALPRO_CLIENT_TOKEN in environment"
        )

    pool = await create_pool()
    failed = 0
    try:
        states = await fetch_active_states(pool, state_filter)
        if not states:
            print("No active states found.")
            return 1
        print(f"Syncing {len(states)} state(s)...")
        async with ApiClient(
            base_url, client_token, auth_token, concurrency=concurrency
        ) as api:
            for row in states:
                code = row["state_code"]
                name = row["state_name"]
                try:
                    status = await sync_state(
                        pool,
                        api,
                        code,
                        name,
                        dry_run=dry_run,
                        force=force,
                    )
                    if status == "FAILED":
                        failed += 1
                except Exception as exc:  # noqa: BLE001
                    failed += 1
                    print(f"  {code}: FAILED — {exc}")
                    if not dry_run:
                        async with pool.acquire() as conn:
                            existing = await conn.fetchrow(
                                SQL_FETCH_EXISTING, code.upper()
                            )
                            await conn.execute(
                                SQL_INSERT_SYNC_LOG,
                                existing["id"] if existing else None,
                                name,
                                code.upper(),
                                "FAILED",
                                _jsonb(existing["jurisdiction_data"] if existing else None),
                                None,
                                _jsonb({}),
                                str(exc),
                            )
    finally:
        await pool.close()
    return 1 if failed else 0


def load_env(env_file: Optional[str]) -> None:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    if env_file:
        path = Path(env_file)
        if not path.is_absolute():
            path = ROOT / path
        if path.is_file():
            load_dotenv(path, override=True)
            print(f"Loaded env from {path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sync Tyler jurisdiction codes into integration.jurisdiction_api_data"
    )
    parser.add_argument("--state", help="Limit sync to one state code (e.g. TX)")
    parser.add_argument("--env-file", help="Optional .env file path")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and build data without writing to DB",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-crawl even when jurisdiction_codes response is unchanged",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=DEFAULT_CONCURRENCY,
        help=f"Max parallel API calls (default {DEFAULT_CONCURRENCY})",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    load_env(args.env_file)
    exit_code = asyncio.run(
        run_sync(
            state_filter=args.state,
            dry_run=args.dry_run,
            force=args.force,
            concurrency=args.concurrency,
        )
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
