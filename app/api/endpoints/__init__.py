"""API endpoint routers mounted by the application."""

from app.api.endpoints import (
    auth,
    chatbot,
    court_form_questions,
    court_rules,
    document_analysis,
)

__all__ = [
    "auth",
    "chatbot",
    "court_form_questions",
    "court_rules",
    "document_analysis",
]
