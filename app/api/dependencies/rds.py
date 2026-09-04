"""FastAPI dependency for the shared RDS connection pool."""

from __future__ import annotations

from typing import Optional

from fastapi import HTTPException, Request, status

from app.adapters.data_sources.AIM_rds import RDSRepository
from app.services.aim_factory import get_or_create_rds_repo


async def get_rds_repo(request: Request) -> Optional[RDSRepository]:
    """Return the app-scoped RDS pool, or None when RDS is not configured."""
    return await get_or_create_rds_repo(request.app)


async def require_rds_repo(request: Request) -> RDSRepository:
    """Return the RDS pool or raise 503 when RDS is unavailable."""
    repo = await get_or_create_rds_repo(request.app)
    if repo is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="RDS is not configured",
        )
    return repo