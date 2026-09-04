"""
RAG API Schemas

Pydantic models for RAG endpoint request/response validation.
"""

from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
from enum import Enum


class VectorStoreType(str, Enum):
    """Supported vector store types."""
    PGVECTOR = "pgvector"


class SourceDocument(BaseModel):
    """Source document information in response."""
    id: str
    content: str
    score: float
    metadata: Dict[str, Any] = {}
    source: Optional[str] = None


class RAGQueryRequest(BaseModel):
    """Request model for Naive RAG queries."""
    query: str = Field(
        default="",
        description="The user query text (optional if using image query)"
    )
    vector_store: VectorStoreType = Field(
        default=VectorStoreType.PGVECTOR,
        description="Vector store to use for retrieval"
    )
    collection_name: str = Field(
        default="default",
        description="Name of the collection to search"
    )
    top_k: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Number of documents to retrieve"
    )
    filters: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Metadata filters for retrieval"
    )
    score_threshold: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Minimum similarity score threshold"
    )
    system_prompt: Optional[str] = Field(
        default=None,
        description="Optional custom system prompt for generation"
    )
    # Multimodal support
    query_image_base64: Optional[str] = Field(
        default=None,
        description="Base64-encoded image for multimodal query (alternative to query text)"
    )

    class Config:
        use_enum_values = True


class RAGResponse(BaseModel):
    """Standard response model for RAG queries."""
    answer: str = Field(..., description="Generated answer")
    sources: List[SourceDocument] = Field(
        default_factory=list,
        description="Source documents used for the answer"
    )
    rag_type: str = Field(..., description="Type of RAG strategy used")
    vector_store: str = Field(..., description="Vector store used")
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Confidence score of the answer"
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Additional metadata about the query execution"
    )


class HealthResponse(BaseModel):
    """Health check response."""
    status: str
    vector_stores: Dict[str, bool]
    rag_strategies: List[str]


class ErrorResponse(BaseModel):
    """Error response model."""
    error: str
    detail: Optional[str] = None
    status_code: int
