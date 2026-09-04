"""
RAG Strategy Implementations

Naive RAG is the sole supported strategy.
"""

from app.core.pipelines.rag.strategies.naive import NaiveRAG

__all__ = [
    "NaiveRAG",
]
