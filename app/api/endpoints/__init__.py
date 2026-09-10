"""API endpoint routers mounted by the application."""

from app.api.endpoints import (
    auth,
    chatbot,
    conversations,
    court_form_questions,
    court_rules,
    document_analysis,
    template_ingest,
)

__all__ = [
    "auth",
    "chatbot",
    "conversations",
    "court_form_questions",
    "court_rules",
    "document_analysis",
    "template_ingest",
]
