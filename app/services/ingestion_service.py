"""
Ingestion Service

Business logic for document ingestion operations.
Handles document loading, chunking, embedding, and storage.
"""

import logging
import os
import tempfile
import zipfile
from typing import Optional, Dict, Any, List
from pathlib import Path

from app.core.pipelines.ingestion.pipeline import IngestionPipeline, IngestionConfig, IngestionResult
from app.core.pipelines.ingestion.loaders.base import DocumentLoader
from app.core.pipelines.ingestion.loaders.text import TextLoader
from app.core.pipelines.ingestion.loaders.pdf import PDFLoader
from app.core.pipelines.ingestion.loaders.json_loader import JSONLoader
from app.core.pipelines.ingestion.loaders.textract import TextractLoader
from app.core.pipelines.ingestion.loaders.image import ImageLoader
from app.core.pipelines.ingestion.loaders.docx_loader import DocxLoader
from app.core.pipelines.ingestion.chunkers.base import ChunkingStrategy
from app.core.pipelines.ingestion.chunkers.fixed import FixedSizeChunker
from app.core.pipelines.ingestion.chunkers.semantic import SemanticChunker
from app.core.pipelines.ingestion.chunkers.recursive import RecursiveChunker
from app.core.pipelines.ingestion.chunkers.court_rules import CourtRulesChunker
from app.adapters.vector_store.base import VectorStoreAdapter

logger = logging.getLogger(__name__)


