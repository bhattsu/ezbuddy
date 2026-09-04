"""
Chatbot API Schemas

Pydantic models for unified chatbot endpoint request/response validation.
"""

from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
from enum import Enum
from datetime import datetime


class VectorStoreType(str, Enum):
    """Supported vector store types."""
    PGVECTOR = "pgvector"


class RAGStrategyType(str, Enum):
    """Supported RAG strategy types."""
    NAIVE = "naive"


class RetrievalConfigRequest(BaseModel):
    """Configuration for retrieval operations."""
    top_k: int = Field(default=10, ge=1, le=100, description="Number of documents to retrieve")
    score_threshold: float = Field(default=0.0, ge=0.0, le=1.0, description="Minimum similarity score")
    filters: Optional[Dict[str, Any]] = Field(default=None, description="Metadata filters")


class GenerationConfigRequest(BaseModel):
    """Configuration for LLM generation."""
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(default=2000, ge=1, le=100000)
    system_prompt: Optional[str] = Field(default=None, description="Custom system prompt")


# ============== Session Management ==============

class CreateSessionRequest(BaseModel):
    """Request to create a new chatbot session."""
    rag_strategy: RAGStrategyType = Field(
        default=RAGStrategyType.NAIVE,
        description="RAG strategy to use (naive only)"
    )
    vector_store: VectorStoreType = Field(
        default=VectorStoreType.PGVECTOR,
        description="Vector store to use"
    )
    collection_name: str = Field(
        default="default",
        description="Collection/index name in the vector store"
    )
    retrieval_config: Optional[RetrievalConfigRequest] = Field(
        default=None,
        description="Retrieval configuration"
    )
    generation_config: Optional[GenerationConfigRequest] = Field(
        default=None,
        description="Generation configuration"
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Additional session metadata"
    )

    class Config:
        use_enum_values = True


class UpdateSessionRequest(BaseModel):
    """Request to update an existing session configuration."""
    rag_strategy: Optional[RAGStrategyType] = Field(
        default=None,
        description="RAG strategy (naive only)"
    )
    vector_store: Optional[VectorStoreType] = Field(
        default=None,
        description="New vector store (switches store)"
    )
    collection_name: Optional[str] = Field(
        default=None,
        description="New collection name"
    )
    retrieval_config: Optional[RetrievalConfigRequest] = Field(
        default=None,
        description="Updated retrieval configuration"
    )
    generation_config: Optional[GenerationConfigRequest] = Field(
        default=None,
        description="Updated generation configuration"
    )
    metadata: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Updated session metadata"
    )

    class Config:
        use_enum_values = True


class SessionResponse(BaseModel):
    """Response containing session information."""
    session_id: str = Field(..., description="Unique session identifier")
    rag_strategy: str = Field(..., description="Current RAG strategy")
    vector_store: str = Field(..., description="Current vector store")
    collection_name: str = Field(..., description="Current collection name")
    retrieval_config: Dict[str, Any] = Field(default_factory=dict, description="Retrieval configuration")
    generation_config: Dict[str, Any] = Field(default_factory=dict, description="Generation configuration")
    strategy_config: Dict[str, Any] = Field(default_factory=dict, description="Strategy-specific configuration")
    created_at: datetime = Field(..., description="Session creation timestamp")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Session metadata")


class SessionListResponse(BaseModel):
    """Response containing list of sessions."""
    sessions: List[SessionResponse] = Field(default_factory=list)
    total: int = Field(..., description="Total number of active sessions")


# ============== Chat ==============

class ChatRequest(BaseModel):
    """Request to send a chat message."""
    session_id: str = Field(..., description="Session ID to use for this chat")
    message: str = Field(
        default="",
        description="User message/query (optional if using image)"
    )
    query_image_base64: Optional[str] = Field(
        default=None,
        description="Base64-encoded image for multimodal query"
    )
    # Optional overrides for this specific message
    top_k: Optional[int] = Field(
        default=None,
        ge=1,
        le=100,
        description="Override top_k for this query"
    )
    filters: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Override filters for this query"
    )
    system_prompt: Optional[str] = Field(
        default=None,
        description="Override system prompt for this query"
    )


class SourceDocument(BaseModel):
    """Source document information in response."""
    id: str
    content: str
    score: float
    metadata: Dict[str, Any] = {}
    source: Optional[str] = None


class ChatResponse(BaseModel):
    """Response from chat endpoint."""
    answer: str = Field(..., description="Generated answer")
    sources: List[SourceDocument] = Field(
        default_factory=list,
        description="Source documents used for the answer"
    )
    session_id: str = Field(..., description="Session ID used")
    rag_strategy: str = Field(..., description="RAG strategy used")
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


class ErrorResponse(BaseModel):
    """Error response model."""
    error: str
    detail: Optional[str] = None
    status_code: int

