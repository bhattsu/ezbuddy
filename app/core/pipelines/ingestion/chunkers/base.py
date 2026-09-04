"""
Base Chunking Strategy

Abstract base class for document chunking.
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
import hashlib
import uuid


@dataclass
class Chunk:
    """Represents a document chunk."""
    content: str
    chunk_id: str
    doc_id: str
    chunk_index: int
    metadata: Dict[str, Any] = field(default_factory=dict)
    start_char: Optional[int] = None
    end_char: Optional[int] = None

    def __post_init__(self):
        """Generate chunk_id if not set."""
        if not self.chunk_id:
            # Generate UUID for chunk ID
            # Store doc_id and chunk_index in metadata instead of ID
            self.chunk_id = str(uuid.uuid4())
            # Ensure doc_id and chunk_index are in metadata for reference
            if "doc_id" not in self.metadata:
                self.metadata["doc_id"] = self.doc_id
            if "chunk_index" not in self.metadata:
                self.metadata["chunk_index"] = self.chunk_index


class ChunkingStrategy(ABC):
    """
    Abstract base class for chunking strategies.
    
    Implementations should handle splitting documents into smaller chunks
    suitable for embedding and retrieval.
    """

    @abstractmethod
    def chunk(self, text: str, doc_id: str, metadata: Dict[str, Any] = None) -> List[Chunk]:
        """
        Split text into chunks.
        
        Args:
            text: The text to chunk.
            doc_id: ID of the source document.
            metadata: Optional metadata to include in chunks.
            
        Returns:
            List of Chunk objects.
        """
        pass

    def chunk_documents(self, documents: List[Any]) -> List[Chunk]:
        """
        Chunk multiple documents.
        
        Args:
            documents: List of Document objects.
            
        Returns:
            List of Chunk objects from all documents.
        """
        all_chunks = []
        for doc in documents:
            chunks = self.chunk(
                text=doc.content,
                doc_id=doc.doc_id,
                metadata=doc.metadata
            )
            all_chunks.extend(chunks)
        return all_chunks

