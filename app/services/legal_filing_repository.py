"""
RDS data access for the legal filing chatbot.

Parameterized SQL for asyncpg ($1, $2, …) lives in ``app/config/sql_queries/``.
Selection options (states, case types, jurisdictions, workflow user-detail fields,
document templates) are loaded from RDS only.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from app.adapters.data_sources.AIM_rds import RDSRepository, RDSQueryError
from app.utils.sql_queries import get_sql

logger = logging.getLogger(__name__)

_STATEMENT_TIMEOUT_MARKERS = ("statement timeout", "canceling statement", "query_canceled")


def _is_statement_timeout(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in _STATEMENT_TIMEOUT_MARKERS)

# ---------------------------------------------------------------------------
# Chat retrieval order (queries wired into the filing chatbot)
# ---------------------------------------------------------------------------
CHAT_RETRIEVAL_ORDER: tuple[str, ...] = (
    "states",
    "case_types_and_subtypes",
    "jurisdictions",
    "workflow_questions",
    "document_templates_for_workflow",
    "document_template_s3",
    "case_by_number",
    "search_cases_by_party",
    "search_cases_by_filing_date",
    "document_templates_for_case",
)

# ---------------------------------------------------------------------------
# SQL keys in app/config/sql_queries/
# ---------------------------------------------------------------------------
SQL_FETCH_STATES = get_sql("configuration.fetch_states")
SQL_FETCH_CASE_TYPES = get_sql("configuration.fetch_case_types")
SQL_FETCH_JURISDICTIONS = get_sql("configuration.fetch_jurisdictions")
SQL_FETCH_USER_DETAILS_BY_CASE_TYPE = get_sql(
    "legal_filing.fetch_user_details_by_case_type"
)
SQL_FETCH_DOCUMENT_TEMPLATES_FOR_WORKFLOW = get_sql(
    "legal_filing.fetch_document_templates_for_workflow"
)
SQL_FETCH_DOCUMENT_TEMPLATE = get_sql("legal_filing.fetch_document_template")
SQL_FETCH_USER_DETAILS_BY_CASE_NUMBER = get_sql(
    "legal_filing.fetch_user_details_by_case_number"
)
SQL_FETCH_AVAILABLE_COURT_FILES = get_sql("legal_filing.fetch_available_court_files")
SQL_SEARCH_BY_PARTY_NAMES = get_sql("legal_filing.search_by_party_names")
SQL_SEARCH_BY_FILING_DATE = get_sql("legal_filing.search_by_filing_date")
SQL_FETCH_USER_DETAILS_BY_PARTY = get_sql("legal_filing.fetch_user_details_by_party")
SQL_FETCH_COUNTIES_BY_STATE = get_sql("configuration.fetch_counties_by_state")
SQL_FETCH_WORKFLOWS_BY_JURISDICTION = get_sql(
    "configuration.fetch_workflows_by_jurisdiction"
)
SQL_FETCH_ACTIVE_WORKFLOWS = get_sql("configuration.fetch_active_workflows")
SQL_FETCH_WORKFLOW_QUESTIONS = get_sql("configuration.fetch_workflow_questions")
SQL_FETCH_DOCUMENT_RULES_BY_WORKFLOW = get_sql(
    "configuration.fetch_document_rules_by_workflow"
)
SQL_FETCH_ACTIVE_DOCUMENT_TEMPLATES = get_sql(
    "legal_filing.fetch_active_document_templates_with_version"
)
SQL_FETCH_LATEST_TEMPLATE_VERSION = get_sql(
    "legal_filing.fetch_latest_template_version"
)
SQL_FETCH_JURISDICTION_API_DATA = get_sql(
    "legal_filing.fetch_jurisdiction_api_data_by_state"
)
SQL_LIST_JURISDICTION_API_STATE_CODES = get_sql(
    "legal_filing.list_jurisdiction_api_state_codes"
)


def _basename_from_s3_key(s3_key: Optional[str]) -> str:
    if not s3_key:
        return ""
    key = str(s3_key).replace("\\", "/")
    if key.startswith("s3://"):
        without = key[5:]
        parts = without.split("/", 1)
        key = parts[1] if len(parts) == 2 else parts[0]
    return os.path.basename(key)


def template_format_from_path(file_name: str, s3_key: Optional[str] = None) -> str:
    """Return template output kind: ftl, pdf, or docx (prefer S3 key from DB)."""
    for candidate in (_basename_from_s3_key(s3_key), file_name):
        if not candidate:
            continue
        lower = candidate.lower()
        if lower.endswith(".ftl"):
            return "ftl"
        if lower.endswith(".docx") or lower.endswith(".doc"):
            return "docx"
        if lower.endswith(".pdf"):
            return "pdf"
    return "pdf"


def _normalize_template_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    templates: List[Dict[str, Any]] = []
    for row in rows:
        code = str(row.get("template_code") or row.get("document_type") or "")
        name = str(row.get("template_name") or code or _document_label(row))
        s3_key = row.get("s3_key") or row.get("template_s3_key")
        file_name = (
            row.get("file_name")
            or _basename_from_s3_key(s3_key)
            or f"{code or name or 'template'}.pdf"
        )
        templates.append({
            "template_id": row.get("template_id"),
            "template_code": code,
            "template_name": name,
            "is_required": bool(
                True if row.get("is_required") is None else row.get("is_required")
            ),
            "s3_bucket": row.get("s3_bucket"),
            "s3_key": s3_key,
            "file_name": file_name,
            "template_format": template_format_from_path(str(file_name), s3_key),
            "rule_condition": row.get("rule_condition") or row.get("condition"),
            "template_version": row.get("template_version"),
        })
    return templates


def build_workflow_schema(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Normalize workflow question rows into ordered schema dicts."""
    questions: List[Dict[str, Any]] = []
    for row in rows:
        field_name = row.get("field_name") or row.get("code") or row.get("name")
        if not field_name:
            continue
        questions.append({
            "field_name": str(field_name),
            "field_label": row.get("field_label") or row.get("question_text") or field_name,
            "question_type": row.get("question_type") or "TEXT",
            "required": bool(row.get("required") if row.get("required") is not None else row.get("is_required", True)),
            "sort_order": int(row.get("sort_order") or 0),
            "placeholder": row.get("placeholder"),
            "help_text": row.get("help_text"),
            "validation_rules": row.get("validation_rules"),
            "visibility_condition": row.get("visibility_condition"),
            "workflow_id": row.get("workflow_id"),
            "question_id": row.get("question_id"),
        })
    questions.sort(key=lambda q: (q["sort_order"], q["field_name"]))
    return questions


