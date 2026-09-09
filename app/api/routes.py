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
)

all_routes = APIRouter()

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


@all_routes.get("/", tags=["Root"])
async def root():
    """API root with documentation links and endpoint index."""
    return {
        "message": "US Legal Pro filing chat",
        "docs": "/docs",
        "redoc": "/redoc",
        "endpoints": {
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
        },
    }
