"""
Naive RAG Endpoint

Simple retrieve and generate RAG implementation.
Supports both text and multimodal (text + image) queries.
"""

import logging
import base64
from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, Form
from typing import Optional

from app.api.schemas.rag import RAGQueryRequest, RAGResponse, ErrorResponse
from app.services.retrieval_service import RAGService
from app.api.dependencies import get_rag_components

logger = logging.getLogger(__name__)

router = APIRouter()


def _get_rag_service(components: dict = Depends(get_rag_components)) -> RAGService:
    """Dependency to get RAG service instance."""
    logger.debug("Initializing RAGService with components: %s", list(components.keys()))
    return RAGService(
        llm_client=components["llm"],
        embedder=components["embedder"]
    )


@router.post(
    "/query",
    response_model=RAGResponse,
    responses={
        500: {"model": ErrorResponse, "description": "Internal server error"}
    },
    summary="Naive RAG Query",
    description="Simple single-pass retrieval and generation. Supports text and multimodal (text + image) queries."
)
async def naive_rag_query(
    request: RAGQueryRequest,
    service: RAGService = Depends(_get_rag_service)
):
    """
    Execute a Naive RAG query.
    
    This is the simplest RAG approach:
    1. Embed the query (text, image, or both)
    2. Retrieve top-k similar documents
    3. Generate response using retrieved context
    
    Supports:
    - Text-only queries
    - Image-only queries (via query_image_base64)
    - Combined text + image queries
    """
    logger.info(
        f"Received Naive RAG query: query='{request.query}', vector_store='{request.vector_store}', "
        f"collection_name='{request.collection_name}', top_k={request.top_k}, filters={request.filters}, "
        f"score_threshold={request.score_threshold}, system_prompt={request.system_prompt}, "
        f"has_image={request.query_image_base64 is not None}"
    )
    try:
        logger.debug("Calling service.execute_naive_rag")
        result = await service.execute_naive_rag(
            query=request.query or "",
            vector_store_name=request.vector_store,
            collection_name=request.collection_name,
            top_k=request.top_k,
            filters=request.filters,
            score_threshold=request.score_threshold,
            system_prompt=request.system_prompt,
            query_image_base64=request.query_image_base64
        )
        logger.info(
            f"Naive RAG query succeeded: answer='{result.answer[:100]}...', "
            f"sources_count={len(result.sources) if result.sources else 0}, "
            f"rag_type={result.rag_type}, vector_store={result.vector_store}, "
            f"confidence={result.confidence}, metadata={result.metadata}"
        )
        return RAGResponse(
            answer=result.answer,
            sources=[s.model_dump() for s in result.sources],
            rag_type=result.rag_type,
            vector_store=result.vector_store,
            confidence=result.confidence,
            metadata=result.metadata,
        )
        
    except Exception as e:
        logger.error(f"Naive RAG query failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/query-multimodal",
    response_model=RAGResponse,
    responses={
        500: {"model": ErrorResponse, "description": "Internal server error"}
    },
    summary="Naive RAG Multimodal Query",
    description="Multimodal query with image upload. Supports text + image queries."
)
async def naive_rag_query_multimodal(
    query: str = Form("", description="Optional text query"),
    image: Optional[UploadFile] = File(None, description="Optional image file for multimodal query"),
    vector_store: str = Form("pgvector"),
    collection_name: str = Form(...),
    top_k: int = Form(10),
    filters: Optional[str] = Form(None, description="JSON string of filters"),
    score_threshold: float = Form(0.0),
    system_prompt: Optional[str] = Form(None),
    service: RAGService = Depends(_get_rag_service)
):
    """
    Execute a Naive RAG query with multimodal support (text + image).
    
    Upload an image file along with optional text query for multimodal search.
    """
    import json
    
    logger.info(
        f"Received multimodal Naive RAG query: query='{query}', "
        f"has_image={image is not None}, vector_store='{vector_store}', "
        f"collection_name='{collection_name}'"
    )
    
    try:
        # Parse filters if provided
        filter_dict = None
        if filters:
            try:
                filter_dict = json.loads(filters)
            except json.JSONDecodeError:
                logger.warning(f"Invalid filters JSON: {filters}")
        
        # Read image if provided
        query_image_base64 = None
        query_image_bytes = None
        if image:
            image_bytes = await image.read()
            query_image_bytes = image_bytes
            query_image_base64 = base64.b64encode(image_bytes).decode('utf-8')
            logger.debug(f"Read image file: {image.filename}, size: {len(image_bytes)} bytes")
        
        if not query and not query_image_base64:
            raise ValueError("Must provide either query text or image file")
        
        result = await service.execute_naive_rag(
            query=query or "",
            vector_store_name=vector_store,
            collection_name=collection_name,
            top_k=top_k,
            filters=filter_dict,
            score_threshold=score_threshold,
            system_prompt=system_prompt,
            query_image_base64=query_image_base64,
            query_image_bytes=query_image_bytes
        )
        
        logger.info(
            f"Multimodal Naive RAG query succeeded: answer='{result.answer[:100]}...', "
            f"sources_count={len(result.sources) if result.sources else 0}"
        )
        
        return RAGResponse(
            answer=result.answer,
            sources=[s.model_dump() for s in result.sources],
            rag_type=result.rag_type,
            vector_store=result.vector_store,
            confidence=result.confidence,
            metadata=result.metadata,
        )
        
    except Exception as e:
        logger.error(f"Multimodal Naive RAG query failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

