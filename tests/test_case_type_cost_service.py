"""LLM-backed case type cost resolution."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.case_type_cost_service import CaseTypeCostService


class _Repo:
    async def list_active(self, *, state_code=None):
        return [
            {
                "case_type": "Divorce",
                "state_code": "tx",
                "cost": Decimal("10.00"),
                "currency": "USD",
            }
        ]


class _Bedrock:
    async def invoke_structured_prompt(self, prompt, model):
        return model(
            matched_case_type="Divorce",
            amount="10.00",
            currency="USD",
            confidence="high",
        )


@pytest.mark.asyncio
async def test_resolve_amount_matches_case_type():
    service = CaseTypeCostService(repo=_Repo(), bedrock=_Bedrock())
    result = await service.resolve_amount(
        {"case_type_name": "Divorce", "state_code": "tx"}
    )
    assert result["amount"] == "10.00"
    assert result["matched_case_type"] == "Divorce"
    assert result["confidence"] == "high"
