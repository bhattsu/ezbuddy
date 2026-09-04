"""Schemas for court document generation (VLM → filled HTML)."""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class GenerateHtmlResponse(BaseModel):
    """Response for generated court documents (HTML and/or FTL)."""

    document_id: str
    source_file_name: str
    source_file_type: str = Field(
        default="pdf",
        description="Uploaded template type: pdf, docx, or ftl.",
    )
    html_content: str = Field(
        default="",
        description=(
            "Self-contained HTML that visually matches the uploaded blank PDF/DOCX, "
            "with user data filled into the blanks."
        ),
    )
    ftl_content: Optional[str] = Field(
        default=None,
        description="Filled FreeMarker template when the upload was an .ftl file.",
    )
    page_count: int = 0
    field_data: str = Field(
        ...,
        description="String data that was passed to the LLM for filling.",
    )
    pages_html: List[str] = Field(
        default_factory=list,
        description="Per-page HTML fragments before final assembly (optional).",
    )
    message: str = (
        "Generated filled court document from blank template + user data via LLM/VLM."
    )
    metadata: Dict[str, Any] = Field(default_factory=dict)
