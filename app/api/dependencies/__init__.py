"""
Shared Dependencies for RAG Endpoints

This module provides dependency injection for RAG components.
"""

import logging
from typing import Dict, Any
from fastapi import Depends, HTTPException, Query

from app.adapters.llm.bedrock import get_bedrock
from app.config.pgvector_store import build_pgvector_store_config_for_embedder
from app.config.registry import VectorStoreRegistry

logger = logging.getLogger(__name__)

# Global instances (initialized on first use)
_vector_store_instances: Dict[str, Any] = {}


def get_llm_client():
    """Get or create the shared Bedrock instance for LLM calls."""
    return get_bedrock()


def get_embedder():
    """Get or create the shared Bedrock instance for embeddings."""
    return get_bedrock()


async def get_vector_store(
    vector_store_type: str = Query(default="pgvector", description="Vector store type")
):
    """
    Get or create a vector store adapter instance.
    
    This dependency manages connection lifecycle for vector stores.
    """
    global _vector_store_instances
    
    if vector_store_type in _vector_store_instances:
        return _vector_store_instances[vector_store_type]
    
    try:
        if vector_store_type != "pgvector":
            raise ValueError(f"Unknown vector store: {vector_store_type}. Available: ['pgvector']")
        embedder = get_embedder()
        config = build_pgvector_store_config_for_embedder(embedder)
        
        # Create adapter
        adapter = VectorStoreRegistry.create_adapter(vector_store_type, config)
        
        # Connect
        await adapter.connect()
        
        # Cache instance
        _vector_store_instances[vector_store_type] = adapter
        
        return adapter
        
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to initialize vector store {vector_store_type}: {e}")
        raise HTTPException(
            status_code=500, 
            detail=f"Failed to connect to vector store: {str(e)}"
        )


async def get_rag_components() -> Dict[str, Any]:
    """
    Get all RAG components needed for query execution.
    
    Note: The vector store adapter is determined by the 'vector_store' field in the request body.
    This dependency provides LLM and embedder instances. The vector store adapter is created
    dynamically based on the request body's vector_store field in the service layer.
    
    Returns a dict with:
    - llm: The LLM client
    - embedder: The embedding client
    """
    llm = get_llm_client()
    embedder = get_embedder()
    
    return {
        "llm": llm,
        "embedder": embedder,
    }

