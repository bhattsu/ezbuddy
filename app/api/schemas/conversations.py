"""Conversation history API schemas."""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class UserMessagesRequest(BaseModel):
    email: str = Field(..., description="User email from operational.users")
    session_id: str = Field(..., min_length=1, description="Platform session ID from operational.users")


class UserMessageItem(BaseModel):
    message_id: UUID
    conversation_id: UUID
    message: str
    created_at: datetime
    conversation_session_id: Optional[UUID] = None
    conversation_status: Optional[str] = None


class UserMessagesResponse(BaseModel):
    user_id: UUID
    email: str
    session_id: str
    messages: List[UserMessageItem] = Field(default_factory=list)
