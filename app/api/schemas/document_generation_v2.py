"""Schemas for document generation 2.0 (PDF field keys + user answers → JSON)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ExtractedFormField(BaseModel):
    """One form field as extracted from the court PDF."""

    field: Optional[str] = Field(
        default=None,
        description="Exact PDF / Textract field label. Used as the JSON key.",
    )
    question: Optional[str] = Field(
        default=None,
        description="Question that was asked to the user for this field.",
    )
    page: Optional[int] = None
    answer: Optional[str] = Field(
        default="",
        description="Optional answer already sitting on the extracted row.",
    )


class GenerateV2Request(BaseModel):
    extracted_text: str = Field(
        default="",
        description="Raw extracted PDF / Textract text used as the field-key source.",
    )
    extracted_fields: List[ExtractedFormField] = Field(
        default_factory=list,
        description="Extracted form fields / questions from the court PDF.",
    )
    answers: Dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "User answers keyed by PDF field label, question text, or slug "
            "(for example cause_number)."
        ),
    )
    file_name: Optional[str] = None


class GenerateV2LLMOutput(BaseModel):
    """Structured LLM output: PDF field label → filled value."""

    fields: Dict[str, str] = Field(default_factory=dict)


class GenerateV2Response(BaseModel):
    document_id: str
    file_name: str = ""
    fields: Dict[str, str] = Field(
        default_factory=dict,
        description="JSON keyed by the exact PDF field labels, values from user answers.",
    )
    message: str = "Filled form JSON generated from extracted PDF fields and user answers."
