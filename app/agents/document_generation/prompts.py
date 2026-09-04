"""
Re-export document-generation prompt templates for agent convenience.

Canonical location: ``app.core.prompts.document_generation``.
Agents should ``.format(...)`` these strings with runtime variables.
"""

from app.core.prompts.document_generation import (
    CLASSIFY_PDF_FILLABLE_PROMPT,
    ENHANCE_CUSTOM_PROMPT_WITH_FORMAT,
    FILL_FTL_CHUNK_PROMPT,
    FILL_FTL_WITH_DATA_PROMPT,
    FILL_PAGE_OVERLAYS_PROMPT,
    FILLED_FTL_VLM_PROMPT,
    FILLED_HTML_DOCUMENT_PROMPT,
    FILLED_HTML_PAGE_PROMPT,
    MAP_FIELDS_TO_ACROFORM_PROMPT,
)

__all__ = [
    "FILLED_HTML_DOCUMENT_PROMPT",
    "FILLED_HTML_PAGE_PROMPT",
    "FILL_PAGE_OVERLAYS_PROMPT",
    "CLASSIFY_PDF_FILLABLE_PROMPT",
    "FILLED_FTL_VLM_PROMPT",
    "FILL_FTL_CHUNK_PROMPT",
    "MAP_FIELDS_TO_ACROFORM_PROMPT",
    "FILL_FTL_WITH_DATA_PROMPT",
    "ENHANCE_CUSTOM_PROMPT_WITH_FORMAT",
]
