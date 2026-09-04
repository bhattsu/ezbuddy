"""
Text Document Loader

Loader for plain text files (.txt, .md, etc.).
"""

import logging
from typing import List
from pathlib import Path

from app.core.pipelines.ingestion.loaders.base import DocumentLoader, Document

logger = logging.getLogger(__name__)


class TextLoader(DocumentLoader):
    """
    Loader for plain text files.
    
    Supports .txt, .md, .rst, and other text-based formats.
    """

    def __init__(self, encoding: str = "utf-8"):
        """
        Initialize the text loader.
        
        Args:
            encoding: File encoding to use when reading.
        """
        self.encoding = encoding
        self.supported_extensions = {".txt", ".md", ".rst", ".text", ".markdown"}
        logger.debug(f"TextLoader initialized with encoding={self.encoding} and supported_extensions={self.supported_extensions}")

    async def load(self, source: str) -> List[Document]:
        """
        Load a text file.
        
        Args:
            source: Path to the text file.
            
        Returns:
            List containing one Document.
        """
        file_path = Path(source)
        logger.debug(f"Attempting to load file: {source}")

        if not file_path.exists():
            logger.error(f"File not found: {source}")
            return []
        
        try:
            logger.debug(f"Reading text file: {source} with encoding {self.encoding}")
            try:
                content = file_path.read_text(encoding=self.encoding)
            except UnicodeDecodeError:
                logger.warning(
                    "UTF-8 decode failed for %s; retrying with latin-1",
                    source,
                )
                content = file_path.read_text(encoding="latin-1")
            
            metadata = self._get_file_metadata(file_path)
            metadata["loader"] = "TextLoader"
            logger.debug(f"Metadata for {source}: {metadata}")
            
            doc = Document(
                content=content,
                metadata=metadata,
                source=str(file_path),
            )
            
            logger.info(f"Loaded text file: {source} ({len(content)} chars)")
            return [doc]
            
        except Exception as e:
            logger.error(f"Failed to load text file {source}: {e}")
            return []

    async def load_directory(self, directory: str, pattern: str = "*.txt") -> List[Document]:
        """
        Load all text files from a directory.
        
        Args:
            directory: Path to directory.
            pattern: Glob pattern for file matching.
            
        Returns:
            List of Document objects.
        """
        dir_path = Path(directory)
        logger.debug(f"Attempting to load directory: {directory} with pattern: {pattern}")

        if not dir_path.exists():
            logger.error(f"Directory not found: {directory}")
            return []
        
        documents = []
        num_files = 0
        for file_path in dir_path.glob(pattern):
            if file_path.is_file() and file_path.suffix.lower() in self.supported_extensions:
                logger.debug(f"Found supported text file: {file_path}")
                docs = await self.load(str(file_path))
                documents.extend(docs)
                num_files += 1
            else:
                logger.debug(f"Skipping file (unsupported extension or not a file): {file_path}")
        
        logger.info(f"Loaded {len(documents)} text files from {directory} (matched {num_files} files by pattern {pattern})")
        return documents

