"""
API Routes Aggregation

Unified router for RAG and IDP endpoints.
"""

from fastapi import APIRouter

from app.api.endpoints import (
    naive_rag,
    ingestion,
    chatbot,
    auth,
    idp_extraction,
    idp_health,
    document_generation,
    document_generation_v2,
    document_analysis,
)
from app.api.endpoints import legal_package_ingestion, question_options, court_rules
from app.api.endpoints import court_form_questions

all_routes = APIRouter()

# RAG strategy router (naive only)
all_routes.include_router(naive_rag.router, prefix="/rag/naive", tags=["Naive RAG"])

# RAG document ingestion (vector store)
all_routes.include_router(ingestion.router, prefix="/ingestion", tags=["RAG Document Ingestion"])

# Court rules knowledge (OpenSearch)
all_routes.include_router(
    court_rules.router,
    prefix="/api/court-rules",
    tags=["Court Rules Knowledge"],
)

# RAG chatbot
all_routes.include_router(chatbot.router, prefix="/chatbot", tags=["Chatbot"])

# Platform login (US Legal Pro)
all_routes.include_router(auth.router, prefix="/auth", tags=["Authentication"])

# IDP document extraction
all_routes.include_router(idp_extraction.router, prefix="/api", tags=["IDP Document Extraction"])
all_routes.include_router(idp_health.router, prefix="/api/health", tags=["IDP Health"])

# Court document generation (fillable PDF / FTL)
all_routes.include_router(
    document_generation.router,
    prefix="/api",
    tags=["Document Generation"],
)

# Document generation 2.0 (extracted fields + answers → JSON)
all_routes.include_router(
    document_generation_v2.router,
    prefix="/api",
    tags=["Document Generation 2.0"],
)

# Court document analysis (Textract + LLM → user details / missing fields)
all_routes.include_router(
    document_analysis.router,
    prefix="/api",
    tags=["Document Analysis"],
)

# Court form field questions (Textract → Q&A strings)
all_routes.include_router(
    court_form_questions.router,
    prefix="/api",
    tags=["Court Form Questions"],
)

all_routes.include_router(
    legal_package_ingestion.router,
    prefix="/api/packages",
    tags=["Legal Package Ingestion"],
)

all_routes.include_router(
    question_options.router,
    prefix="/api/questions",
    tags=["Question Options"],
)


@all_routes.get("/health", tags=["Health"])
async def health_check():
    """Unified health check listing RAG strategies and vector stores."""
    from app.config.registry import RAGStrategyRegistry, VectorStoreRegistry
    from app.adapters.vector_store import _lazy_import_adapters
    from app.core.pipelines.rag import _lazy_import_strategies

    _lazy_import_adapters()
    _lazy_import_strategies()

    return {
        "status": "healthy",
        "service": "unified-rag-idp",
        "rag_strategies": RAGStrategyRegistry.list_strategies(),
        "vector_stores": VectorStoreRegistry.list_adapters(),
    }


@all_routes.get("/", tags=["Root"])
async def root():
    """API root with documentation links and endpoint index."""
    return {
        "message": "US Legal Pro - Unified RAG + IDP API",
        "docs": "/docs",
        "redoc": "/redoc",
        "endpoints": {
            "rag": {
                "chatbot_session": "/chatbot/session",
                "chatbot_rag_chat": "/chatbot/rag/chat",
                "chatbot_sessions": "/chatbot/sessions",
                "naive_rag": "/rag/naive/query",
                "ingestion_file": "/ingestion/file",
                "ingestion_text": "/ingestion/text",
                "ingestion_directory": "/ingestion/directory",
                "ingestion_zip": "/ingestion/zip",
            },
            "legal_filing_chatbot": {
                "login": "/auth/login",
                "websocket": "/chatbot/ws",
                "test_ui": "chatbot_test.html (project root — open in browser)",
            },
            "idp": {
                "extract": "/api/extract",
                "extract_batch": "/api/extract/batch",
                "health": "/api/health/",
                "health_live": "/api/health/live",
                "health_ready": "/api/health/ready",
            },
            "document_generation": {
                "generate": "/api/generate",
                "generate_v2": "/api/generate/v2",
            },
            "document_analysis": {
                "analyze": "/api/analyze",
            },
            "court_form_questions": {
                "questions": "/api/court-form/questions",
            },
            "court_rules": {
                "ingest": "/api/court-rules/ingest",
                "retrieve": "/api/court-rules/retrieve",
                "query": "/api/court-rules/query",
            },
        },
    }
