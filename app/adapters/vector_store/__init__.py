"""
Vector Database Adapters

Provides pgvector and Amazon OpenSearch adapters.
All adapters implement the VectorStoreAdapter interface.

Note: Adapters are imported lazily to avoid circular import issues.
They register themselves when their modules are imported.
"""

from app.adapters.vector_store.base import (
    VectorStoreAdapter,
    VectorStoreConfig,
    SearchResult,
    Document,
    DistanceMetric,
)

# Lazy imports - adapters will register themselves when imported
# Import them only when needed to avoid circular imports
def _lazy_import_adapters():
    """Lazy import of adapters to trigger registration."""
    from app.adapters.vector_store import pgvector  # noqa: F401
    from app.adapters.vector_store import opensearch  # noqa: F401

__all__ = [
    # Base classes
    "VectorStoreAdapter",
    "VectorStoreConfig",
    "SearchResult",
    "Document",
    "DistanceMetric",
]
