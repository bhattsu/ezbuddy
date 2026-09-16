"""Create S3 template folders and configuration.states rows."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from app.adapters.data_sources.AIM_rds import RDSQueryError, RDSRepository
from app.config.settings import settings
from app.services.template_ingest_service import templates_root_prefix
from app.utils.s3_utils import S3Manager
from app.utils.sql_queries import get_sql

logger = logging.getLogger(__name__)

SQL_FETCH_STATE = get_sql("configuration.fetch_state_by_code")
SQL_UPSERT_STATE = get_sql("configuration.upsert_state")


class FolderSetupError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


class FolderSetupService:
    def __init__(
        self,
        *,
        rds: Optional[RDSRepository] = None,
        s3: Optional[S3Manager] = None,
    ) -> None:
        self.rds = rds
        self.s3 = s3 or S3Manager()

    def _bucket(self) -> str:
        bucket = str(settings.BUCKET_NAME or "").strip()
        if not bucket:
            raise FolderSetupError("BUCKET_NAME is not configured.", 503)
        return bucket

    async def create_state(self, *, code: str, name: str) -> Dict[str, Any]:
        if self.rds is None:
            raise FolderSetupError("Database is not configured.", 503)

        state_code = str(code or "").strip().upper()
        state_name = str(name or "").strip()
        if not state_code or not state_name:
            raise FolderSetupError("code and name are required.")

        bucket = self._bucket()
        folder_prefix = f"{templates_root_prefix()}/{state_code}"

        existing_db = await self.rds.fetch_one(SQL_FETCH_STATE, state_code)
        db_created = existing_db is None

        try:
            row = await self.rds.fetch_one(SQL_UPSERT_STATE, state_code, state_name)
        except RDSQueryError as exc:
            logger.exception("State upsert failed for code=%s", state_code)
            raise FolderSetupError(f"Database error: {exc}", 500) from exc
        if not row:
            raise FolderSetupError("State upsert returned no row.", 500)

        marker_key, s3_created = await self.s3.ensure_folder_marker(
            folder_prefix,
            bucket=bucket,
        )

        return {
            "id": row["id"],
            "code": row["code"],
            "name": row["name"],
            "is_active": row.get("is_active", True),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "s3_bucket": bucket,
            "s3_prefix": f"{folder_prefix}/",
            "s3_marker_key": marker_key,
            "s3_folder_created": s3_created,
            "db_created": db_created,
        }

    async def create_jurisdiction(
        self,
        *,
        state: str,
        jurisdiction_name: str,
    ) -> Dict[str, Any]:
        state_code = str(state or "").strip().upper()
        jurisdiction = str(jurisdiction_name or "").strip()
        if not state_code or not jurisdiction:
            raise FolderSetupError("state and jurisdiction_name are required.")
        if "/" in jurisdiction or "\\" in jurisdiction:
            raise FolderSetupError("jurisdiction_name must not contain path separators.")

        bucket = self._bucket()
        root = templates_root_prefix()
        state_prefix = f"{root}/{state_code}"

        try:
            state_folders = await self.s3.list_common_prefixes(root, bucket=bucket)
        except RuntimeError as exc:
            raise FolderSetupError(str(exc), 502) from exc

        state_found = state_code in state_folders
        if not state_found:
            raise FolderSetupError(
                f"State folder {state_code!r} was not found under {root}/. "
                "Create the state folder first.",
                404,
            )

        folder_prefix = f"{state_prefix}/{jurisdiction}"
        marker_key, s3_created = await self.s3.ensure_folder_marker(
            folder_prefix,
            bucket=bucket,
        )

        return {
            "state": state_code,
            "jurisdiction_name": jurisdiction,
            "s3_bucket": bucket,
            "s3_prefix": f"{folder_prefix}/",
            "s3_marker_key": marker_key,
            "s3_folder_created": s3_created,
            "state_found_in_s3": state_found,
        }
