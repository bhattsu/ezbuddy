"""Sync SQLAlchemy engine for local ORM bootstrap / seeds only.

Package Excel ingest uses ``AIM_rds.RDSRepository`` (see ``app.core.ingestion.excel.importer``).
Do not use this module for chat or package import.
"""

from __future__ import annotations

from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config.settings import settings


@lru_cache(maxsize=1)
def get_sync_ingestion_engine() -> Engine:
    return create_engine(settings.SYNC_DATABASE_URI, pool_pre_ping=True)


@lru_cache(maxsize=1)
def get_sync_ingestion_session_factory() -> sessionmaker[Session]:
    return sessionmaker(
        bind=get_sync_ingestion_engine(),
        autoflush=False,
        autocommit=False,
    )
