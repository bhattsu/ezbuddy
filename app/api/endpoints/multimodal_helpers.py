"""
Multimodal Query Helpers

Shared helper functions for multimodal query endpoints.
"""

import logging
import base64
import json
from typing import Optional, Dict, Any
from fastapi import UploadFile, Form, File, HTTPException

from app.services.retrieval_service import RAGService
from app.api.schemas.rag import RAGResponse, ErrorResponse

logger = logging.getLogger(__name__)


async def process_multimodal_request(
    query: str,
    image: Optional[UploadFile],
    filters: Optional[str],
    service: RAGService,
    execute_func,
    **execute_kwargs
) -> RAGResponse:
    """
    Process a multimodal request with image upload.
    
    Args:
        query: Text query string
        image: Optional uploaded image file
        filters: Optional JSON string of filters
        service: RAGService instance
        execute_func: Function to call on service (e.g., service.execute_naive_rag)
        **execute_kwargs: Additional arguments to pass to execute_func
        
    Returns:
        RAGResponse
    """
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
    
    # Add multimodal parameters to execute_kwargs
    execute_kwargs.update({
        "query": query or "",
        "query_image_base64": query_image_base64,
        "query_image_bytes": query_image_bytes,
        "filters": filter_dict,
    })
    
    # Execute the RAG function
    result = await execute_func(**execute_kwargs)
    
    logger.info(
        f"Multimodal query succeeded: answer='{result.answer[:100]}...', "
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

