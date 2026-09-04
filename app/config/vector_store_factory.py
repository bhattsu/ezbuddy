"""Factory helpers for vector store adapters (pgvector / OpenSearch)."""

from __future__ import annotations

from typing import Any

from app.adapters.vector_store.base import VectorStoreAdapter, VectorStoreConfig
from app.config.opensearch_store import build_opensearch_store_config
from app.config.pgvector_store import build_pgvector_store_config, pgvector_vector_dim
from app.config.registry import VectorStoreRegistry


def build_vector_store_config(
    vector_store_name: str,
    vector_dim: int,
) -> VectorStoreConfig:
    name = (vector_store_name or "").lower()
    if name == "pgvector":
        return build_pgvector_store_config(vector_dim)
    if name in {"opensearch", "aos", "amazon_opensearch"}:
        return build_opensearch_store_config(vector_dim)
    raise ValueError(
        f"Unknown vector store: {vector_store_name}. Available: ['pgvector', 'opensearch']"
    )


async def create_connected_vector_store(
    vector_store_name: str,
    embedder: Any = None,
) -> VectorStoreAdapter:
    vector_dim = pgvector_vector_dim(embedder)
    config = build_vector_store_config(vector_store_name, vector_dim)
    # Normalize alias names to registered adapter id
    name = (vector_store_name or "").lower()
    if name in {"aos", "amazon_opensearch"}:
        name = "opensearch"
    adapter = VectorStoreRegistry.create_adapter(name, config)
    await adapter.connect()
    return adapter
