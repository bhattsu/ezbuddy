"""
JSON Document Loader

Loader for JSON and JSONL files.
"""

import json
import logging
from typing import List, Optional
from pathlib import Path

from app.core.pipelines.ingestion.loaders.base import DocumentLoader, Document

logger = logging.getLogger(__name__)


class JSONLoader(DocumentLoader):
    """
    Loader for JSON and JSONL files.
    
    Supports both single JSON objects and JSON Lines format.
    """

    def __init__(
        self,
        content_key: str = "content",
        metadata_keys: Optional[List[str]] = None,
        jq_filter: Optional[str] = None
    ):
        """
        Initialize the JSON loader.
        
        Args:
            content_key: Key to extract as document content.
            metadata_keys: Keys to extract as metadata.
            jq_filter: Optional JQ filter for complex extraction (not implemented).
        """
        self.content_key = content_key
        self.metadata_keys = metadata_keys or []
        self.jq_filter = jq_filter
        logger.debug(f"Initialized JSONLoader with content_key='{self.content_key}', metadata_keys={self.metadata_keys}, jq_filter={self.jq_filter}")

    def _extract_document(self, data: dict, source: str) -> Optional[Document]:
        """
        Extract a Document from a JSON object.
        
        Args:
            data: JSON object as dictionary.
            source: Source identifier.
            
        Returns:
            Document if content found, None otherwise.
        """
        logger.debug(f"Attempting to extract document from source: {source}")
        # Get content
        content = data.get(self.content_key)
        if not content:
            logger.debug(f"Content key '{self.content_key}' not found in data. Trying common content keys.")
            # Try common content keys
            for key in ["text", "body", "description", "message"]:
                if key in data:
                    logger.debug(f"Found content in fallback key '{key}'")
                    content = data[key]
                    break
        
        if not content:
            logger.warning(f"No content found for document in source: {source}")
            return None
        
        # Convert to string if needed
        if not isinstance(content, str):
            logger.debug(f"Content is not a string for source: {source}, serializing to JSON string.")
            content = json.dumps(content)
        
        # Extract metadata
        metadata = {"loader": "JSONLoader"}
        for key in self.metadata_keys:
            if key in data:
                metadata[key] = data[key]
                logger.debug(f"Added metadata key '{key}': {data[key]} to document in source: {source}")
        
        # Include all non-content keys if no specific keys specified
        if not self.metadata_keys:
            for key, value in data.items():
                if key != self.content_key and isinstance(value, (str, int, float, bool)):
                    metadata[key] = value
        
        logger.debug(f"Extracted document from {source} with metadata keys: {list(metadata.keys())}")
        return Document(
            content=content,
            metadata=metadata,
            source=source,
        )

    async def load(self, source: str) -> List[Document]:
        """
        Load a JSON or JSONL file.
        
        Args:
            source: Path to the JSON file.
            
        Returns:
            List of Document objects.
        """
        logger.info(f"Starting to load JSON from: {source}")
        file_path = Path(source)
        
        if not file_path.exists():
            logger.error(f"File not found: {source}")
            return []
        
        documents = []
        
        try:
            content = file_path.read_text(encoding="utf-8")
            logger.debug(f"Read file '{source}' (length={len(content)})")
            
            # Check if JSONL (one JSON object per line)
            if file_path.suffix.lower() == ".jsonl":
                logger.info(f"Detected JSONL format in file: {source}")
                for i, line in enumerate(content.strip().split("\n")):
                    logger.debug(f"Processing line {i+1} of JSONL: {line[:80]}...")  # Show up to first 80 chars
                    if line.strip():
                        try:
                            data = json.loads(line)
                            doc = self._extract_document(data, f"{source}:line{i+1}")
                            if doc:
                                documents.append(doc)
                                logger.debug(f"Added document from {source}:line{i+1}")
                            else:
                                logger.warning(f"No valid document extracted from {source}:line{i+1}")
                        except json.JSONDecodeError as e:
                            logger.warning(f"Invalid JSON at line {i+1}: {e}")
            else:
                logger.info(f"Detected regular JSON format in file: {source}")
                # Regular JSON file
                data = json.loads(content)
                
                # Handle array of objects
                if isinstance(data, list):
                    logger.debug(f"Top-level array detected in JSON file: {source} (length={len(data)})")
                    for i, item in enumerate(data):
                        if isinstance(item, dict):
                            doc = self._extract_document(item, f"{source}:item{i}")
                            if doc:
                                documents.append(doc)
                                logger.debug(f"Added document from {source}:item{i}")
                            else:
                                logger.warning(f"No valid document extracted from {source}:item{i}")
                elif isinstance(data, dict):
                    logger.debug(f"Top-level dict detected in JSON file: {source}")
                    # Check if it contains an array
                    found_nested_list = False
                    for key, value in data.items():
                        if isinstance(value, list) and value and isinstance(value[0], dict):
                            logger.debug(f"Detected nested array in key '{key}' in file: {source} (length={len(value)})")
                            for i, item in enumerate(value):
                                doc = self._extract_document(item, f"{source}:{key}[{i}]")
                                if doc:
                                    documents.append(doc)
                                    logger.debug(f"Added document from {source}:{key}[{i}]")
                                else:
                                    logger.warning(f"No valid document extracted from {source}:{key}[{i}]")
                            found_nested_list = True
                            break
                    if not found_nested_list:
                        # Single object
                        doc = self._extract_document(data, source)
                        if doc:
                            documents.append(doc)
                            logger.debug(f"Added single document from {source}")
                        else:
                            logger.warning(f"No valid document extracted from {source} (single object)")
            
            # Add file metadata to all documents
            file_metadata = self._get_file_metadata(file_path)
            logger.debug(f"Adding file metadata to {len(documents)} documents from {source}: {file_metadata}")
            for doc in documents:
                doc.metadata.update(file_metadata)
            
            logger.info(f"Loaded {len(documents)} documents from JSON: {source}")
            return documents
            
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in {source}: {e}")
            return []
        except Exception as e:
            logger.error(f"Failed to load JSON {source}: {e}")
            return []

    async def load_directory(self, directory: str, pattern: str = "*.json") -> List[Document]:
        """
        Load all JSON files from a directory.
        
        Args:
            directory: Path to directory.
            pattern: Glob pattern for file matching.
            
        Returns:
            List of Document objects.
        """
        logger.info(f"Loading all JSON files in directory: {directory} with pattern: '{pattern}'")
        dir_path = Path(directory)
        
        if not dir_path.exists():
            logger.error(f"Directory not found: {directory}")
            return []
        
        documents = []
        for file_path in dir_path.glob(pattern):
            if file_path.is_file():
                logger.debug(f"Attempting to load JSON file: {str(file_path)}")
                docs = await self.load(str(file_path))
                documents.extend(docs)
                logger.debug(f"Loaded {len(docs)} documents from {str(file_path)}")
        
        # Also load JSONL files
        for file_path in dir_path.glob("*.jsonl"):
            if file_path.is_file():
                logger.debug(f"Attempting to load JSONL file: {str(file_path)}")
                docs = await self.load(str(file_path))
                documents.extend(docs)
                logger.debug(f"Loaded {len(docs)} documents from {str(file_path)}")
        
        logger.info(f"Loaded {len(documents)} documents from JSON files in {directory}")
        return documents

