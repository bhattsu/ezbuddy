"""
Ingestion API Schemas

Pydantic models for ingestion endpoint request/response validation.
"""

from typing import Optional, Dict, Any
from pydantic import BaseModel, Field


class IngestionRequest(BaseModel):
    """Base request model for ingestion operations."""
    vector_store: str = Field(
        default="pgvector",
        description="Vector store to use"
    )
    collection_name: str = Field(
        ...,
        description="Name of the collection to store documents"
    )
    chunk_size: int = Field(
        default=1000,
        ge=100,
        le=10000,
        description="Target chunk size in characters"
    )
    chunk_overlap: int = Field(
        default=200,
        ge=0,
        le=1000,
        description="Overlap between chunks"
    )
    chunker_type: str = Field(
        default="fixed",
        description="Chunking strategy: fixed, semantic, or recursive"
    )
    reset_collection: bool = Field(
        default=False,
        description="Whether to reset/clear the collection before ingestion"
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Additional metadata to attach to documents"
    )


class FileIngestionRequest(IngestionRequest):
    """Request model for file ingestion."""
    loader_type: str = Field(
        default="auto",
        description="Loader type: auto, text, txt, pdf, json, textract, or image"
    )


class TextIngestionRequest(IngestionRequest):
    """Request model for text ingestion."""
    source: str = Field(
        default="direct_input",
        description="Source identifier for the text"
    )


class DirectoryIngestionRequest(IngestionRequest):
    """Request model for directory ingestion."""
    pattern: str = Field(
        default="*",
        description="Glob pattern for file matching"
    )
    loader_type: str = Field(
        default="auto",
        description="Loader type: auto, text, txt, pdf, json, textract, or image"
    )


class IngestionResponse(BaseModel):
    """Response model for ingestion operations."""
    success: bool = Field(..., description="Whether ingestion was successful")
    total_documents: int = Field(..., description="Total number of documents processed")
    total_chunks: int = Field(..., description="Total number of chunks created")
    vectors_stored: int = Field(..., description="Number of vectors successfully stored")
    failed_chunks: int = Field(..., description="Number of chunks that failed")
    collection_name: str = Field(..., description="Collection name")
    message: str = Field(..., description="Status message")
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Additional metadata about the ingestion"
    )

