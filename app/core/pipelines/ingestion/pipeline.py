"""
Data Ingestion Pipeline

Main orchestrator for loading, chunking, embedding, and storing documents.
"""

import logging
import uuid
from typing import List, Dict, Any, Optional, Type
from dataclasses import dataclass, field

from app.core.pipelines.ingestion.loaders.base import DocumentLoader, Document
from app.core.pipelines.ingestion.chunkers.base import ChunkingStrategy, Chunk
from app.adapters.vector_store.base import VectorStoreAdapter

logger = logging.getLogger(__name__)


@dataclass
class IngestionConfig:
    """Configuration for the ingestion pipeline."""
    collection_name: str
    chunk_size: int = 1000
    chunk_overlap: int = 200
    batch_size: int = 100
    reset_collection: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class IngestionResult:
    """Result of an ingestion operation."""
    total_documents: int
    total_chunks: int
    vectors_stored: int
    failed_chunks: int
    collection_name: str
    metadata: Dict[str, Any] = field(default_factory=dict)


class IngestionPipeline:
    """
    Main pipeline for ingesting documents into vector stores.
    
    Orchestrates:
    1. Document loading
    2. Text chunking
    3. Embedding generation
    4. Vector storage
    """

    def __init__(
        self,
        vector_store: VectorStoreAdapter,
        embedder: Any,
        chunker: ChunkingStrategy,
        loader: Optional[DocumentLoader] = None
    ):
        """
        Initialize the ingestion pipeline.
        
        Args:
            vector_store: Vector store adapter for storage.
            embedder: Embedding model/client.
            chunker: Chunking strategy to use.
            loader: Optional default document loader.
        """
        self.vector_store = vector_store
        self.embedder = embedder
        self.chunker = chunker
        self.loader = loader

    async def ingest_documents(
        self,
        documents: List[Document],
        config: IngestionConfig
    ) -> IngestionResult:
        """
        Ingest a list of documents into the vector store.
        
        Args:
            documents: List of Document objects to ingest.
            config: Ingestion configuration.
            
        Returns:
            IngestionResult with statistics.
        """
        logger.info(f"Starting ingestion of {len(documents)} documents to {config.collection_name}")
        
        # Get embedding dimension from embedder
        if hasattr(self.embedder, 'embedding_dimension'):
            vector_dim = self.embedder.embedding_dimension
        else:
            # Fallback: try to get dimension from first embedding
            logger.warning("Embedder doesn't have embedding_dimension property. Generating test embedding to determine dimension...")
            try:
                test_embedding = await self.embedder.embed("test")
                vector_dim = len(test_embedding)
                logger.info(f"Determined embedding dimension from test embedding: {vector_dim}")
            except Exception as e:
                logger.error(f"Failed to determine embedding dimension: {e}. Defaulting to 1024.")
                vector_dim = 1024
        
        # Chunk all documents first to check if we have any documents
        all_chunks = []
        for doc in documents:
            logger.debug(f"Chunking document with doc_id={doc.doc_id} and source={getattr(doc, 'source', None)}")
            
            # Check if this is an image/multimodal document
            is_multimodal = doc.metadata.get("multimodal", False) or doc.metadata.get("content_type") == "image"
            
            if is_multimodal:
                # Images should not be chunked - treat as single unit
                from app.core.pipelines.ingestion.chunkers.base import Chunk
                import uuid
                chunk = Chunk(
                    content=doc.content,  # Base64 image
                    chunk_id=str(uuid.uuid4()),  # Generate UUID for chunk ID
                    doc_id=doc.doc_id,
                    chunk_index=0,
                    metadata={**doc.metadata, **config.metadata, "multimodal": True, "content_type": "image"}
                )
                all_chunks.append(chunk)
                logger.debug(f"Image document {doc.doc_id} treated as single chunk (no chunking)")
            else:
                # Regular text documents - chunk normally
                chunks = self.chunker.chunk(
                    text=doc.content,
                    doc_id=doc.doc_id,
                    metadata={**doc.metadata, **config.metadata}
                )
                all_chunks.extend(chunks)
        logger.info(f"Created {len(all_chunks)} chunks from {len(documents)} documents")
        
        # If no documents or chunks, return early without creating collection
        if len(documents) == 0 or len(all_chunks) == 0:
            logger.warning(
                f"No documents or chunks to ingest. Documents: {len(documents)}, Chunks: {len(all_chunks)}. "
                f"Skipping collection creation and ingestion."
            )
            return IngestionResult(
                total_documents=len(documents),
                total_chunks=len(all_chunks),
                vectors_stored=0,
                failed_chunks=0,
                collection_name=config.collection_name,
                metadata={
                    "chunk_size": config.chunk_size,
                    "chunk_overlap": config.chunk_overlap,
                    "warning": "No documents or chunks to ingest"
                }
            )
        
        # Ensure collection exists only if we have documents to ingest
        logger.debug(f"Ensuring collection '{config.collection_name}' exists with vector_dim={vector_dim}")
        await self.vector_store.ensure_collection(
            collection_name=config.collection_name,
            vector_dim=vector_dim,
        )
        
        # Optionally reset collection
        if config.reset_collection:
            logger.info(f"Resetting collection '{config.collection_name}' as requested in config")
            await self.vector_store.delete_collection(config.collection_name)
            await self.vector_store.create_collection(
                collection_name=config.collection_name,
                vector_dim=vector_dim,
            )
        
        # Embed and store in batches
        total_stored = 0
        failed_count = 0
        
        for i in range(0, len(all_chunks), config.batch_size):
            batch = all_chunks[i:i + config.batch_size]
            logger.debug(f"Processing batch {i // config.batch_size + 1}: size={len(batch)}")
            
            try:
                # Generate embeddings
                ids = []
                embeddings = []
                metadata_list = []
                
                for chunk in batch:
                    try:
                        logger.debug(f"Embedding chunk {chunk.chunk_id} (index={chunk.chunk_index}) of document {chunk.doc_id}")
                        
                        # Check if this is a multimodal (image) chunk
                        is_multimodal = chunk.metadata.get("multimodal", False) or chunk.metadata.get("content_type") == "image"
                        
                        if is_multimodal and hasattr(self.embedder, 'embed_multimodal'):
                            # Use multimodal embedding for images
                            image_base64 = chunk.content
                            image_bytes = chunk.metadata.get("image_bytes")
                            embedding = await self.embedder.embed_multimodal(
                                image_base64=image_base64,
                                image_bytes=image_bytes
                            )
                        else:
                            # Use text embedding
                            embedding = await self.embedder.embed(chunk.content)
                        
                        ids.append(chunk.chunk_id)
                        embeddings.append(embedding)
                        
                        # Prepare metadata for storage (remove binary data that can't be JSON-serialized)
                        storage_metadata = {
                            "content": chunk.content,
                            "doc_id": chunk.doc_id,
                            "chunk_index": chunk.chunk_index,
                            "multimodal": is_multimodal,
                        }
                        
                        # Copy chunk metadata but exclude binary fields
                        # image_bytes is only needed during embedding, not for storage
                        for key, value in chunk.metadata.items():
                            if key == "image_bytes":
                                # Skip binary data - it can't be JSON-serialized
                                logger.debug(f"Skipping binary metadata key '{key}' from storage")
                                continue
                            # Include other metadata fields
                            storage_metadata[key] = value
                        
                        metadata_list.append(storage_metadata)
                    except Exception as e:
                        logger.warning(f"Failed to embed chunk {chunk.chunk_id}: {e}", exc_info=True)
                        failed_count += 1
                
                # Store in vector store
                if embeddings:
                    logger.debug(
                        f"Upserting {len(embeddings)} chunks to vector store collection '{config.collection_name}'"
                    )
                    stored = await self.vector_store.upsert(
                        collection_name=config.collection_name,
                        ids=ids,
                        embeddings=embeddings,
                        metadata=metadata_list,
                    )
                    logger.info(f"Stored {stored} vectors for batch {i // config.batch_size + 1}")
                    total_stored += stored
                else:
                    logger.warning(f"No embeddings created for batch {i // config.batch_size + 1}")
                
                logger.debug(f"Processed batch {i // config.batch_size + 1}")
                
            except Exception as e:
                logger.error(f"Failed to process batch: {e}", exc_info=True)
                failed_count += len(batch)
        
        result = IngestionResult(
            total_documents=len(documents),
            total_chunks=len(all_chunks),
            vectors_stored=total_stored,
            failed_chunks=failed_count,
            collection_name=config.collection_name,
            metadata={
                "chunk_size": config.chunk_size,
                "chunk_overlap": config.chunk_overlap,
            }
        )
        
        logger.info(
            f"Ingestion complete: {result.vectors_stored}/{result.total_chunks} "
            f"chunks stored ({result.failed_chunks} failed) in '{config.collection_name}'"
        )
        
        return result

    async def ingest_file(
        self,
        file_path: str,
        config: IngestionConfig,
        loader: Optional[DocumentLoader] = None
    ) -> IngestionResult:
        """
        Ingest a single file.
        
        Args:
            file_path: Path to the file.
            config: Ingestion configuration.
            loader: Optional specific loader to use.
            
        Returns:
            IngestionResult with statistics.
        """
        loader = loader or self.loader
        if not loader:
            logger.error("No loader provided or set for ingestion pipeline ingest_file")
            raise ValueError("No loader provided or set")
        
        logger.info(f"Loading file '{file_path}' using loader: {type(loader).__name__}")
        documents = await loader.load(file_path)
        logger.info(f"Loaded {len(documents)} document(s) from file '{file_path}'")
        result = await self.ingest_documents(documents, config)
        logger.info(f"Ingestion from file '{file_path}' finished with {result.vectors_stored} vectors stored")
        return result

    async def ingest_directory(
        self,
        directory: str,
        config: IngestionConfig,
        loader: Optional[DocumentLoader] = None,
        pattern: str = "*"
    ) -> IngestionResult:
        """
        Ingest all files from a directory.
        
        Args:
            directory: Path to the directory.
            config: Ingestion configuration.
            loader: Optional specific loader to use.
            pattern: Glob pattern for file matching.
            
        Returns:
            IngestionResult with statistics.
        """
        loader = loader or self.loader
        if not loader:
            logger.error("No loader provided or set for ingestion pipeline ingest_directory")
            raise ValueError("No loader provided or set")
        
        logger.info(f"Loading directory '{directory}' with pattern '{pattern}' using loader: {type(loader).__name__}")
        documents = await loader.load_directory(directory, pattern)
        logger.info(f"Loaded {len(documents)} document(s) from directory '{directory}'")
        result = await self.ingest_documents(documents, config)
        logger.info(f"Ingestion from directory '{directory}' finished with {result.vectors_stored} vectors stored")
        return result

    async def ingest_text(
        self,
        text: str,
        config: IngestionConfig,
        source: str = "direct_input",
        metadata: Dict[str, Any] = None
    ) -> IngestionResult:
        """
        Ingest raw text directly.
        
        Args:
            text: The text to ingest.
            config: Ingestion configuration.
            source: Source identifier for the text.
            metadata: Optional metadata.
            
        Returns:
            IngestionResult with statistics.
        """
        logger.info(f"Ingesting raw text from source='{source}'")
        doc = Document(
            content=text,
            metadata=metadata or {},
            source=source,
            doc_id=str(uuid.uuid4()),
        )
        result = await self.ingest_documents([doc], config)
        logger.info(f"Text ingestion complete for source='{source}' [{result.vectors_stored} stored]")
        return result

    async def ingest_texts(
        self,
        texts: List[str],
        config: IngestionConfig,
        metadata_list: Optional[List[Dict[str, Any]]] = None
    ) -> IngestionResult:
        """
        Ingest multiple raw texts.
        
        Args:
            texts: List of texts to ingest.
            config: Ingestion configuration.
            metadata_list: Optional list of metadata dicts for each text.
            
        Returns:
            IngestionResult with statistics.
        """
        logger.info(f"Preparing to ingest {len(texts)} raw texts")
        documents = []
        for i, text in enumerate(texts):
            metadata = metadata_list[i] if metadata_list and i < len(metadata_list) else {}
            logger.debug(f"Constructing Document for raw text index {i}")
            doc = Document(
                content=text,
                metadata=metadata,
                source=f"text_{i}",
                doc_id=str(uuid.uuid4()),
            )
            documents.append(doc)
        logger.info(f"Constructed {len(documents)} Document objects for ingestion")
        result = await self.ingest_documents(documents, config)
        logger.info(f"Multiple text ingestion complete ({result.vectors_stored}/{result.total_chunks} stored)")
        return result

