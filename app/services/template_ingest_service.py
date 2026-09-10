"""Upload a court PDF template to S3 and insert configuration RDS rows."""

from __future__ import annotations

import logging
import re
import uuid
from datetime import date
from typing import Any, Dict, Optional

from app.adapters.data_sources.AIM_rds import RDSQueryError, RDSRepository
from app.api.schemas.template_ingest import DuplicateTemplateError, TemplateIngestError
from app.config.settings import settings
from app.utils.s3_utils import S3Manager
from app.utils.sql_queries import get_sql

logger = logging.getLogger(__name__)

SQL_FETCH_BY_CODE = get_sql("configuration.fetch_template_id_by_code")
SQL_INSERT_TEMPLATE = get_sql("configuration.insert_document_template")
SQL_INSERT_VERSION = get_sql("configuration.insert_template_version")

_PDF_MAGIC = b"%PDF"
_UNSAFE_PATH = re.compile(r"[\\/]+")
_CODE_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def templates_root_prefix() -> str:
    repo = str(settings.S3_DOCUMENTS_REPO_PREFIX or "documents-repo").strip("/")
    return f"{repo}/templates"


def build_template_s3_key(
    *,
    state: str,
    jurisdiction: str,
    code: str,
    file_name: str,
    object_id: Optional[str] = None,
    version: int = 1,
) -> str:
    """Match existing keys: templates/{STATE}/{JURISDICTION}/{CODE}/v1/{uuid}-{file}.pdf"""
    hex_id = (object_id or uuid.uuid4().hex).replace("-", "")
    safe_name = _sanitize_file_name(file_name)
    return (
        f"{templates_root_prefix()}/"
        f"{_sanitize_segment(state)}/"
        f"{_sanitize_segment(jurisdiction)}/"
        f"{_sanitize_segment(code)}/"
        f"v{int(version)}/"
        f"{hex_id}-{safe_name}"
    )


def _sanitize_segment(value: str) -> str:
    text = _UNSAFE_PATH.sub("", str(value or "").strip())
    if not text:
        raise TemplateIngestError("Folder or code value is empty.")
    return text


def _sanitize_file_name(file_name: str) -> str:
    name = str(file_name or "template.pdf").replace("\\", "/").rsplit("/", 1)[-1]
    name = name.strip() or "template.pdf"
    if not name.lower().endswith(".pdf"):
        name = f"{name}.pdf"
    return _CODE_SAFE.sub("_", name)


def _normalize_code(code: str) -> str:
    text = str(code or "").strip()
    if not text:
        raise TemplateIngestError("code is required.")
    if len(text) > 50:
        raise TemplateIngestError("code must be 50 characters or fewer.")
    return text


def _is_pdf(data: bytes) -> bool:
    return bool(data) and data.lstrip()[:4] == _PDF_MAGIC


