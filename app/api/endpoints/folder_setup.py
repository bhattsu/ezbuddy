"""APIs to create S3 template folders and ingest configuration.states rows."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from app.api.schemas.folder_setup import (
    CreateJurisdictionFolderRequest,
    CreateStateFolderRequest,
    JurisdictionFolderResponse,
    StateFolderResponse,
)
from app.api.schemas.rag import ErrorResponse
from app.services.aim_factory import get_or_create_rds_repo
from app.services.folder_setup_service import FolderSetupError, FolderSetupService
from app.utils.s3_utils import S3Manager

logger = logging.getLogger(__name__)
router = APIRouter()


async def _service(request: Request) -> FolderSetupService:
    try:
        rds = await get_or_create_rds_repo(request.app)
    except Exception as exc:  # noqa: BLE001
        logger.error("RDS unavailable for folder setup: %s", exc)
        raise HTTPException(status_code=503, detail="Database unavailable") from exc
    return FolderSetupService(rds=rds, s3=S3Manager())


@router.post(
    "/states",
    response_model=StateFolderResponse,
    responses={404: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
    summary="Create state template folder and ingest configuration.states",
    description=(
        "Creates ``documents-repo/templates/{code}/`` in S3 (via a .keep marker) "
        "and upserts a row in ``configuration.states``."
    ),
)
async def create_state_folder(
    request: Request,
    body: CreateStateFolderRequest,
) -> StateFolderResponse:
    service = await _service(request)
    try:
        row = await service.create_state(code=body.code, name=body.name)
    except FolderSetupError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("State folder creation failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return StateFolderResponse.model_validate(row)


@router.post(
    "/jurisdictions",
    response_model=JurisdictionFolderResponse,
    responses={404: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
    summary="Create jurisdiction template folder under a state",
    description=(
        "Creates ``documents-repo/templates/{state}/{jurisdiction_name}/`` in S3. "
        "The state folder must already exist."
    ),
)
async def create_jurisdiction_folder(
    request: Request,
    body: CreateJurisdictionFolderRequest,
) -> JurisdictionFolderResponse:
    service = await _service(request)
    try:
        row = await service.create_jurisdiction(
            state=body.state,
            jurisdiction_name=body.jurisdiction_name,
        )
    except FolderSetupError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("Jurisdiction folder creation failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return JurisdictionFolderResponse.model_validate(row)
