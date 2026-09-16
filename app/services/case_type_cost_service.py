"""Resolve filing authorization amount from case type costs via LLM matching."""

from __future__ import annotations

import json
import logging
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.adapters.llm.bedrock import Bedrock
from app.services.case_type_cost_repository import CaseTypeCostRepository

logger = logging.getLogger(__name__)

_COST_MATCH_PROMPT = """Match the user's selected case type to one configured filing cost.

User case context (from the filing session):
{user_case_context_json}

Configured costs (choose the best matching row):
{cost_rows_json}

Return:
- matched_case_type: the case_type string from the chosen cost row, or null if none fit
- amount: the cost as a decimal string with two places (e.g. "10.00"), or null if no match
- currency: ISO currency code from the matched row (default USD)
- confidence: high, medium, or low

Prefer state-specific rows over global rows (state_code null) when both match.
If no row is a reasonable match, set matched_case_type and amount to null and confidence to low.
"""


class CaseTypeCostMatchOutput(BaseModel):
    matched_case_type: Optional[str] = Field(default=None)
    amount: Optional[str] = Field(default=None)
    currency: str = "USD"
    confidence: str = "low"


class CaseTypeCostResolutionError(RuntimeError):
    """Could not resolve a filing cost for the session case type."""


class CaseTypeCostService:
    """Load cost rows and resolve amount for payment authorization."""

    def __init__(
        self,
        *,
        repo: Optional[CaseTypeCostRepository] = None,
        bedrock: Optional[Bedrock] = None,
    ) -> None:
        self.repo = repo or CaseTypeCostRepository()
        self.bedrock = bedrock

    @staticmethod
    def _case_context(selections: Dict[str, Any]) -> Dict[str, Any]:
        keys = (
            "case_type",
            "case_type_name",
            "case_type_code",
            "sub_case_type",
            "case_category_name",
            "state_code",
            "jurisdiction_name",
            "county_name",
        )
        return {
            key: selections.get(key)
            for key in keys
            if selections.get(key) not in (None, "", [], {})
        }

    @staticmethod
    def _serialize_cost_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for row in rows:
            cost = row.get("cost")
            try:
                amount = f"{Decimal(str(cost)):.2f}"
            except (InvalidOperation, TypeError, ValueError):
                continue
            out.append(
                {
                    "case_type": str(row.get("case_type") or "").strip(),
                    "state_code": row.get("state_code"),
                    "cost": amount,
                    "currency": str(row.get("currency") or "USD").strip().upper(),
                }
            )
        return out

    async def _match_with_llm(
        self,
        case_context: Dict[str, Any],
        cost_rows: List[Dict[str, Any]],
    ) -> CaseTypeCostMatchOutput:
        if self.bedrock is None:
            raise CaseTypeCostResolutionError("LLM is not available to match filing cost.")

        prompt = _COST_MATCH_PROMPT.format(
            user_case_context_json=json.dumps(case_context, default=str, indent=2),
            cost_rows_json=json.dumps(cost_rows, default=str, indent=2),
        )
        try:
            return await self.bedrock.invoke_structured_prompt(prompt, CaseTypeCostMatchOutput)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Structured cost match failed: %s", exc)

        body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 512,
            "temperature": 0,
            "messages": [{"role": "user", "content": prompt}],
        }
        from app.agents.utils.json_utils import parse_llm_json

        raw = await self.bedrock.invoke_prompt_with_timeout(body)
        parsed = parse_llm_json(raw)
        return CaseTypeCostMatchOutput.model_validate(parsed)

    @staticmethod
    def _normalize_amount(raw: Any) -> str:
        try:
            return f"{Decimal(str(raw)):.2f}"
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise CaseTypeCostResolutionError("Matched cost amount is invalid.") from exc

    async def resolve_amount(
        self,
        selections: Dict[str, Any],
    ) -> Dict[str, Any]:
        state_code = str(selections.get("state_code") or "").strip() or None
        rows = await self.repo.list_active(state_code=state_code)
        cost_rows = self._serialize_cost_rows(rows)
        if not cost_rows:
            raise CaseTypeCostResolutionError(
                "No filing costs are configured for this case type. "
                "Please add a cost using the case type costs API."
            )

        case_context = self._case_context(selections)
        match = await self._match_with_llm(case_context, cost_rows)
        if match.confidence == "low" or not match.amount or not match.matched_case_type:
            raise CaseTypeCostResolutionError(
                "I could not find a configured filing cost for your case type."
            )

        amount = self._normalize_amount(match.amount)
        return {
            "matched_case_type": match.matched_case_type,
            "amount": amount,
            "currency": str(match.currency or "USD").strip().upper(),
            "confidence": match.confidence,
            "case_context": case_context,
        }
