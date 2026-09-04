"""
Document Ingestion Endpoint

API endpoint for ingesting documents into vector stores.
"""

import logging
from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, Form
from typing import Optional

from app.api.schemas.ingestion import (
    FileIngestionRequest,
    TextIngestionRequest,
    DirectoryIngestionRequest,
    IngestionResponse,
)
from app.api.schemas.rag import ErrorResponse
from app.services.ingestion_service import IngestionService
from app.api.dependencies import get_embedder
from app.config.settings import settings
from app.config.vector_store_factory import create_connected_vector_store

logger = logging.getLogger(__name__)

router = APIRouter()

# Cache for vector store adapters
_vector_store_cache = {}


async def _get_vector_store_adapter(vector_store_name: str, embedder) -> any:
    """
    Get or create a vector store adapter based on the vector store name.
    
    Args:
        vector_store_name: Name of the vector store (e.g., "opensearch", "pgvector").
        embedder: Embedder instance to get dimension from.
        
    Returns:
        Vector store adapter instance.
    """
    global _vector_store_cache

    vector_store_name = (vector_store_name or settings.DEFAULT_VECTOR_STORE or "opensearch").lower()
    
    if vector_store_name in _vector_store_cache:
        return _vector_store_cache[vector_store_name]
    
    try:
        adapter = await create_connected_vector_store(vector_store_name, embedder)
        _vector_store_cache[vector_store_name] = adapter
        logger.info(f"Created and cached vector store adapter: {vector_store_name}")
        return adapter
    except Exception as e:
        logger.error(f"Failed to create vector store adapter for {vector_store_name}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to connect to vector store '{vector_store_name}': {str(e)}"
        )


