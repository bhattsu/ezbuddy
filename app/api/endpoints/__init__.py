"""
API Endpoints Module

This module provides endpoint routers for Naive RAG and ingestion.
"""

from app.api.endpoints import (
    naive_rag,
    ingestion,
    chatbot,
    idp_extraction,
    idp_health,
    document_generation,
    document_generation_v2,
    document_analysis,
    court_form_questions,
)

__all__ = [
    "naive_rag",
    "ingestion",
    "chatbot",
    "idp_extraction",
    "idp_health",
    "document_generation",
    "document_generation_v2",
    "document_analysis",
    "court_form_questions",
]