def _document_label(row: Dict[str, Any]) -> str:
    return str(
        row.get("template_code")
        or row.get("document_type")
        or row.get("template_name")
        or row.get("display_name")
        or row
    )


class LegalFilingRepository:
    """RDS-backed repository for the filing chatbot."""

    def __init__(self, rds: Optional[RDSRepository] = None):
        self.rds = rds

    async def _safe_fetch(self, query: str, *params: Any) -> List[Dict[str, Any]]:
        if self.rds is None:
            logger.debug("RDS not configured; returning empty result for query")
            return []
        try:
            return await self.rds.fetch(query, *params)
        except RDSQueryError as exc:
            logger.warning("RDS query failed: %s", exc)
            return []
        except Exception as exc:  # noqa: BLE001
            logger.warning("Unexpected RDS error: %s", exc)
            return []

    async def get_states(self) -> List[Dict[str, Any]]:
        """Fetch active states from RDS for selection options."""
        return await self._safe_fetch(SQL_FETCH_STATES)

    async def get_case_types(self) -> List[Dict[str, Any]]:
        """Fetch available case types and sub case types from RDS."""
        return await self._safe_fetch(SQL_FETCH_CASE_TYPES)

    async def get_jurisdictions(
        self, state_code: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Fetch active jurisdictions, optionally filtered by state code."""
        code = (state_code or "").strip().upper() if state_code else None
        return await self._safe_fetch(SQL_FETCH_JURISDICTIONS, code)

    async def get_user_detail_schema(
        self,
        case_type: str,
        sub_case_type: str,
        jurisdiction_code: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Fetch form fields / user details schema from active workflow questions."""
        rows = await self._safe_fetch(
            SQL_FETCH_USER_DETAILS_BY_CASE_TYPE,
            case_type,
            sub_case_type,
            jurisdiction_code,
        )
        if not rows:
            return {}
        if len(rows) == 1 and any(
            k in rows[0] for k in ("fields", "user_details", "schema")
        ):
            return (
                rows[0].get("fields")
                or rows[0].get("user_details")
                or rows[0].get("schema")
                or rows[0]
            )
        schema: Dict[str, Any] = {
            "case_type": case_type,
            "sub_case_type": sub_case_type,
        }
        if jurisdiction_code:
            schema["jurisdiction_code"] = jurisdiction_code
        for row in rows:
            name = row.get("field_name") or row.get("name")
            if name:
                schema[str(name)] = row.get("default_value") or ""
        return schema

    async def get_document_templates_for_workflow(
        self,
        case_type: str,
        sub_case_type: str,
        jurisdiction_code: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return full template rows (required flag, S3 keys) for a workflow."""
        rows = await self._safe_fetch(
            SQL_FETCH_DOCUMENT_TEMPLATES_FOR_WORKFLOW,
            case_type,
            sub_case_type,
            jurisdiction_code,
        )
        return _normalize_template_rows(rows)

    async def get_document_templates_by_workflow_id(
        self, workflow_id: str
    ) -> List[Dict[str, Any]]:
        """Return document templates linked to a workflow via document_rules."""
        rows = await self._safe_fetch(
            SQL_FETCH_DOCUMENT_RULES_BY_WORKFLOW, workflow_id
        )
        return _normalize_template_rows(rows)

    async def get_document_template(
        self,
        case_type: str,
        sub_case_type: str,
        document_type: str,
        jurisdiction_code: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Fetch document template S3 key for generation.

        Expected row keys: s3_key, template_name / template_code, s3_bucket
        """
        rows = await self._safe_fetch(
            SQL_FETCH_DOCUMENT_TEMPLATE,
            case_type,
            sub_case_type,
            document_type,
            jurisdiction_code,
        )
        if rows:
            row = rows[0]
            s3_key = row.get("s3_key") or row.get("template_s3_key") or row.get("s3_link")
            if s3_key:
                label = (
                    row.get("template_code")
                    or row.get("template_name")
                    or document_type
                )
                return {
                    "s3_key": s3_key,
                    "s3_bucket": row.get("s3_bucket"),
                    "file_name": row.get("file_name") or f"{label}.pdf",
                    "document_type": row.get("document_type") or document_type,
                    **row,
                }
        logger.warning(
            "No template found for %s/%s/%s — "
            "set DOCUMENT_TEMPLATE_S3_KEY in .env for local testing",
            case_type,
            sub_case_type,
            document_type,
        )
        return None

    async def get_user_details_by_case_number(
        self, case_number: str
    ) -> Optional[Dict[str, Any]]:
        rows = await self._safe_fetch(SQL_FETCH_USER_DETAILS_BY_CASE_NUMBER, case_number)
        if rows:
            return dict(rows[0])
        return None

    async def get_available_court_files(self, case_number: str) -> List[str]:
        rows = await self._safe_fetch(SQL_FETCH_AVAILABLE_COURT_FILES, case_number)
        return [_document_label(r) for r in rows]

    async def search_by_party_names(self, party_names: str) -> List[Dict[str, Any]]:
        pattern = f"%{party_names.strip()}%"
        return await self._safe_fetch(SQL_SEARCH_BY_PARTY_NAMES, pattern)

    async def search_by_filing_date(self, filing_date: str) -> List[Dict[str, Any]]:
        return await self._safe_fetch(SQL_SEARCH_BY_FILING_DATE, filing_date)

    async def get_counties(self, state_code: str) -> List[Dict[str, Any]]:
        code = (state_code or "").strip().upper() or None
        return await self._safe_fetch(SQL_FETCH_COUNTIES_BY_STATE, code)

    async def get_jurisdictions_for_county(
        self,
        state_code: Optional[str],
        county_name: Optional[str] = None,
        county_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        rows = await self.get_jurisdictions(state_code)
        if not county_name and not county_id:
            return rows
        filtered: List[Dict[str, Any]] = []
        county_norm = (county_name or "").strip().lower()
        for row in rows:
            if county_id and str(row.get("county_id")) == str(county_id):
                filtered.append(row)
                continue
            name = str(row.get("county_name") or "").strip().lower()
            if county_norm and (name == county_norm or county_norm in name or name in county_norm):
                filtered.append(row)
        return filtered

    async def get_workflows_for_jurisdiction(
        self, jurisdiction_code: str
    ) -> List[Dict[str, Any]]:
        return await self._safe_fetch(
            SQL_FETCH_WORKFLOWS_BY_JURISDICTION, jurisdiction_code
        )

    async def get_active_workflows(self) -> List[Dict[str, Any]]:
        """Fetch active workflow definitions before loading their questions."""
        return await self._safe_fetch(SQL_FETCH_ACTIVE_WORKFLOWS)

    async def get_workflow_questions(self, workflow_id: str) -> List[Dict[str, Any]]:
        rows = await self._safe_fetch(SQL_FETCH_WORKFLOW_QUESTIONS, workflow_id)
        return build_workflow_schema(rows)

    async def get_workflow_questions_by_case(
        self,
        case_type: str,
        sub_case_type: str,
        jurisdiction_code: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        rows = await self._safe_fetch(
            SQL_FETCH_USER_DETAILS_BY_CASE_TYPE,
            case_type,
            sub_case_type,
            jurisdiction_code,
        )
        return build_workflow_schema(rows)

    async def get_user_details_by_party_case(
        self, case_number: str
    ) -> Optional[Dict[str, Any]]:
        rows = await self._safe_fetch(SQL_FETCH_USER_DETAILS_BY_PARTY, case_number)
        if rows:
            return dict(rows[0])
        return await self.get_user_details_by_case_number(case_number)

    async def list_active_document_templates(self) -> List[Dict[str, Any]]:
        """Active document_templates joined to the latest template_versions row."""
        return await self._safe_fetch(SQL_FETCH_ACTIVE_DOCUMENT_TEMPLATES)

    async def get_latest_template_version(
        self, template_id: str
    ) -> Optional[Dict[str, Any]]:
        rows = await self._safe_fetch(SQL_FETCH_LATEST_TEMPLATE_VERSION, template_id)
        return dict(rows[0]) if rows else None

    async def list_jurisdiction_api_state_codes(self) -> List[str]:
        """Distinct state codes with cached jurisdiction API JSON in RDS."""
        rows = await self._safe_fetch(SQL_LIST_JURISDICTION_API_STATE_CODES)
        return [
            str(row.get("state_code") or "").strip().upper()
            for row in rows
            if str(row.get("state_code") or "").strip()
        ]

    async def get_jurisdiction_api_data(
        self,
        state_code: str,
        *,
        timeout_sec: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        """Latest integration.jurisdiction_api_data row for a state."""
        code = (state_code or "").strip()
        if not code:
            return None
        if self.rds is None:
            logger.debug("RDS not configured; skipping jurisdiction_api_data for %s", code)
            return None
        timeout = (
            timeout_sec
            if timeout_sec is not None
            else float(os.getenv("COURT_CATALOG_RDS_TIMEOUT_SEC", "90"))
        )
        try:
            return await self.rds.fetch_one_with_statement_timeout(
                SQL_FETCH_JURISDICTION_API_DATA,
                code,
                timeout_sec=timeout,
            )
        except RDSQueryError as exc:
            if _is_statement_timeout(exc):
                logger.warning(
                    "Jurisdiction API data query timed out after %.0fs for %s",
                    timeout,
                    code,
                )
            else:
                logger.warning("RDS query failed: %s", exc)
            return None
        except Exception as exc:  # noqa: BLE001
            logger.warning("Unexpected RDS error for jurisdiction_api_data: %s", exc)
            return None
