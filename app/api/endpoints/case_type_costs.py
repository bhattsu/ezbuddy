"""Case type filing cost configuration API."""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request

from app.api.schemas.case_type_costs import (
    CaseTypeCostListResponse,
    CaseTypeCostResponse,
    CaseTypeCostUpsertRequest,
)
from app.api.schemas.rag import ErrorResponse
from app.services.aim_factory import get_or_create_rds_repo
from app.services.case_type_cost_repository import CaseTypeCostRepository

logger = logging.getLogger(__name__)
router = APIRouter()


async def _repo(request: Request) -> CaseTypeCostRepository:
    try:
        rds = await get_or_create_rds_repo(request.app)
    except Exception as exc:  # noqa: BLE001
        logger.error("RDS unavailable for case type costs: %s", exc)
        raise HTTPException(status_code=503, detail="Database unavailable") from exc
    if rds is None:
        raise HTTPException(status_code=503, detail="Database not configured")
    return CaseTypeCostRepository(rds=rds)


def _row_to_response(row: dict) -> CaseTypeCostResponse:
    return CaseTypeCostResponse.model_validate(row)


@router.get(
    "",
    response_model=CaseTypeCostListResponse,
    responses={503: {"model": ErrorResponse}},
    summary="List active case type filing costs",
)
async def list_case_type_costs(
    request: Request,
    state_code: Optional[str] = Query(default=None, max_length=10),
) -> CaseTypeCostListResponse:
    repo = await _repo(request)
    rows = await repo.list_active(state_code=state_code)
    items = [_row_to_response(row) for row in rows]
    return CaseTypeCostListResponse(items=items, total=len(items))


@router.put(
    "/ingest",
    response_model=CaseTypeCostResponse,
    responses={503: {"model": ErrorResponse}},
    summary="Upsert a case type filing cost",
)
async def upsert_case_type_cost(
    request: Request,
    body: CaseTypeCostUpsertRequest,
) -> CaseTypeCostResponse:
    repo = await _repo(request)
    try:
        row = await repo.upsert(
            case_type=body.case_type.strip(),
            state_code=(body.state_code or "").strip() or None,
            cost=body.cost,
            currency=body.currency.strip().upper(),
            is_active=body.is_active,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Case type cost upsert failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if not row:
        raise HTTPException(status_code=500, detail="Upsert returned no row")
    return _row_to_response(row)
