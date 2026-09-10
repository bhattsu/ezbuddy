"""Schemas for court PDF template ingest."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, List, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class TemplateFolderListResponse(BaseModel):
    prefix: str
    options: List[str] = Field(default_factory=list)


class TemplateIngestResponse(BaseModel):
    template_id: UUID
    version_id: UUID
    version: int
    s3_bucket: str
    s3_key: str
    s3_uri: str
    document_template: dict[str, Any]
    template_version: dict[str, Any]


class DuplicateTemplateError(Exception):
    """Raised when document_templates.code already exists."""

    def __init__(self, code: str, template_id: Optional[str] = None):
        self.code = code
        self.template_id = template_id
        super().__init__(f"Template code already exists: {code}")


class TemplateIngestError(Exception):
    """Validation or ingest failure with an HTTP-friendly message."""

    def __init__(self, message: str, status_code: int = 400):
        self.status_code = status_code
        super().__init__(message)
