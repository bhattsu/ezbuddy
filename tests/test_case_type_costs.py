"""Case type filing cost repository and authorize client tests."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.case_type_cost_repository import CaseTypeCostRepository
from app.services.uslegalpro_payment_service import USLegalProPaymentService


class _FakeRDS:
    def __init__(self) -> None:
        self.last_fetch: tuple | None = None

    async def fetch(self, sql, *args):
        self.last_fetch = (sql, args)
        if "upsert" in str(sql).lower():
            return [
                {
                    "id": "11111111-1111-1111-1111-111111111111",
                    "case_type": args[0],
                    "state_code": args[1] or None,
                    "cost": args[2],
                    "currency": args[3],
                    "is_active": args[4],
                }
            ]
        return [
            {
                "id": "11111111-1111-1111-1111-111111111111",
                "case_type": "Divorce",
                "state_code": "tx",
                "cost": Decimal("10.00"),
                "currency": "USD",
                "is_active": True,
            }
        ]


@pytest.mark.asyncio
async def test_case_type_cost_upsert():
    rds = _FakeRDS()
    repo = CaseTypeCostRepository(rds=rds)
    row = await repo.upsert(
        case_type="Divorce",
        state_code="tx",
        cost=Decimal("10.00"),
        currency="USD",
        is_active=True,
    )
    assert row is not None
    assert row["case_type"] == "Divorce"
    assert row["cost"] == Decimal("10.00")
    assert rds.last_fetch is not None
    assert rds.last_fetch[1][0] == "Divorce"


@pytest.mark.asyncio
async def test_case_type_cost_list_active():
    rds = _FakeRDS()
    repo = CaseTypeCostRepository(rds=rds)
    rows = await repo.list_active(state_code="tx")
    assert len(rows) == 1
    assert rows[0]["case_type"] == "Divorce"


@pytest.mark.asyncio
async def test_authorize_payment_payload(monkeypatch):
    captured = {}

    class _Client:
        def __init__(self, auth_token=None, client_token=None, base_url=None):
            self.base_url = base_url

        async def authorize_payment(
            self,
            *,
            payment_account_id,
            amount,
            additional_info=None,
        ):
            captured["payment_account_id"] = payment_account_id
            captured["amount"] = amount
            captured["additional_info"] = additional_info
            return {"message_code": 0, "item": {"status": "authorized"}}

    monkeypatch.setattr(
        "app.services.uslegalpro_payment_service.USLegalProApiClient",
        _Client,
    )
    result = await USLegalProPaymentService().authorize_payment(
        payment_account_id="1m1xrwdt",
        amount="10.00",
        additional_info={"email": "user@example.com", "source": "USLP-AI"},
    )
    assert captured["payment_account_id"] == "1m1xrwdt"
    assert captured["amount"] == "10.00"
    assert captured["additional_info"]["source"] == "USLP-AI"
    assert result["item"]["status"] == "authorized"
