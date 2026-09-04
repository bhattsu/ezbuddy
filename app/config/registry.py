"""
Registry Pattern for RAG Strategies and Vector Store Adapters

This module provides a central registry for dynamically resolving
RAG strategies and vector store adapters at runtime.
"""

from typing import Dict, Type, Optional, Any
import logging

from app.adapters.vector_store.base import VectorStoreAdapter, VectorStoreConfig
from app.core.pipelines.rag.base import RAGStrategy, RAGType

logger = logging.getLogger(__name__)


class VectorStoreRegistry:
    """
    Registry for vector store adapters.
    
    Allows registration and retrieval of vector store adapter classes
    by name, enabling runtime selection of vector databases.
    """
    
    _adapters: Dict[str, Type[VectorStoreAdapter]] = {}
    _instances: Dict[str, VectorStoreAdapter] = {}

    @classmethod
    def register(cls, name: str, adapter_class: Type[VectorStoreAdapter]) -> None:
        """
        Register a vector store adapter class.
        
        Args:
            name: Unique name for the adapter (e.g., "pgvector").
            adapter_class: The adapter class to register.
        """
        cls._adapters[name.lower()] = adapter_class
        logger.info(f"Registered vector store adapter: {name}")

    @classmethod
    def get_adapter_class(cls, name: str) -> Optional[Type[VectorStoreAdapter]]:
        """
        Get a registered adapter class by name.
        
        Args:
            name: Name of the adapter to retrieve.
            
        Returns:
            The adapter class if found, None otherwise.
        """
        return cls._adapters.get(name.lower())

    @classmethod
    def create_adapter(
        cls,
        name: str,
        config: VectorStoreConfig
    ) -> VectorStoreAdapter:
        """
        Create an instance of a registered adapter.
        
        Args:
            name: Name of the adapter to create.
            config: Configuration for the adapter.
            
        Returns:
            An instance of the requested adapter.
            
        Raises:
            ValueError: If adapter name is not registered.
        """
        # Lazy import adapters to ensure they're registered
        if not cls._adapters:
            from app.adapters.vector_store import _lazy_import_adapters
            _lazy_import_adapters()
        
        adapter_class = cls.get_adapter_class(name)
        if adapter_class is None:
            available = list(cls._adapters.keys())
            raise ValueError(
                f"Unknown vector store adapter: {name}. "
                f"Available adapters: {available}"
            )
        return adapter_class(config)

    @classmethod
    def get_or_create_adapter(
        cls,
        name: str,
        config: VectorStoreConfig,
        cache_key: Optional[str] = None
    ) -> VectorStoreAdapter:
        """
        Get an existing adapter instance or create a new one.
        
        Args:
            name: Name of the adapter.
            config: Configuration for the adapter.
            cache_key: Optional key for caching the instance.
            
        Returns:
            The adapter instance.
        """
        key = cache_key or f"{name}:{config.host}:{config.port}"
        if key not in cls._instances:
            cls._instances[key] = cls.create_adapter(name, config)
        return cls._instances[key]

    @classmethod
    def list_adapters(cls) -> list:
        """List all registered adapter names."""
        return list(cls._adapters.keys())

    @classmethod
    def clear_instances(cls) -> None:
        """Clear all cached adapter instances."""
        cls._instances.clear()


class RAGStrategyRegistry:
    """
    Registry for RAG strategies.
    
    Allows registration and retrieval of RAG strategy classes
    by type, enabling runtime selection of RAG approaches.
    """
    
    _strategies: Dict[str, Type[RAGStrategy]] = {}

    @classmethod
    def register(cls, rag_type: RAGType, strategy_class: Type[RAGStrategy]) -> None:
        """
        Register a RAG strategy class.
        
        Args:
            rag_type: The RAG type enum value.
            strategy_class: The strategy class to register.
        """
        cls._strategies[rag_type.value] = strategy_class
        logger.info(f"Registered RAG strategy: {rag_type.value}")

    @classmethod
    def get_strategy_class(cls, rag_type: str) -> Optional[Type[RAGStrategy]]:
        """
        Get a registered strategy class by type.
        
        Args:
            rag_type: Type of the strategy (string value).
            
        Returns:
            The strategy class if found, None otherwise.
        """
        return cls._strategies.get(rag_type.lower())

    @classmethod
    def create_strategy(
        cls,
        rag_type: str,
        vector_store_adapter: VectorStoreAdapter,
        llm_client: Any,
        embedder: Any
    ) -> RAGStrategy:
        """
        Create an instance of a registered strategy.
        
        Args:
            rag_type: Type of the strategy to create.
            vector_store_adapter: The vector store adapter to use.
            llm_client: The LLM client for generation.
            embedder: The embedding client.
            
        Returns:
            An instance of the requested strategy.
            
        Raises:
            ValueError: If strategy type is not registered.
        """
        # Lazy import strategies to ensure they're registered
        if not cls._strategies:
            from app.core.pipelines.rag import _lazy_import_strategies
            _lazy_import_strategies()
        
        strategy_class = cls.get_strategy_class(rag_type)
        if strategy_class is None:
            available = list(cls._strategies.keys())
            raise ValueError(
                f"Unknown RAG strategy: {rag_type}. "
                f"Available strategies: {available}"
            )
        return strategy_class(vector_store_adapter, llm_client, embedder)

    @classmethod
    def list_strategies(cls) -> list:
        """List all registered strategy types."""
        return list(cls._strategies.keys())


def register_adapter(name: str):
    """
    Decorator for registering vector store adapters.
    
    Usage:
        @register_adapter("pgvector")
        class PgVectorAdapter(VectorStoreAdapter):
            ...
    """
    def decorator(cls: Type[VectorStoreAdapter]):
        VectorStoreRegistry.register(name, cls)
        return cls
    return decorator


def register_strategy(rag_type: RAGType):
    """
    Decorator for registering RAG strategies.
    
    Usage:
        @register_strategy(RAGType.NAIVE)
        class NaiveRAG(RAGStrategy):
            ...
    """
    def decorator(cls: Type[RAGStrategy]):
        RAGStrategyRegistry.register(rag_type, cls)
        return cls
    return decorator


# Convenience functions for getting adapters and strategies
def get_vector_store(
    name: str,
    config: VectorStoreConfig
) -> VectorStoreAdapter:
    """Get a vector store adapter by name."""
    return VectorStoreRegistry.create_adapter(name, config)


def get_rag_strategy(
    rag_type: str,
    vector_store: VectorStoreAdapter,
    llm_client: Any,
    embedder: Any
) -> RAGStrategy:
    """Get a RAG strategy by type."""
    return RAGStrategyRegistry.create_strategy(
        rag_type, vector_store, llm_client, embedder
    )

