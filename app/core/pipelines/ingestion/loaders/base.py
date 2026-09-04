"""
Base Document Loader

Abstract base class for document loaders.
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
from pathlib import Path
import hashlib
import uuid


@dataclass
class Document:
    """Represents a loaded document."""
    content: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    source: Optional[str] = None
    doc_id: Optional[str] = None

    def __post_init__(self):
        """Generate doc_id if not provided."""
        if self.doc_id is None:
            # Generate UUID for document ID
            # Store source path in metadata instead of ID
            self.doc_id = str(uuid.uuid4())
            # Ensure source is stored in metadata for reference
            if self.source and "source" not in self.metadata:
                self.metadata["source"] = self.source


class DocumentLoader(ABC):
    """
    Abstract base class for document loaders.
    
    Implementations should handle loading documents from various sources
    (files, URLs, databases, etc.) and converting them to Document objects.
    """

    @abstractmethod
    async def load(self, source: str) -> List[Document]:
        """
        Load documents from the given source.
        
        Args:
            source: Path to file, URL, or other source identifier.
            
        Returns:
            List of Document objects.
        """
        pass

    @abstractmethod
    async def load_directory(self, directory: str, pattern: str = "*") -> List[Document]:
        """
        Load all matching documents from a directory.
        
        Args:
            directory: Path to directory.
            pattern: Glob pattern for file matching.
            
        Returns:
            List of Document objects.
        """
        pass

    def _get_file_metadata(self, file_path: Path) -> Dict[str, Any]:
        """
        Extract metadata from a file.
        
        Args:
            file_path: Path to the file.
            
        Returns:
            Metadata dictionary.
        """
        return {
            "filename": file_path.name,
            "file_path": str(file_path.absolute()),
            "file_size": file_path.stat().st_size if file_path.exists() else 0,
            "file_type": file_path.suffix.lower(),
        }

