"""Shared pgvector ``VectorStoreConfig`` builder (RDS settings)."""

from __future__ import annotations

from app.adapters.vector_store.base import DistanceMetric, VectorStoreConfig
from app.config.settings import settings


def pgvector_vector_dim(embedder=None, *, default: int = 1024) -> int:
    if embedder is not None and hasattr(embedder, "embedding_dimension"):
        return int(embedder.embedding_dimension)
    return default


def build_pgvector_store_config(
    vector_dim: int,
) -> VectorStoreConfig:
    return VectorStoreConfig(
        host=settings.rds_host or "localhost",
        port=int(settings.rds_port or 5432),
        api_key=settings.rds_password or None,
        vector_dim=vector_dim,
        distance_metric=DistanceMetric.COSINE,
        extra_params={
            "user": settings.rds_username or "postgres",
            "password": settings.rds_password or "",
            "database": settings.rds_database or "postgres",
            "schema": settings.PGVECTOR_SCHEMA,
        },
    )


def build_pgvector_store_config_for_embedder(embedder=None) -> VectorStoreConfig:
    return build_pgvector_store_config(pgvector_vector_dim(embedder))
