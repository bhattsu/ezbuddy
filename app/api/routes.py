"""
API Routes Aggregation

Filing chat WebSocket plus the HTTP APIs still used by the product.
"""

from fastapi import APIRouter

from app.api.endpoints import (
    auth,
    chatbot,
    conversations,
    court_form_questions,
    court_rules,
    document_analysis,
    idp_health,
    template_ingest,
)

all_routes = APIRouter()

# Container / load balancer probes
all_routes.include_router(
    idp_health.router,
    prefix="/api/health",
    tags=["Health"],
)

# Filing chat WebSocket lives on this router (/chatbot/ws)
all_routes.include_router(chatbot.router, prefix="/chatbot", tags=["Chatbot"])

# Platform login (US Legal Pro)
all_routes.include_router(auth.router, prefix="/auth", tags=["Authentication"])

# Conversation history
all_routes.include_router(
    conversations.router,
    prefix="/api/conversations",
    tags=["Conversations"],
)

# Court document analysis (uploaded PDF → filled / blank fields)
all_routes.include_router(
    document_analysis.router,
    prefix="/api",
    tags=["Document Analysis"],
)

# Court form field questions (template PDF → questions)
all_routes.include_router(
    court_form_questions.router,
    prefix="/api",
    tags=["Court Form Questions"],
)

# Court rules knowledge (OpenSearch)
all_routes.include_router(
    court_rules.router,
    prefix="/api/court-rules",
    tags=["Court Rules Knowledge"],
)

# Court PDF template ingest (S3 + configuration.document_templates)
all_routes.include_router(
    template_ingest.router,
    prefix="/api/templates",
    tags=["Template Ingest"],
)


@all_routes.get("/api/health", tags=["Health"], include_in_schema=False)
async def health_check_no_slash():
    """Alias so probes hitting /api/health get 200 instead of a 307 redirect."""
    return await idp_health.health_check()


@all_routes.get("/", tags=["Root"])
async def root():
    """API root with documentation links and endpoint index."""
    return {
        "message": "US Legal Pro filing chat",
        "docs": "/docs",
        "redoc": "/redoc",
        "endpoints": {
            "health": {
                "health": "/api/health/",
                "liveness": "/api/health/live",
                "readiness": "/api/health/ready",
            },
            "legal_filing_chatbot": {
                "login": "/auth/login",
                "websocket": "/chatbot/ws",
                "test_ui": "chatbot_test.html (project root — open in browser)",
            },
            "conversations": {
                "user_messages": "/api/conversations/user-messages",
            },
            "document_analysis": {
                "analyze": "/api/analyze",
            },
            "court_form_questions": {
                "questions": "/api/court-form/questions",
            },
            "template_ingest": {
                "states": "/api/templates/states",
                "jurisdictions": "/api/templates/jurisdictions?state=TX",
                "ingest": "/api/templates/ingest",
            },
        },
    }
