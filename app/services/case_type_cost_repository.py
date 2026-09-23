"""RDS access for ``configuration.case_type_filing_costs``."""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Dict, List, Optional

from app.adapters.data_sources.AIM_rds import RDSRepository, RDSQueryError
from app.utils.sql_queries import get_sql

logger = logging.getLogger(__name__)

SQL_LIST = get_sql("configuration.list_case_type_filing_costs")
SQL_UPSERT = get_sql("configuration.upsert_case_type_filing_cost")


class CaseTypeCostRepository:
    """Load and upsert configurable filing costs by case type."""

    def __init__(self, rds: Optional[RDSRepository] = None) -> None:
        self.rds = rds

    async def list_active(self, *, state_code: Optional[str] = None) -> List[Dict[str, Any]]:
        if self.rds is None:
            return []
        try:
            code = str(state_code or "").strip() or None
            rows = await self.rds.fetch(SQL_LIST, code)
            return [dict(row) for row in rows]
        except RDSQueryError as exc:
            logger.warning("Case type cost list failed: %s", exc)
            return []

    async def upsert(
        self,
        *,
        case_type: str,
        state_code: Optional[str],
        cost: Decimal,
        currency: str = "USD",
        is_active: bool = True,
    ) -> Optional[Dict[str, Any]]:
        if self.rds is None:
            logger.error("RDS not configured; cannot upsert case type cost")
            return None
        try:
            rows = await self.rds.fetch(
                SQL_UPSERT,
                str(case_type or "").strip(),
                str(state_code or "").strip(),
                cost,
                str(currency or "USD").strip().upper(),
                bool(is_active),
            )
            return dict(rows[0]) if rows else None
        except RDSQueryError as exc:
            logger.warning("Case type cost upsert failed: %s", exc)
            raise
