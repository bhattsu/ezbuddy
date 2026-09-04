"""Schemas for court-form Textract question extraction."""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class FormQuestion(BaseModel):
    """One court-form field asked as a question with a simple string answer."""

    question: str
    answer: str = ""
    field: Optional[str] = Field(
        default=None,
        description="Original form label from Textract, when available.",
    )
    page: Optional[int] = None


class CourtFormQuestionsLLMOutput(BaseModel):
    """Structured LLM output for court-form field questions."""

    questions: List[FormQuestion] = Field(default_factory=list)


class CourtFormQuestionsResponse(BaseModel):
    document_id: str
    file_name: str
    source: str = Field(description="upload or s3")
    questions: List[FormQuestion] = Field(default_factory=list)
    message: str = (
        "Court form fields extracted. Each item is a question with a simple string answer."
    )