def _row_json(row: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not row:
        return {}
    out: Dict[str, Any] = {}
    for key, value in row.items():
        if hasattr(value, "isoformat"):
            out[key] = value.isoformat()
        elif isinstance(value, uuid.UUID):
            out[key] = str(value)
        else:
            out[key] = value
    return out


class TemplateIngestService:
    def __init__(
        self,
        *,
        rds: RDSRepository,
        s3: Optional[S3Manager] = None,
    ) -> None:
        self.rds = rds
        self.s3 = s3 or S3Manager()

    def _bucket(self) -> str:
        bucket = str(settings.BUCKET_NAME or "").strip()
        if not bucket:
            raise TemplateIngestError("BUCKET_NAME is not configured.", 503)
        return bucket

    async def list_states(self) -> list[str]:
        prefix = templates_root_prefix()
        return await self.s3.list_common_prefixes(prefix, bucket=self._bucket())

    async def list_jurisdictions(self, state: str) -> list[str]:
        state_code = _sanitize_segment(state)
        states = await self.list_states()
        if state_code not in states:
            raise TemplateIngestError(
                f"State folder {state_code!r} is not under {templates_root_prefix()}/.",
                404,
            )
        prefix = f"{templates_root_prefix()}/{state_code}"
        return await self.s3.list_common_prefixes(prefix, bucket=self._bucket())

    async def list_folder_tree(self) -> Dict[str, list[str]]:
        """State folder → jurisdiction folders under documents-repo/templates/."""
        tree: Dict[str, list[str]] = {}
        for state in await self.list_states():
            prefix = f"{templates_root_prefix()}/{state}"
            tree[state] = await self.s3.list_common_prefixes(
                prefix, bucket=self._bucket()
            )
        return tree

    async def ingest(
        self,
        *,
        file_bytes: bytes,
        file_name: str,
        code: str,
        name: str,
        description: str = "",
        doc_type: str = "",
        state: str = "",
        jurisdiction: str = "",
        case_category: str = "",
        case_type: str = "",
        case_subtype: str = "",
        field_mapping: str = "",
        sample_input: str = "",
        effective_from: Optional[date] = None,
        effective_to: Optional[date] = None,
    ) -> Dict[str, Any]:
        if not _is_pdf(file_bytes):
            raise TemplateIngestError("Uploaded file must be a PDF.")
        template_code = _normalize_code(code)
        display_name = str(name or "").strip()
        if not display_name:
            raise TemplateIngestError("name is required.")

        state_code = _sanitize_segment(state)
        jurisdiction_code = _sanitize_segment(jurisdiction)
        jurisdictions = await self.list_jurisdictions(state_code)
        if jurisdiction_code not in jurisdictions:
            raise TemplateIngestError(
                f"Jurisdiction folder {jurisdiction_code!r} is not under "
                f"{templates_root_prefix()}/{state_code}/.",
                404,
            )

        existing = await self.rds.fetch_one(SQL_FETCH_BY_CODE, template_code)
        if existing:
            raise DuplicateTemplateError(
                template_code,
                str(existing.get("template_id") or existing.get("id") or ""),
            )

        template_id = uuid.uuid4()
        version_id = uuid.uuid4()
        object_id = uuid.uuid4().hex
        s3_key = build_template_s3_key(
            state=state_code,
            jurisdiction=jurisdiction_code,
            code=template_code,
            file_name=file_name,
            object_id=object_id,
            version=1,
        )
        bucket = self._bucket()
        uploaded = False
        try:
            await self.s3.upload_bytes_at_key(
                file_bytes,
                s3_key,
                bucket=bucket,
                content_type="application/pdf",
            )
            uploaded = True
            template_row = await self.rds.fetch_one(
                SQL_INSERT_TEMPLATE,
                str(template_id),
                template_code,
                display_name,
                description or None,
                doc_type or None,
                state_code,
                jurisdiction_code,
                case_category or None,
                case_type or None,
                case_subtype or None,
                field_mapping or None,
                sample_input or None,
            )
            version_row = await self.rds.fetch_one(
                SQL_INSERT_VERSION,
                str(version_id),
                str(template_id),
                1,
                bucket,
                s3_key,
                effective_from,
                effective_to,
            )
        except DuplicateTemplateError:
            raise
        except RDSQueryError as exc:
            if uploaded:
                await self.s3.delete_file(s3_key, bucket=bucket)
            if _is_unique_violation(exc):
                existing = await self.rds.fetch_one(SQL_FETCH_BY_CODE, template_code)
                raise DuplicateTemplateError(
                    template_code,
                    str((existing or {}).get("template_id") or "") or None,
                ) from exc
            raise TemplateIngestError(str(exc), 502) from exc
        except Exception:
            if uploaded:
                await self.s3.delete_file(s3_key, bucket=bucket)
            raise

        return {
            "template_id": template_id,
            "version_id": version_id,
            "version": 1,
            "s3_bucket": bucket,
            "s3_key": s3_key,
            "s3_uri": f"s3://{bucket}/{s3_key}",
            "document_template": _row_json(template_row) or {"id": str(template_id)},
            "template_version": _row_json(version_row)
            or {"id": str(version_id), "template_id": str(template_id)},
        }


def _is_unique_violation(exc: BaseException) -> bool:
    seen: set[int] = set()
    current: Optional[BaseException] = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        sqlstate = getattr(current, "sqlstate", None)
        name = type(current).__name__
        text = str(current).lower()
        if sqlstate == "23505" or name == "UniqueViolation":
            return True
        if "duplicate key" in text or "unique constraint" in text:
            return True
        current = current.__cause__ or getattr(current, "__context__", None)
    return False
