"""
RAG Service

Business logic for RAG operations.
Handles strategy creation and execution.
"""

import logging
from typing import Dict, Any, Optional

from app.core.pipelines.rag.base import RAGConfig, RAGResponse
from app.adapters.vector_store.base import VectorStoreAdapter
from app.config.vector_store_factory import create_connected_vector_store

logger = logging.getLogger(__name__)

# Lazy imports to avoid circular dependencies
def _get_strategy_class(rag_type: str):
    """Get strategy class by type with lazy import."""
    logger.debug(f"Resolving RAG strategy class for rag_type='{rag_type}'")
    if rag_type == "naive":
        from app.core.pipelines.rag.strategies.naive import NaiveRAG
        return NaiveRAG
    logger.error(f"Unknown RAG type requested: {rag_type}")
    raise ValueError(f"Unknown RAG type: {rag_type}. Only 'naive' is supported.")


class RAGService:
    """
    Service for executing RAG queries.
    
    Handles strategy selection, configuration, and execution.
    """

    def __init__(
        self,
        llm_client: Any,
        embedder: Any
    ):
        """
        Initialize the RAG service.
        
        Args:
            llm_client: LLM client for generation.
            embedder: Embedding client.
        """
        self.llm = llm_client
        self.embedder = embedder
        self._vector_store_cache: Dict[str, VectorStoreAdapter] = {}
        logger.info(f"RAGService initialized with llm_client={llm_client}, embedder={embedder}")
    
    async def _get_vector_store_adapter(self, vector_store_name: str) -> VectorStoreAdapter:
        """
        Get or create a vector store adapter for the specified vector store type.
        
        Args:
            vector_store_name: Name of the vector store type (e.g., "pgvector").
            
        Returns:
            Vector store adapter instance.
        """
        if vector_store_name in self._vector_store_cache:
            return self._vector_store_cache[vector_store_name]
        
        try:
            adapter = await create_connected_vector_store(
                vector_store_name, self.embedder
            )
            self._vector_store_cache[vector_store_name] = adapter
            logger.info(f"Created and cached vector store adapter: {vector_store_name}")
            return adapter
        except Exception as e:
            logger.error(f"Failed to create vector store adapter for {vector_store_name}: {e}")
            raise
    
    def _get_strategy(self, rag_type: str, vector_store_adapter: VectorStoreAdapter):
        """
        Get the appropriate RAG strategy instance.
        
        Args:
            rag_type: Type of RAG strategy to use.
            vector_store_adapter: Vector store adapter to use.
            
        Returns:
            RAG strategy instance.
        """
        logger.debug(f"Selecting RAG strategy for rag_type='{rag_type}'")
        strategy_class = _get_strategy_class(rag_type.lower())

        logger.info(f"Instantiating RAG strategy: {strategy_class.__name__}")
        return strategy_class(
            vector_store_adapter=vector_store_adapter,
            llm_client=self.llm,
            embedder=self.embedder
        )

    async def execute_naive_rag(
        self,
        query: str,
        vector_store_name: str,
        collection_name: str,
        top_k: int = 10,
        filters: Dict[str, Any] = None,
        score_threshold: float = 0.0,
        system_prompt: str = None,
        query_image_base64: Optional[str] = None,
        query_image_bytes: Optional[bytes] = None
    ) -> RAGResponse:
        """Execute Naive RAG query with optional multimodal support."""
        logger.info(f"Executing naive RAG query. Params: vector_store='{vector_store_name}', collection='{collection_name}', top_k={top_k}, filters={filters}, score_threshold={score_threshold}, multimodal={query_image_base64 is not None or query_image_bytes is not None}")
        vector_store = await self._get_vector_store_adapter(vector_store_name)
        strategy = self._get_strategy("naive", vector_store)
        config = RAGConfig(
            vector_store=vector_store_name,
            collection_name=collection_name,
            top_k=top_k,
            filters=filters,
            score_threshold=score_threshold,
            system_prompt=system_prompt,
            extra_params={
                "query_image_base64": query_image_base64,
                "query_image_bytes": query_image_bytes,
            }
        )
        logger.debug(f"Naive RAG strategy config: {config}")
        try:
            result = await strategy.execute(query, config)
            logger.info(f"Naive RAG execution successful: answer_length={len(result.answer) if result and getattr(result, 'answer', None) else 0}, sources_count={len(result.sources) if result and getattr(result, 'sources', None) else 0}")
            return result
        except Exception as e:
            logger.exception(f"Naive RAG execution failed: {e}")
            raise
