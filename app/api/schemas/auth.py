"""Authentication API schemas (US Legal Pro platform login)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(..., description="Account email / username")
    password: str = Field(..., min_length=1)
    state: str = Field(default="ca", description="US state code for API path (e.g. ca, tx)")


class LoginResponse(BaseModel):
    user_id: str
    email: str
    session_id: str
    auth_token: str
