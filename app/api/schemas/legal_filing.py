"""
Schemas for document analysis API (court document upload/analysis).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class FilingChatRequest(BaseModel):
    """Legacy JSON chat request (deprecated — use WebSocket events)."""

    session_id: Optional[str] = Field(default=None)
    message: str = Field(default="")
    case_number: Optional[str] = Field(default=None)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class DocumentAnalysisResponse(BaseModel):
    """Response from POST /api/analyze."""

    document_id: str
    file_name: str
    extracted_fields: Dict[str, Any] = Field(default_factory=dict)
    user_details: Dict[str, Any] = Field(default_factory=dict)
    missing_fields: List[str] = Field(default_factory=list)
    document_classification: Optional[str] = Field(default=None)
    case_type: Optional[str] = None
    sub_case_type: Optional[str] = None
    raw_textract: Optional[Dict[str, Any]] = Field(default=None)
    message: str = (
        "Document analyzed. Review extracted details and provide any missing fields."
    )
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    error: str
    detail: Optional[str] = None
    status_code: int = 400
