"""User authentication endpoints (US Legal Pro platform)."""

from __future__ import annotations

import logging
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Request

from app.adapters.uslegalpro.client import USLegalProClient
from app.api.dependencies.rate_limit import limiter, rate_limit_string
from app.api.schemas.auth import LoginRequest, LoginResponse
from app.api.schemas.rag import ErrorResponse
from app.services.auth_service import AuthService
from app.services.operational_user_repository import OperationalUserRepository

logger = logging.getLogger(__name__)

router = APIRouter()


def _build_auth_service(rds) -> AuthService:
    return AuthService(
        user_repo=OperationalUserRepository(rds=rds),
        api_client=USLegalProClient(),
    )


@router.post(
    "/login",
    response_model=LoginResponse,
    responses={401: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
    summary="Login via US Legal Pro platform",
    description=(
        "Authenticates with the external US Legal Pro API, stores auth_token and "
        "session_id in operational.users, and returns the internal user_id for chat."
    ),
)
@limiter.limit(rate_limit_string)
async def login(request: Request, body: LoginRequest):
    from app.services.aim_factory import get_or_create_rds_repo

    try:
        rds = await get_or_create_rds_repo(request.app)
    except Exception as exc:  # noqa: BLE001
        logger.error("RDS unavailable for login: %s", exc)
        raise HTTPException(status_code=503, detail="Database unavailable") from exc

    if rds is None:
        raise HTTPException(status_code=503, detail="Database not configured")

    service = _build_auth_service(rds)
    try:
        return await service.login(body.username, body.password, state=body.state)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        logger.error("Login upstream error: %s", exc)
        raise HTTPException(status_code=502, detail="Authentication service unreachable") from exc
