"""Shared OpenSearch ``VectorStoreConfig`` builder."""

from __future__ import annotations

from app.adapters.vector_store.base import DistanceMetric, VectorStoreConfig
from app.config.pgvector_store import pgvector_vector_dim
from app.config.settings import settings


def build_opensearch_store_config(vector_dim: int) -> VectorStoreConfig:
    endpoint = (
        getattr(settings, "OPENSEARCH_ENDPOINT", None)
        or getattr(settings, "opensearch_endpoint", None)
        or ""
    )
    host = endpoint.strip()
    # Allow host without scheme; adapter normalizes.
    return VectorStoreConfig(
        host=host,
        port=int(getattr(settings, "OPENSEARCH_PORT", 443) or 443),
        api_key=None,
        vector_dim=vector_dim,
        distance_metric=DistanceMetric.COSINE,
        extra_params={
            "region": getattr(settings, "OPENSEARCH_REGION", None)
            or settings.AWS_REGION
            or "us-east-1",
            "use_aws_auth": bool(getattr(settings, "OPENSEARCH_USE_AWS_AUTH", True)),
            "use_ssl": bool(getattr(settings, "OPENSEARCH_USE_SSL", True)),
            "verify_certs": bool(getattr(settings, "OPENSEARCH_VERIFY_CERTS", True)),
            "aws_access_key_id": settings.AWS_ACCESS_KEY_ID,
            "aws_secret_access_key": settings.AWS_SECRET_ACCESS_KEY,
            "username": getattr(settings, "OPENSEARCH_USERNAME", None),
            "password": getattr(settings, "OPENSEARCH_PASSWORD", None),
            "knn_engine": getattr(settings, "OPENSEARCH_KNN_ENGINE", "nmslib") or "nmslib",
            "ef_search": int(getattr(settings, "OPENSEARCH_EF_SEARCH", 100) or 100),
            "ef_construction": int(
                getattr(settings, "OPENSEARCH_EF_CONSTRUCTION", 128) or 128
            ),
            "hnsw_m": int(getattr(settings, "OPENSEARCH_HNSW_M", 16) or 16),
            "number_of_shards": int(
                getattr(settings, "OPENSEARCH_NUMBER_OF_SHARDS", 1) or 1
            ),
            "number_of_replicas": int(
                getattr(settings, "OPENSEARCH_NUMBER_OF_REPLICAS", 1) or 1
            ),
            "timeout": int(getattr(settings, "OPENSEARCH_TIMEOUT", 60) or 60),
            "aws_service": getattr(settings, "OPENSEARCH_AWS_SERVICE", "es") or "es",
        },
    )


def build_opensearch_store_config_for_embedder(embedder=None) -> VectorStoreConfig:
    return build_opensearch_store_config(pgvector_vector_dim(embedder))
