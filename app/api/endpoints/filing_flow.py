"""Deterministic filing intake over HTTP (dropdown steps until form questions)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from app.api.schemas.filing_flow import (
    CreateFilingFlowSessionRequest,
    FilingFlowSelectRequest,
    FilingFlowSessionCreatedResponse,
    FilingFlowStepResponse,
)
from app.api.schemas.rag import ErrorResponse
from app.services.conversation_repository import ConversationRepository
from app.services.filing_selection_flow_service import (
    FilingSelectionFlowError,
    FilingSelectionFlowService,
)
from app.services.legal_filing_repository import LegalFilingRepository

logger = logging.getLogger(__name__)

router = APIRouter()


async def _build_service(request: Request) -> FilingSelectionFlowService:
    from app.services.aim_factory import get_or_create_rds_repo

    try:
        rds = await get_or_create_rds_repo(request.app)
    except Exception as exc:  # noqa: BLE001
        logger.error("RDS unavailable for filing flow: %s", exc)
        raise HTTPException(status_code=503, detail="Database unavailable") from exc
    if rds is None:
        raise HTTPException(status_code=503, detail="Database not configured")
    filing_repo = LegalFilingRepository(rds=rds)
    conversation_repo = ConversationRepository(rds=rds)
    return FilingSelectionFlowService(
        filing_repo=filing_repo,
        conversation_repo=conversation_repo,
    )


@router.post(
    "/sessions",
    response_model=FilingFlowSessionCreatedResponse,
    responses={503: {"model": ErrorResponse}},
    summary="Start a filing flow session (state dropdown first)",
)
async def create_filing_flow_session(
    request: Request,
    body: CreateFilingFlowSessionRequest,
):
    service = await _build_service(request)
    try:
        return await service.create_session(body.user_id, body.case_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("create_filing_flow_session failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get(
    "/sessions/{conversation_id}",
    response_model=FilingFlowStepResponse,
    responses={404: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
    summary="Current step: phase, cached selections, and dropdown options",
)
async def get_filing_flow_step(request: Request, conversation_id: str):
    service = await _build_service(request)
    try:
        return await service.get_step(conversation_id)
    except FilingSelectionFlowError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post(
    "/sessions/{conversation_id}/select",
    response_model=FilingFlowStepResponse,
    responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
    summary="Apply one dropdown choice (code) and advance to the next step",
)
async def select_filing_flow_option(
    request: Request,
    conversation_id: str,
    body: FilingFlowSelectRequest,
):
    service = await _build_service(request)
    try:
        return await service.apply_selection(conversation_id, body.code)
    except FilingSelectionFlowError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
