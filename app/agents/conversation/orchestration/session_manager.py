"""In-memory filing session registry (per conversation)."""

from __future__ import annotations

from typing import Dict, Optional

from app.agents.conversation.orchestration.state import FilingSession


class FilingSessionManager:
    _sessions: Dict[str, FilingSession] = {}

    @classmethod
    def get(cls, conversation_id: str) -> Optional[FilingSession]:
        return cls._sessions.get(conversation_id)

    @classmethod
    def create(cls, conversation_id: str, user_id: str) -> FilingSession:
        session = FilingSession(conversation_id=conversation_id, user_id=user_id)
        cls._sessions[conversation_id] = session
        return session

    @classmethod
    def get_or_create(cls, conversation_id: str, user_id: str) -> FilingSession:
        existing = cls.get(conversation_id)
        if existing:
            return existing
        return cls.create(conversation_id, user_id)
