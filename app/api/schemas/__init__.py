"""
API Schemas Module

This module provides Pydantic models for API request/response validation.
"""

from app.api.schemas.rag import (
    RAGQueryRequest,
    RAGResponse,
    SourceDocument,
    ErrorResponse,
    HealthResponse,
)
from app.api.schemas.ingestion import (
    IngestionRequest,
    FileIngestionRequest,
    TextIngestionRequest,
    DirectoryIngestionRequest,
    IngestionResponse,
)
from app.api.schemas.chatbot import (
    CreateSessionRequest,
    UpdateSessionRequest,
    SessionResponse,
    SessionListResponse,
    ChatRequest,
    ChatResponse,
)

__all__ = [
    # RAG schemas
    "RAGQueryRequest",
    "RAGResponse",
    "SourceDocument",
    "ErrorResponse",
    "HealthResponse",
    # Ingestion schemas
    "IngestionRequest",
    "FileIngestionRequest",
    "TextIngestionRequest",
    "DirectoryIngestionRequest",
    "IngestionResponse",
    # Chatbot schemas
    "CreateSessionRequest",
    "UpdateSessionRequest",
    "SessionResponse",
    "SessionListResponse",
    "ChatRequest",
    "ChatResponse",
]