@router.post(
    "/file",
    response_model=IngestionResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Bad request"},
        500: {"model": ErrorResponse, "description": "Internal server error"}
    },
    summary="Ingest File",
    description="Upload and ingest a single file into the vector store."
)
async def ingest_file(
    file: UploadFile = File(...),
    vector_store: str = Form(default=None),
    collection_name: str = Form(...),
    chunk_size: int = Form(1000),
    chunk_overlap: int = Form(200),
    chunker_type: str = Form("fixed"),
    loader_type: str = Form("auto"),
    reset_collection: bool = Form(False),
    embedder = Depends(get_embedder)
):
    """
    Ingest a single file into the vector store.
    
    Supports:
    - Text files (.txt, .md)
    - PDF files (.pdf)
    - JSON files (.json, .jsonl)
    - Images for Textract OCR (.png, .jpg, .jpeg, .tiff) - extracts text
    - Images for multimodal embedding (.png, .jpg, .jpeg, .tiff, .webp, .bmp) - creates image embeddings
      (auto-selects based on multimodal model configuration)
    """
    try:
        logger.info(f"Received file ingestion request. "
                    f"Filename: {file.filename}, "
                    f"Vector store: {vector_store}, "
                    f"Collection: {collection_name}, "
                    f"Chunk size: {chunk_size}, "
                    f"Chunk overlap: {chunk_overlap}, "
                    f"Chunker type: {chunker_type}, "
                    f"Loader type: {loader_type}, "
                    f"Reset collection: {reset_collection}"
        )

        # Get vector store adapter based on form parameter
        logger.debug(f"Getting vector store adapter for: {vector_store}")
        vector_store_adapter = await _get_vector_store_adapter(vector_store, embedder)
        
        # Create service instance
        logger.debug("Instantiating IngestionService for file ingestion.")
        service = IngestionService(
            vector_store=vector_store_adapter,
            embedder=embedder
        )

        # Read file content
        logger.debug(f"Reading uploaded file: {file.filename}")
        file_content = await file.read()

        # Ingest using service
        logger.info(f"Starting file ingestion process for {file.filename}")
        result = await service.ingest_uploaded_file(
            file_content=file_content,
            filename=file.filename,
            collection_name=collection_name,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            chunker_type=chunker_type,
            loader_type=loader_type,
            reset_collection=reset_collection
        )

        logger.info(f"File ingestion successful: {file.filename}. "
                    f"Documents: {result.total_documents}, "
                    f"Chunks: {result.vectors_stored}, "
                    f"Failed: {result.failed_chunks}")

        return IngestionResponse(
            success=True,
            total_documents=result.total_documents,
            total_chunks=result.total_chunks,
            vectors_stored=result.vectors_stored,
            failed_chunks=result.failed_chunks,
            collection_name=result.collection_name,
            message=f"Successfully ingested {result.vectors_stored} chunks from {result.total_documents} document(s)",
            metadata=result.metadata
        )

    except ValueError as e:
        logger.error(f"Invalid file ingestion request: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"File ingestion failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/text",
    response_model=IngestionResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Bad request"},
        500: {"model": ErrorResponse, "description": "Internal server error"}
    },
    summary="Ingest Text",
    description="Ingest raw text directly into the vector store."
)
async def ingest_text(
    text: str = Form(...),
    vector_store: str = Form(default=None),
    collection_name: str = Form(...),
    source: str = Form("direct_input"),
    chunk_size: int = Form(1000),
    chunk_overlap: int = Form(200),
    chunker_type: str = Form("fixed"),
    reset_collection: bool = Form(False),
    embedder = Depends(get_embedder)
):
    """
    Ingest raw text directly into the vector store.
    
    Useful for ingesting text from APIs, databases, or user input.
    """
    try:
        logger.info(f"Received text ingestion request. "
                    f"Vector store: {vector_store}, "
                    f"Collection: {collection_name}, "
                    f"Source: {source}, "
                    f"Chunk size: {chunk_size}, "
                    f"Chunk overlap: {chunk_overlap}, "
                    f"Chunker type: {chunker_type}, "
                    f"Reset collection: {reset_collection}"
        )

        # Get vector store adapter based on form parameter
        logger.debug(f"Getting vector store adapter for: {vector_store}")
        vector_store_adapter = await _get_vector_store_adapter(vector_store, embedder)
        
        # Create service instance
        logger.debug("Instantiating IngestionService for text ingestion.")
        service = IngestionService(
            vector_store=vector_store_adapter,
            embedder=embedder
        )

        logger.info(f"Starting text ingestion process for collection '{collection_name}'.")
        result = await service.ingest_text(
            text=text,
            collection_name=collection_name,
            source=source,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            chunker_type=chunker_type,
            reset_collection=reset_collection
        )

        logger.info(f"Text ingestion successful. "
                    f"Documents: {result.total_documents}, "
                    f"Chunks: {result.vectors_stored}, "
                    f"Failed: {result.failed_chunks}")

        return IngestionResponse(
            success=True,
            total_documents=result.total_documents,
            total_chunks=result.total_chunks,
            vectors_stored=result.vectors_stored,
            failed_chunks=result.failed_chunks,
            collection_name=result.collection_name,
            message=f"Successfully ingested {result.vectors_stored} chunks from text",
            metadata=result.metadata
        )

    except ValueError as e:
        logger.error(f"Invalid text ingestion request: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Text ingestion failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/directory",
    response_model=IngestionResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Bad request"},
        500: {"model": ErrorResponse, "description": "Internal server error"}
    },
    summary="Ingest Directory",
    description="Ingest all matching files from a directory into the vector store."
)
async def ingest_directory(
    directory: str = Form(...),
    vector_store: str = Form(default=None),
    collection_name: str = Form(...),
    pattern: str = Form("*"),
    chunk_size: int = Form(1000),
    chunk_overlap: int = Form(200),
    chunker_type: str = Form("fixed"),
    loader_type: str = Form("auto"),
    reset_collection: bool = Form(False),
    embedder = Depends(get_embedder)
):
    """
    Ingest all matching files from a directory.
    
    Processes all files matching the pattern in the specified directory.
    """
    try:
        logger.info(f"Received directory ingestion request. "
                    f"Directory: {directory}, "
                    f"Vector store: {vector_store}, "
                    f"Collection: {collection_name}, "
                    f"Pattern: {pattern}, "
                    f"Chunk size: {chunk_size}, "
                    f"Chunk overlap: {chunk_overlap}, "
                    f"Chunker type: {chunker_type}, "
                    f"Loader type: {loader_type}, "
                    f"Reset collection: {reset_collection}"
        )

        # Get vector store adapter based on form parameter
        logger.debug(f"Getting vector store adapter for: {vector_store}")
        vector_store_adapter = await _get_vector_store_adapter(vector_store, embedder)
        
        # Create service instance
        logger.debug("Instantiating IngestionService for directory ingestion.")
        service = IngestionService(
            vector_store=vector_store_adapter,
            embedder=embedder
        )

        logger.info(f"Starting directory ingestion from '{directory}' for collection '{collection_name}' with pattern '{pattern}'.")
        result = await service.ingest_directory(
            directory=directory,
            collection_name=collection_name,
            pattern=pattern,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            chunker_type=chunker_type,
            loader_type=loader_type,
            reset_collection=reset_collection
        )

        logger.info(f"Directory ingestion successful for directory '{directory}'. "
                    f"Documents: {result.total_documents}, "
                    f"Chunks: {result.vectors_stored}, "
                    f"Failed: {result.failed_chunks}")

        return IngestionResponse(
            success=True,
            total_documents=result.total_documents,
            total_chunks=result.total_chunks,
            vectors_stored=result.vectors_stored,
            failed_chunks=result.failed_chunks,
            collection_name=result.collection_name,
            message=f"Successfully ingested {result.vectors_stored} chunks from {result.total_documents} documents in directory",
            metadata=result.metadata
        )

    except ValueError as e:
        logger.error(f"Invalid directory ingestion request: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Directory ingestion failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/zip",
    response_model=IngestionResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Bad request"},
        500: {"model": ErrorResponse, "description": "Internal server error"}
    },
    summary="Ingest Zip File",
    description="Upload and ingest a zip file containing multiple documents into the vector store."
)
async def ingest_zip(
    file: UploadFile = File(...),
    vector_store: str = Form(default=None),
    collection_name: str = Form(...),
    pattern: str = Form("*"),
    chunk_size: int = Form(1000),
    chunk_overlap: int = Form(200),
    chunker_type: str = Form("fixed"),
    loader_type: str = Form("auto"),
    reset_collection: bool = Form(False),
    embedder = Depends(get_embedder)
):
    """
    Ingest files from a zip archive.
    
    Extracts the zip file and processes all matching files within it.
    Supports nested directories within the zip.
    """
    try:
        logger.info(f"Received zip file ingestion request. "
                    f"Filename: {file.filename}, "
                    f"Vector store: {vector_store}, "
                    f"Collection: {collection_name}, "
                    f"Pattern: {pattern}, "
                    f"Chunk size: {chunk_size}, "
                    f"Chunk overlap: {chunk_overlap}, "
                    f"Chunker type: {chunker_type}, "
                    f"Loader type: {loader_type}, "
                    f"Reset collection: {reset_collection}"
        )

        # Validate file is a zip
        if not file.filename or not file.filename.lower().endswith(('.zip', '.zipx')):
            raise ValueError("File must be a zip archive (.zip or .zipx)")

        # Get vector store adapter based on form parameter
        logger.debug(f"Getting vector store adapter for: {vector_store}")
        vector_store_adapter = await _get_vector_store_adapter(vector_store, embedder)
        
        # Create service instance
        logger.debug("Instantiating IngestionService for zip file ingestion.")
        service = IngestionService(
            vector_store=vector_store_adapter,
            embedder=embedder
        )

        # Read zip file content
        logger.debug(f"Reading uploaded zip file: {file.filename}")
        zip_content = await file.read()

        # Ingest using service
        logger.info(f"Starting zip file ingestion process for {file.filename}")
        result = await service.ingest_zip_file(
            zip_content=zip_content,
            filename=file.filename,
            collection_name=collection_name,
            pattern=pattern,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            chunker_type=chunker_type,
            loader_type=loader_type,
            reset_collection=reset_collection
        )

        logger.info(f"Zip file ingestion successful: {file.filename}. "
                    f"Documents: {result.total_documents}, "
                    f"Chunks: {result.vectors_stored}, "
                    f"Failed: {result.failed_chunks}")

        return IngestionResponse(
            success=True,
            total_documents=result.total_documents,
            total_chunks=result.total_chunks,
            vectors_stored=result.vectors_stored,
            failed_chunks=result.failed_chunks,
            collection_name=result.collection_name,
            message=f"Successfully ingested {result.vectors_stored} chunks from {result.total_documents} document(s) in zip archive",
            metadata=result.metadata
        )

    except ValueError as e:
        logger.error(f"Invalid zip file ingestion request: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Zip file ingestion failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
