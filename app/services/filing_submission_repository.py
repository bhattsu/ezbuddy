"""Persistence helpers for workflow.jurisdiction_submissions."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Iterable, Optional

from app.adapters.data_sources.AIM_rds import RDSRepository, RDSQueryError
from app.utils.sql_queries import get_sql

logger = logging.getLogger(__name__)

SQL_FETCH_SUBMISSION_BY_SESSION = get_sql("workflow.fetch_submission_by_session")
SQL_FETCH_SUBMISSION_BY_REFERENCE = get_sql("workflow.fetch_submission_by_reference")
SQL_INSERT_SUBMISSION = get_sql("workflow.insert_submission")
SQL_UPDATE_SUBMISSION_STATUS = get_sql("workflow.update_submission_status")
SQL_FETCH_ACTIVE_PROVIDERS = get_sql("integration.fetch_active_providers")

STATUS_MAP = {
    "accepted": "ACCEPTED",
    "accept": "ACCEPTED",
    "rejected": "REJECTED",
    "reject": "REJECTED",
    "under_review": "UNDER_REVIEW",
    "under review": "UNDER_REVIEW",
    "processing": "PROCESSING",
    "submitted": "PROCESSING",
    "pending": "PENDING",
    "cancelled": "CANCELLED",
    "canceled": "CANCELLED",
}


def normalize_submission_status(raw: str) -> str:
    value = str(raw or "").strip().lower()
    if not value:
        return "PENDING"
    if value in STATUS_MAP:
        return STATUS_MAP[value]
    upper = value.upper()
    if upper in {"PENDING", "PROCESSING", "ACCEPTED", "REJECTED", "UNDER_REVIEW", "CANCELLED"}:
        return upper
    return "PROCESSING"


class FilingSubmissionRepository:
    """RDS CRUD for jurisdiction submissions used by conversation orchestration."""

    def __init__(self, rds: Optional[RDSRepository] = None) -> None:
        self.rds = rds

    async def _safe_fetch(self, query: str, *params: Any) -> list[Dict[str, Any]]:
        if self.rds is None:
            return []
        try:
            return await self.rds.fetch(query, *params)
        except RDSQueryError as exc:
            logger.warning("Submission query failed: %s", exc)
            return []
        except Exception as exc:  # noqa: BLE001
            logger.warning("Unexpected submission DB error: %s", exc)
            return []

    async def _safe_fetch_one(self, query: str, *params: Any) -> Optional[Dict[str, Any]]:
        rows = await self._safe_fetch(query, *params)
        return rows[0] if rows else None

    async def latest_for_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        if not str(session_id or "").strip():
            return None
        return await self._safe_fetch_one(SQL_FETCH_SUBMISSION_BY_SESSION, session_id)

    async def by_reference(self, reference_number: str) -> Optional[Dict[str, Any]]:
        if not str(reference_number or "").strip():
            return None
        return await self._safe_fetch_one(SQL_FETCH_SUBMISSION_BY_REFERENCE, reference_number)

    async def resolve_provider_id(self, provider_types: Iterable[str] = ()) -> Optional[str]:
        tried = [str(item or "").strip() for item in provider_types if str(item or "").strip()]
        for provider_type in tried:
            rows = await self._safe_fetch(SQL_FETCH_ACTIVE_PROVIDERS, provider_type)
            if rows:
                return str(rows[0].get("provider_id") or "").strip() or None
        rows = await self._safe_fetch(SQL_FETCH_ACTIVE_PROVIDERS, None)
        if rows:
            return str(rows[0].get("provider_id") or "").strip() or None
        return None

    async def insert_submission(
        self,
        *,
        session_id: str,
        provider_id: str,
        reference_number: str,
        submission_status: str,
        response_message: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        if not (session_id and provider_id and reference_number):
            return None
        payload = json.dumps(response_message or {}, default=str)
        return await self._safe_fetch_one(
            SQL_INSERT_SUBMISSION,
            session_id,
            provider_id,
            reference_number,
            normalize_submission_status(submission_status),
            payload,
        )

    async def update_submission_status(
        self,
        *,
        submission_id: str,
        submission_status: str,
        response_message: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        if not submission_id:
            return None
        payload = json.dumps(response_message or {}, default=str)
        return await self._safe_fetch_one(
            SQL_UPDATE_SUBMISSION_STATUS,
            submission_id,
            normalize_submission_status(submission_status),
            payload,
        )

