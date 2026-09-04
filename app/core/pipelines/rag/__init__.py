"""
RAG (Retrieval-Augmented Generation) Module

This module provides the base RAG strategy interface and all strategy implementations.

Note: Strategies are imported lazily to avoid circular import issues.
They register themselves when their modules are imported.
"""

from app.core.pipelines.rag.base import (
    RAGStrategy,
    RAGConfig,
    RAGResponse,
    RAGType,
    RetrievedDocument,
    RetrievalContext,
    SourceDocument,
)

# Lazy imports - strategies will register themselves when imported
# Import them only when needed to avoid circular imports
def _lazy_import_strategies():
    """Lazy import of strategies to trigger registration."""
    from app.core.pipelines.rag.strategies import naive  # noqa: F401

__all__ = [
    # Base classes
    "RAGStrategy",
    "RAGConfig",
    "RAGResponse",
    "RAGType",
    "RetrievedDocument",
    "RetrievalContext",
    "SourceDocument",
]