class IngestionService:
    """
    Service for ingesting documents into vector stores.
    
    Handles document loading, chunking, embedding, and storage.
    """

    def __init__(
        self,
        vector_store: VectorStoreAdapter,
        embedder: Any
    ):
        """
        Initialize the ingestion service.
        
        Args:
            vector_store: Vector store adapter for storage.
            embedder: Embedding model/client.
        """
        self.vector_store = vector_store
        self.embedder = embedder
        logger.debug(f"IngestionService initialized with vector_store={vector_store} embedder={embedder}")

    def _get_loader(
        self,
        loader_type: str,
        file_extension: Optional[str] = None
    ) -> DocumentLoader:
        """
        Get the appropriate document loader.
        
        Args:
            loader_type: Type of loader ("auto", "text", "pdf", "json", "textract").
            file_extension: File extension for auto-detection.
            
        Returns:
            Document loader instance.
        """
        logger.debug(f"Selecting loader: loader_type={loader_type}, file_extension={file_extension}")
        if loader_type == "auto":
            if file_extension:
                # Auto-detect based on extension
                if file_extension.lower() == ".pdf":
                    logger.info("Auto-selecting PDFLoader for .pdf file")
                    return PDFLoader()
                elif file_extension.lower() in [".json", ".jsonl"]:
                    logger.info("Auto-selecting JSONLoader for json/jsonl file")
                    return JSONLoader()
                elif file_extension.lower() in [".txt", ".text", ".md", ".markdown", ".rst"]:
                    logger.info("Auto-selecting TextLoader for text file (%s)", file_extension)
                    return TextLoader()
                elif file_extension.lower() in [".docx"]:
                    logger.info("Auto-selecting DocxLoader for .docx file")
                    return DocxLoader()
                elif file_extension.lower() in [".png", ".jpg", ".jpeg", ".tiff", ".tif", ".webp", ".bmp"]:
                    # Check if multimodal embedding is available
                    if hasattr(self.embedder, 'embed_multimodal') and hasattr(self.embedder, 'multimodal_embedding_model_id') and self.embedder.multimodal_embedding_model_id:
                        logger.info("Auto-selecting ImageLoader for multimodal image embedding")
                        return ImageLoader()
                    else:
                        logger.info("Auto-selecting TextractLoader for OCR (multimodal not configured)")
                        return TextractLoader()
                else:
                    logger.info("Auto-selecting TextLoader for unrecognized extension %s", file_extension)
                    return TextLoader()
            else:
                # No file extension provided for auto mode - default to TextLoader
                logger.warning("Auto loader type specified but no file_extension provided. Defaulting to TextLoader.")
                return TextLoader()
        elif loader_type in ("text", "txt"):
            logger.info("Using TextLoader as specified (loader_type=%s)", loader_type)
            return TextLoader()
        elif loader_type == "pdf":
            logger.info("Using PDFLoader as specified")
            return PDFLoader()
        elif loader_type == "json":
            logger.info("Using JSONLoader as specified")
            return JSONLoader()
        elif loader_type == "textract":
            logger.info("Using TextractLoader as specified")
            return TextractLoader()
        elif loader_type == "image":
            logger.info("Using ImageLoader as specified")
            return ImageLoader()
        elif loader_type in ("docx", "word"):
            logger.info("Using DocxLoader as specified")
            return DocxLoader()
        else:
            logger.error(f"Unknown loader type: {loader_type}")
            raise ValueError(f"Unknown loader type: {loader_type}")

    def _normalize_pattern(self, pattern: str, recursive: bool = True) -> str:
        """
        Normalize glob pattern for file matching.
        
        Args:
            pattern: Glob pattern (e.g., ".png", "*.png", "*", "**/*.png").
            recursive: Whether to search recursively in subdirectories.
            
        Returns:
            Normalized glob pattern.
        """
        if not pattern or pattern == "*":
            # Match all files, recursively if requested
            return "**/*" if recursive else "*"
        
        # If pattern starts with just an extension (e.g., ".png"), convert to "*.png"
        if pattern.startswith(".") and "*" not in pattern:
            normalized = f"*{pattern}"
        else:
            normalized = pattern
        
        # If recursive and pattern doesn't already have recursive indicator, add it
        if recursive and not normalized.startswith("**/"):
            # Check if it's a simple pattern like "*.ext" that should be made recursive
            if normalized.startswith("*") and "/" not in normalized:
                normalized = f"**/{normalized}"
        
        return normalized

    def _get_chunker(
        self,
        chunker_type: str,
        chunk_size: int,
        chunk_overlap: int
    ) -> ChunkingStrategy:
        """
        Get the appropriate chunking strategy.
        
        Args:
            chunker_type: Type of chunker ("fixed", "semantic", "recursive").
            chunk_size: Target chunk size.
            chunk_overlap: Chunk overlap size.
            
        Returns:
            Chunking strategy instance.
        """
        logger.debug(f"Selecting chunker: chunker_type={chunker_type}, chunk_size={chunk_size}, chunk_overlap={chunk_overlap}")
        if chunker_type == "fixed":
            logger.info("Using FixedSizeChunker")
            return FixedSizeChunker(
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap
            )
        elif chunker_type == "semantic":
            logger.info("Using SemanticChunker")
            return SemanticChunker(
                max_chunk_size=chunk_size,
                min_chunk_size=chunk_overlap
            )
        elif chunker_type == "recursive":
            logger.info("Using RecursiveChunker")
            return RecursiveChunker(
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap
            )
        elif chunker_type in ("court_rules", "court-rules"):
            logger.info("Using CourtRulesChunker")
            return CourtRulesChunker(
                max_chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
            )
        else:
            logger.error(f"Unknown chunker type: {chunker_type}")
            raise ValueError(f"Unknown chunker type: {chunker_type}")

    async def ingest_file(
        self,
        file_path: str,
        collection_name: str,
        chunk_size: int = 1000,
        chunk_overlap: int = 200,
        chunker_type: str = "fixed",
        loader_type: str = "auto",
        reset_collection: bool = False,
        metadata: Dict[str, Any] = None
    ) -> IngestionResult:
        """
        Ingest a single file.
        
        Args:
            file_path: Path to the file.
            collection_name: Name of the collection.
            chunk_size: Target chunk size.
            chunk_overlap: Chunk overlap.
            chunker_type: Type of chunker to use.
            loader_type: Type of loader to use.
            reset_collection: Whether to reset the collection.
            metadata: Additional metadata.
            
        Returns:
            IngestionResult with statistics.
        """
        logger.info(f"Starting ingestion for file: {file_path}, collection: {collection_name}, loader_type: {loader_type}, chunker_type: {chunker_type}")
        file_extension = os.path.splitext(file_path)[1]
        loader = self._get_loader(loader_type, file_extension)
        chunker = self._get_chunker(chunker_type, chunk_size, chunk_overlap)
        
        pipeline = IngestionPipeline(
            vector_store=self.vector_store,
            embedder=self.embedder,
            chunker=chunker,
            loader=loader
        )
        
        config = IngestionConfig(
            collection_name=collection_name,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            reset_collection=reset_collection,
            metadata=metadata or {}
        )
        
        logger.info(f"Ingestion config: {config}")
        result = await pipeline.ingest_file(file_path, config, loader)
        logger.info(f"Finished ingestion for file: {file_path}. Result: {result}")
        return result

    async def ingest_uploaded_file(
        self,
        file_content: bytes,
        filename: str,
        collection_name: str,
        chunk_size: int = 1000,
        chunk_overlap: int = 200,
        chunker_type: str = "fixed",
        loader_type: str = "auto",
        reset_collection: bool = False,
        metadata: Dict[str, Any] = None
    ) -> IngestionResult:
        """
        Ingest an uploaded file from memory.
        
        Args:
            file_content: File content as bytes.
            filename: Original filename.
            collection_name: Name of the collection.
            chunk_size: Target chunk size.
            chunk_overlap: Chunk overlap.
            chunker_type: Type of chunker to use.
            loader_type: Type of loader to use.
            reset_collection: Whether to reset the collection.
            metadata: Additional metadata.
            
        Returns:
            IngestionResult with statistics.
        """
        # Save to temporary file
        file_extension = os.path.splitext(filename)[1]
        
        logger.info(f"Saving uploaded file '{filename}' to temporary file (extension: {file_extension})")
        with tempfile.NamedTemporaryFile(delete=False, suffix=file_extension) as tmp_file:
            tmp_file.write(file_content)
            tmp_path = tmp_file.name

        logger.debug(f"Temporary file created at: {tmp_path}")
        try:
            result = await self.ingest_file(
                file_path=tmp_path,
                collection_name=collection_name,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                chunker_type=chunker_type,
                loader_type=loader_type,
                reset_collection=reset_collection,
                metadata=metadata
            )
            logger.info(f"Finished ingestion for uploaded file '{filename}' (tmp: {tmp_path}). Result: {result}")
            return result
        finally:
            # Clean up temp file
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
                logger.debug(f"Deleted temporary file at: {tmp_path}")

    async def ingest_text(
        self,
        text: str,
        collection_name: str,
        source: str = "direct_input",
        chunk_size: int = 1000,
        chunk_overlap: int = 200,
        chunker_type: str = "fixed",
        reset_collection: bool = False,
        metadata: Dict[str, Any] = None
    ) -> IngestionResult:
        """
        Ingest raw text directly.
        
        Args:
            text: The text to ingest.
            collection_name: Name of the collection.
            source: Source identifier.
            chunk_size: Target chunk size.
            chunk_overlap: Chunk overlap.
            chunker_type: Type of chunker to use.
            reset_collection: Whether to reset the collection.
            metadata: Additional metadata.
            
        Returns:
            IngestionResult with statistics.
        """
        logger.info(f"Starting ingestion for text (length: {len(text)}) into collection: {collection_name}, chunker_type: {chunker_type}")
        chunker = self._get_chunker(chunker_type, chunk_size, chunk_overlap)
        
        pipeline = IngestionPipeline(
            vector_store=self.vector_store,
            embedder=self.embedder,
            chunker=chunker
        )
        
        config = IngestionConfig(
            collection_name=collection_name,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            reset_collection=reset_collection,
            metadata=metadata or {}
        )
        
        logger.info(f"Ingestion config: {config}")
        result = await pipeline.ingest_text(text, config, source, metadata)
        logger.info(f"Finished ingestion for text. Result: {result}")
        return result

    async def ingest_directory(
        self,
        directory: str,
        collection_name: str,
        pattern: str = "*",
        chunk_size: int = 1000,
        chunk_overlap: int = 200,
        chunker_type: str = "fixed",
        loader_type: str = "auto",
        reset_collection: bool = False,
        metadata: Dict[str, Any] = None
    ) -> IngestionResult:
        """
        Ingest all files from a directory.
        
        Args:
            directory: Path to directory.
            collection_name: Name of the collection.
            pattern: Glob pattern for file matching.
            chunk_size: Target chunk size.
            chunk_overlap: Chunk overlap.
            chunker_type: Type of chunker to use.
            loader_type: Type of loader to use.
            reset_collection: Whether to reset the collection.
            metadata: Additional metadata.
            
        Returns:
            IngestionResult with statistics.
        """
        logger.info(f"Starting directory ingestion from '{directory}' into collection '{collection_name}'. Pattern: '{pattern}', loader_type: '{loader_type}', chunker_type: '{chunker_type}'")
        
        chunker = self._get_chunker(chunker_type, chunk_size, chunk_overlap)
        
        config = IngestionConfig(
            collection_name=collection_name,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            reset_collection=reset_collection,
            metadata=metadata or {}
        )
        
        # Handle "auto" loader type by processing files individually
        if loader_type == "auto":
            logger.info("Using auto loader mode - processing files individually with appropriate loaders")
            dir_path = Path(directory)
            if not dir_path.exists():
                logger.error(f"Directory not found: {directory}")
                raise ValueError(f"Directory not found: {directory}")
            
            # Normalize pattern for glob matching (handle .ext -> *.ext, add recursive if needed)
            normalized_pattern = self._normalize_pattern(pattern, recursive=True)
            logger.debug(f"Normalized pattern '{pattern}' to '{normalized_pattern}' for glob matching")
            
            # Find all matching files (recursively)
            files_found = list(dir_path.glob(normalized_pattern))
            files_found = [f for f in files_found if f.is_file()]
            logger.info(f"Found {len(files_found)} files matching pattern '{pattern}' (normalized: '{normalized_pattern}') in directory '{directory}'")
            
            if not files_found:
                logger.warning(f"No files found matching pattern '{pattern}' in directory '{directory}'")
                # Return empty result
                return IngestionResult(
                    total_documents=0,
                    total_chunks=0,
                    vectors_stored=0,
                    failed_chunks=0,
                    collection_name=collection_name,
                    metadata=metadata or {}
                )
            
            # Process each file with appropriate loader
            total_documents = 0
            total_chunks = 0
            total_vectors = 0
            total_failed = 0
            
            for file_path in files_found:
                file_extension = file_path.suffix
                try:
                    loader = self._get_loader(loader_type, file_extension)
                    pipeline = IngestionPipeline(
                        vector_store=self.vector_store,
                        embedder=self.embedder,
                        chunker=chunker,
                        loader=loader
                    )
                    result = await pipeline.ingest_file(str(file_path), config, loader)
                    total_documents += result.total_documents
                    total_chunks += result.total_chunks
                    total_vectors += result.vectors_stored
                    total_failed += result.failed_chunks
                    logger.info(f"Processed file '{file_path}': {result.total_documents} documents, {result.vectors_stored} vectors")
                except Exception as e:
                    logger.error(f"Failed to process file '{file_path}': {e}")
                    total_failed += 1
            
            result = IngestionResult(
                total_documents=total_documents,
                total_chunks=total_chunks,
                vectors_stored=total_vectors,
                failed_chunks=total_failed,
                collection_name=collection_name,
                metadata=metadata or {}
            )
            logger.info(f"Finished directory ingestion for '{directory}'. Result: {result}")
            return result
        else:
            # Use single loader for all files
            loader = self._get_loader(loader_type)
            pipeline = IngestionPipeline(
                vector_store=self.vector_store,
                embedder=self.embedder,
                chunker=chunker,
                loader=loader
            )
            
            logger.info(f"Ingestion config: {config}")
            result = await pipeline.ingest_directory(directory, config, loader, pattern)
            logger.info(f"Finished directory ingestion for '{directory}'. Result: {result}")
            return result

    async def ingest_zip_file(
        self,
        zip_content: bytes,
        filename: str,
        collection_name: str,
        pattern: str = "*",
        chunk_size: int = 1000,
        chunk_overlap: int = 200,
        chunker_type: str = "fixed",
        loader_type: str = "auto",
        reset_collection: bool = False,
        metadata: Dict[str, Any] = None
    ) -> IngestionResult:
        """
        Ingest files from a zip archive.
        
        Extracts the zip file to a temporary directory and processes all matching files.
        
        Args:
            zip_content: Zip file content as bytes.
            filename: Original zip filename.
            collection_name: Name of the collection.
            pattern: Glob pattern for file matching within zip.
            chunk_size: Target chunk size.
            chunk_overlap: Chunk overlap.
            chunker_type: Type of chunker to use.
            loader_type: Type of loader to use.
            reset_collection: Whether to reset the collection.
            metadata: Additional metadata.
            
        Returns:
            IngestionResult with statistics.
        """
        logger.info(f"Starting zip file ingestion for '{filename}' into collection '{collection_name}'. Pattern: '{pattern}', loader_type: '{loader_type}', chunker_type: '{chunker_type}'")
        
        # Create temporary directory for extraction
        with tempfile.TemporaryDirectory() as temp_dir:
            logger.debug(f"Created temporary directory for zip extraction: {temp_dir}")
            
            # Save zip file to temp location
            zip_path = os.path.join(temp_dir, filename)
            with open(zip_path, 'wb') as f:
                f.write(zip_content)
            
            logger.debug(f"Saved zip file to: {zip_path}")
            
            # Extract zip file
            extract_dir = os.path.join(temp_dir, "extracted")
            os.makedirs(extract_dir, exist_ok=True)
            
            try:
                with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                    zip_ref.extractall(extract_dir)
                    extracted_files = zip_ref.namelist()
                    logger.info(f"Extracted {len(extracted_files)} files from zip archive")
            except zipfile.BadZipFile as e:
                logger.error(f"Invalid zip file: {e}")
                raise ValueError(f"Invalid zip file: {str(e)}")
            except Exception as e:
                logger.error(f"Failed to extract zip file: {e}")
                raise ValueError(f"Failed to extract zip file: {str(e)}")
            
            # Process extracted directory
            logger.info(f"Processing extracted directory: {extract_dir}")
            result = await self.ingest_directory(
                directory=extract_dir,
                collection_name=collection_name,
                pattern=pattern,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                chunker_type=chunker_type,
                loader_type=loader_type,
                reset_collection=reset_collection,
                metadata=metadata
            )
            
            # Update metadata with zip info
            if result.metadata:
                result.metadata["zip_filename"] = filename
                result.metadata["extracted_files_count"] = len(extracted_files)
            else:
                result.metadata = {
                    "zip_filename": filename,
                    "extracted_files_count": len(extracted_files)
                }
            
            logger.info(f"Finished zip file ingestion for '{filename}'. Result: {result}")
            return result
