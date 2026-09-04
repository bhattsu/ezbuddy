"""
Base Vector Store Adapter Interface

This module defines the abstract base class for all vector store adapters.
All vector database implementations must inherit from VectorStoreAdapter.
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from enum import Enum


class DistanceMetric(Enum):
    """Supported distance metrics for vector similarity search."""
    COSINE = "cosine"
    EUCLIDEAN = "euclidean"
    DOT_PRODUCT = "dot_product"


@dataclass
class SearchResult:
    """Represents a single search result from vector store."""
    id: str
    score: float
    payload: Dict[str, Any]
    vector: Optional[List[float]] = None


@dataclass
class Document:
    """Represents a document with its embedding and metadata."""
    id: str
    content: str
    embedding: List[float]
    metadata: Dict[str, Any]


@dataclass
class VectorStoreConfig:
    """Configuration for vector store connection."""
    host: str
    port: int
    api_key: Optional[str] = None
    collection_name: str = "default"
    vector_dim: int = 1536
    distance_metric: DistanceMetric = DistanceMetric.COSINE
    extra_params: Dict[str, Any] = None

    def __post_init__(self):
        if self.extra_params is None:
            self.extra_params = {}


class VectorStoreAdapter(ABC):
    """
    Abstract base class for vector store adapters.
    
    All vector database implementations must implement this interface
    to ensure pluggability.
    """

    def __init__(self, config: VectorStoreConfig):
        """Initialize the adapter with configuration."""
        self.config = config
        self._client = None

    @abstractmethod
    async def connect(self) -> None:
        """
        Establish connection to the vector store.
        
        Raises:
            ConnectionError: If connection fails.
        """
        pass

    @abstractmethod
    async def disconnect(self) -> None:
        """Close connection to the vector store."""
        pass

    @abstractmethod
    async def create_collection(
        self,
        collection_name: str,
        vector_dim: int,
        distance_metric: DistanceMetric = DistanceMetric.COSINE,
        **kwargs
    ) -> bool:
        """
        Create a new collection/index in the vector store.
        
        Args:
            collection_name: Name of the collection to create.
            vector_dim: Dimension of vectors to store.
            distance_metric: Distance metric for similarity search.
            **kwargs: Additional store-specific parameters.
            
        Returns:
            True if collection was created successfully.
        """
        pass

    @abstractmethod
    async def delete_collection(self, collection_name: str) -> bool:
        """
        Delete a collection from the vector store.
        
        Args:
            collection_name: Name of the collection to delete.
            
        Returns:
            True if collection was deleted successfully.
        """
        pass

    @abstractmethod
    async def collection_exists(self, collection_name: str) -> bool:
        """
        Check if a collection exists.
        
        Args:
            collection_name: Name of the collection to check.
            
        Returns:
            True if collection exists.
        """
        pass

    @abstractmethod
    async def upsert(
        self,
        collection_name: str,
        ids: List[str],
        embeddings: List[List[float]],
        metadata: List[Dict[str, Any]],
        **kwargs
    ) -> int:
        """
        Insert or update vectors in the collection.
        
        Args:
            collection_name: Name of the collection.
            ids: List of unique identifiers for each vector.
            embeddings: List of embedding vectors.
            metadata: List of metadata dictionaries for each vector.
            **kwargs: Additional store-specific parameters.
            
        Returns:
            Number of vectors successfully upserted.
        """
        pass

    @abstractmethod
    async def query(
        self,
        collection_name: str,
        query_vector: List[float],
        top_k: int = 10,
        filters: Optional[Dict[str, Any]] = None,
        include_vectors: bool = False,
        **kwargs
    ) -> List[SearchResult]:
        """
        Query the vector store for similar vectors.
        
        Args:
            collection_name: Name of the collection to search.
            query_vector: The query embedding vector.
            top_k: Maximum number of results to return.
            filters: Optional metadata filters.
            include_vectors: Whether to include vectors in results.
            **kwargs: Additional store-specific parameters.
            
        Returns:
            List of SearchResult objects ordered by similarity.
        """
        pass

    @abstractmethod
    async def delete(
        self,
        collection_name: str,
        ids: List[str]
    ) -> bool:
        """
        Delete vectors by their IDs.
        
        Args:
            collection_name: Name of the collection.
            ids: List of vector IDs to delete.
            
        Returns:
            True if deletion was successful.
        """
        pass

    @abstractmethod
    async def get_by_ids(
        self,
        collection_name: str,
        ids: List[str],
        include_vectors: bool = False
    ) -> List[SearchResult]:
        """
        Retrieve vectors by their IDs.
        
        Args:
            collection_name: Name of the collection.
            ids: List of vector IDs to retrieve.
            include_vectors: Whether to include vectors in results.
            
        Returns:
            List of SearchResult objects.
        """
        pass

    @abstractmethod
    async def count(self, collection_name: str) -> int:
        """
        Get the number of vectors in a collection.
        
        Args:
            collection_name: Name of the collection.
            
        Returns:
            Number of vectors in the collection.
        """
        pass

    @abstractmethod
    async def health_check(self) -> bool:
        """
        Check if the vector store is healthy and accessible.
        
        Returns:
            True if the store is healthy.
        """
        pass

    async def ensure_collection(
        self,
        collection_name: str,
        vector_dim: int,
        distance_metric: DistanceMetric = DistanceMetric.COSINE
    ) -> None:
        """
        Ensure a collection exists, creating it if necessary.
        
        Args:
            collection_name: Name of the collection.
            vector_dim: Dimension of vectors.
            distance_metric: Distance metric for similarity.
        """
        if not await self.collection_exists(collection_name):
            await self.create_collection(collection_name, vector_dim, distance_metric)

    async def __aenter__(self):
        """Async context manager entry."""
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.disconnect()

