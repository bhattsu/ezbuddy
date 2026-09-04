"""
Base RAG Strategy Interface

This module defines the abstract base class for RAG strategy implementations.
Naive RAG inherits from RAGStrategy.
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
from enum import Enum
from pydantic import BaseModel


class RAGType(Enum):
    """Supported RAG strategy types."""
    NAIVE = "naive"


@dataclass
class RetrievedDocument:
    """Represents a document retrieved from the vector store."""
    id: str
    content: str
    score: float
    metadata: Dict[str, Any] = field(default_factory=dict)
    source: Optional[str] = None


@dataclass
class RetrievalContext:
    """Context for retrieval operations."""
    collection_name: str
    top_k: int = 10
    filters: Optional[Dict[str, Any]] = None
    rerank: bool = False
    hybrid: bool = False
    score_threshold: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)
    # Multimodal support
    query_text: Optional[str] = None
    query_image_base64: Optional[str] = None
    query_image_bytes: Optional[bytes] = None


@dataclass 
class RAGConfig:
    """Configuration for RAG execution."""
    vector_store: str  # Name of the vector store adapter to use
    collection_name: str
    top_k: int = 10
    filters: Optional[Dict[str, Any]] = None
    rerank: bool = False
    hybrid: bool = False
    score_threshold: float = 0.0
    temperature: float = 0.7
    max_tokens: int = 2000
    system_prompt: Optional[str] = None
    extra_params: Dict[str, Any] = field(default_factory=dict)


class SourceDocument(BaseModel):
    """Source document information for response."""
    id: str
    content: str
    score: float
    metadata: Dict[str, Any] = {}
    source: Optional[str] = None


class RAGResponse(BaseModel):
    """Standard response from RAG execution."""
    answer: str
    sources: List[SourceDocument]
    rag_type: str
    vector_store: str
    confidence: float
    metadata: Dict[str, Any] = {}
    
    class Config:
        arbitrary_types_allowed = True


class RAGStrategy(ABC):
    """
    Abstract base class for RAG strategies.
    
    Naive RAG inherits from this class and must implement the abstract methods
    defined below to ensure pluggability.
    """

    def __init__(self, vector_store_adapter, llm_client, embedder):
        """
        Initialize the RAG strategy.
        
        Args:
            vector_store_adapter: The vector store adapter to use for retrieval.
            llm_client: The LLM client for generation.
            embedder: The embedding model/client for query embedding.
        """
        self.vector_store = vector_store_adapter
        self.llm = llm_client
        self.embedder = embedder

    @property
    @abstractmethod
    def rag_type(self) -> RAGType:
        """Return the type of this RAG strategy."""
        pass

    @abstractmethod
    async def retrieve(
        self,
        query: str,
        context: RetrievalContext
    ) -> List[RetrievedDocument]:
        """
        Retrieve relevant documents for the query.
        
        Args:
            query: The user query string (can be text or empty if using image query).
            context: Retrieval configuration and context.
                     For multimodal queries, use context.query_image_base64 or context.query_image_bytes.
            
        Returns:
            List of retrieved documents ordered by relevance.
        """
        pass

    @abstractmethod
    async def generate(
        self,
        query: str,
        documents: List[RetrievedDocument],
        system_prompt: Optional[str] = None,
        **kwargs
    ) -> str:
        """
        Generate an answer based on retrieved documents.
        
        Args:
            query: The user query string.
            documents: List of retrieved documents for context.
            system_prompt: Optional system prompt override.
            **kwargs: Additional generation parameters.
            
        Returns:
            Generated answer string.
        """
        pass

    @abstractmethod
    async def execute(
        self,
        query: str,
        config: RAGConfig
    ) -> RAGResponse:
        """
        Execute the full RAG pipeline: retrieve + generate.
        
        Args:
            query: The user query string.
            config: RAG configuration parameters.
            
        Returns:
            RAGResponse containing answer, sources, and metadata.
        """
        pass

    def _separate_image_documents(
        self,
        documents: List[RetrievedDocument]
    ) -> tuple[List[RetrievedDocument], List[RetrievedDocument]]:
        """
        Separate documents into image and text documents.
        
        Args:
            documents: List of retrieved documents.
            
        Returns:
            Tuple of (image_documents, text_documents).
        """
        image_documents = []
        text_documents = []
        for doc in documents:
            is_multimodal = (
                doc.metadata.get("multimodal", False) or 
                doc.metadata.get("content_type") == "image"
            )
            if is_multimodal:
                image_documents.append(doc)
            else:
                text_documents.append(doc)
        return image_documents, text_documents
    
    def _detect_image_mime_type(self, image_bytes: bytes) -> str:
        """
        Detect image MIME type from image bytes by checking magic bytes.
        
        Args:
            image_bytes: Raw image bytes.
            
        Returns:
            MIME type string (defaults to 'image/png' if unknown).
        """
        if not image_bytes or len(image_bytes) < 4:
            return "image/png"
        
        # Check magic bytes for common image formats
        header = image_bytes[:4]
        
        # PNG: 89 50 4E 47
        if header[:4] == b'\x89PNG':
            return "image/png"
        # JPEG: FF D8 FF
        elif header[:3] == b'\xFF\xD8\xFF':
            return "image/jpeg"
        # GIF: 47 49 46 38
        elif header[:4] == b'GIF8':
            return "image/gif"
        # WebP: RIFF...WEBP
        elif len(image_bytes) >= 12 and image_bytes[:4] == b'RIFF' and image_bytes[8:12] == b'WEBP':
            return "image/webp"
        # BMP: 42 4D
        elif header[:2] == b'BM':
            return "image/bmp"
        # TIFF: 49 49 2A 00 (little-endian) or 4D 4D 00 2A (big-endian)
        elif header[:2] == b'II' and header[2:4] == b'\x2A\x00':
            return "image/tiff"
        elif header[:2] == b'MM' and header[2:4] == b'\x00\x2A':
            return "image/tiff"
        else:
            # Default to PNG if format cannot be determined
            return "image/png"
    
    def _build_image_content_blocks(
        self,
        image_documents: List[RetrievedDocument]
    ) -> list:
        """
        Build image content blocks for Claude 3 multimodal format.
        
        Args:
            image_documents: List of image documents.
            
        Returns:
            List of image content blocks.
        """
        content = []
        for i, doc in enumerate(image_documents, 1):
            image_base64 = doc.content
            if image_base64:
                # Determine MIME type from metadata
                image_format = doc.metadata.get("image_format", ".png")
                mime_type_map = {
                    ".png": "image/png",
                    ".jpg": "image/jpeg",
                    ".jpeg": "image/jpeg",
                    ".gif": "image/gif",
                    ".webp": "image/webp",
                    ".bmp": "image/bmp",
                    ".tiff": "image/tiff",
                    ".tif": "image/tiff"
                }
                mime_type = mime_type_map.get(image_format.lower(), "image/png")
                
                content.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": mime_type,
                        "data": image_base64
                    }
                })
        return content

    def _build_context(self, documents: List[RetrievedDocument]) -> str:
        """
        Build context string from retrieved documents.
        
        Args:
            documents: List of retrieved documents.
            
        Returns:
            Formatted context string.
        """
        context_parts = []
        for i, doc in enumerate(documents, 1):
            source_info = f" (Source: {doc.source})" if doc.source else ""
            
            # Check if this is an image/multimodal document
            is_multimodal = (
                doc.metadata.get("multimodal", False) or 
                doc.metadata.get("content_type") == "image"
            )
            
            if is_multimodal:
                # For image documents, don't include the full base64 content (it's too large)
                # Instead, provide a reference that the LLM can understand
                image_info = f"Image document"
                
                # Include filename if available
                filename = doc.metadata.get("filename") or doc.source
                if filename:
                    # Extract just the filename from path if it's a full path
                    import os
                    filename_only = os.path.basename(filename) if os.path.sep in filename or '/' in filename else filename
                    image_info += f" (filename: {filename_only})"
                
                if doc.metadata.get("image_format"):
                    image_info += f" (format: {doc.metadata['image_format']})"
                if doc.metadata.get("image_size_bytes"):
                    size_kb = doc.metadata['image_size_bytes'] / 1024
                    image_info += f" (size: {size_kb:.1f} KB)"
                
                content_preview = "[Image content - retrieved from vector store based on visual similarity. The actual image is provided in the message content below.]"
                context_parts.append(
                    f"[Document {i}]{source_info}:\n{image_info}\n{content_preview}"
                )
            else:
                # For text documents, include the full content
                context_parts.append(
                    f"[Document {i}]{source_info}:\n{doc.content}"
                )
        return "\n\n".join(context_parts)

    def _calculate_confidence(self, documents: List[RetrievedDocument]) -> float:
        """
        Calculate confidence score based on retrieved documents.
        
        Args:
            documents: List of retrieved documents with scores.
            
        Returns:
            Confidence score between 0 and 1.
        """
        if not documents:
            return 0.0
        
        # Average of top document scores, weighted by position
        total_weight = 0
        weighted_sum = 0
        for i, doc in enumerate(documents):
            weight = 1 / (i + 1)  # Higher weight for top results
            weighted_sum += doc.score * weight
            total_weight += weight
        
        return min(weighted_sum / total_weight, 1.0) if total_weight > 0 else 0.0

    def _documents_to_sources(
        self,
        documents: List[RetrievedDocument]
    ) -> List[SourceDocument]:
        """
        Convert retrieved documents to source documents for response.
        
        Args:
            documents: List of retrieved documents.
            
        Returns:
            List of SourceDocument objects.
        """
        source_docs = []
        for doc in documents:
            # Check if this is an image/multimodal document
            is_multimodal = (
                doc.metadata.get("multimodal", False) or 
                doc.metadata.get("content_type") == "image"
            )
            
            # For image documents, don't truncate content (base64 images need full content)
            # For text documents, truncate to 500 chars for response size
            content = doc.content if is_multimodal else doc.content[:500]
            
            source_docs.append(
                SourceDocument(
                    id=doc.id,
                    content=content,
                    score=doc.score,
                    metadata=doc.metadata,
                    source=doc.source
                )
            )
        return source_docs

    def get_default_system_prompt(self) -> str:
        """
        Get the default system prompt for this RAG strategy.
        
        Returns:
            Default system prompt string.
        """
        return """You are a helpful assistant that answers questions based on the provided context and images.

When analyzing images:
- Provide accurate, detailed descriptions of what you see
- Be specific about objects, people, activities, colors, and settings
- If multiple images are provided, describe each one clearly and distinguish between them
- Cite image sources when referencing specific images
- If you're uncertain about details, acknowledge the uncertainty rather than making assumptions
        
Instructions:
- Only use information from the provided context to answer questions.
- If the context doesn't contain enough information to answer, say so clearly.
- Cite your sources by referencing document numbers when possible.
- Be concise and accurate in your responses."""

