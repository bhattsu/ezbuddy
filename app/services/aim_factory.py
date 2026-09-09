"""Build AIM repository instances from ``app.config.settings``."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Optional

from fastapi import FastAPI

from app.adapters.data_sources.AIM_rds import RDSRepository
from app.config.settings import Settings, get_settings

logger = logging.getLogger(__name__)


def aim_config_snapshot(settings: Optional[Settings] = None) -> dict[str, Any]:
    """Non-secret flags for health/config endpoints."""
    s = settings or get_settings()
    return {
        "rds_configured": bool(
            s.rds_host and s.rds_database and s.rds_username and s.rds_password
        ),
    }


def make_rds_repo(settings: Optional[Settings] = None) -> Optional[RDSRepository]:
    s = settings or get_settings()
    if not all([s.rds_host, s.rds_database, s.rds_username, s.rds_password]):
        return None
    return RDSRepository(
        host=s.rds_host,
        port=s.rds_port,
        database=s.rds_database,
        user=s.rds_username,
        password=s.rds_password,
        pool_min_size=2,
    )


async def startup_rds(app: FastAPI) -> None:
    """Create the shared RDS pool at application startup."""
    repo = make_rds_repo()
    app.state.rds_repo = repo
    if repo is None:
        logger.debug("RDS not configured; skipping pool init")
        return

    s = get_settings()
    if await repo.check_connection():
        logger.info(
            "RDS connection pool ready (%s:%s/%s)",
            s.rds_host,
            s.rds_port,
            s.rds_database,
        )
    else:
        logger.warning("RDS configured but initial connection check failed")


async def shutdown_rds(app: FastAPI) -> None:
    """Close the shared RDS pool on application shutdown."""
    repo: Optional[RDSRepository] = getattr(app.state, "rds_repo", None)
    if repo is not None:
        await repo.close()
        app.state.rds_repo = None


async def get_or_create_rds_repo(app: FastAPI) -> Optional[RDSRepository]:
    """Return the app-scoped pool, creating it lazily when lifespan is disabled (e.g. Lambda)."""
    repo: Optional[RDSRepository] = getattr(app.state, "rds_repo", None)
    if repo is not None:
        return repo
    repo = make_rds_repo()
    app.state.rds_repo = repo
    return repo


@asynccontextmanager
async def ephemeral_rds_repo() -> AsyncIterator[Optional[RDSRepository]]:
    """One-off scripts: create a pool, yield it, then close (do not use in request handlers)."""
    repo = make_rds_repo()
    try:
        yield repo
    finally:
        if repo is not None:
            await repo.close()