"""
Core prompt templates and builders.

Extraction prompts live in ``templates.py``.
Document-generation / VLM prompt strings live in ``document_generation.py``.
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
    MAP_SLOTS_TO_DATA_PROMPT,
)
from app.core.prompts.loader import get_prompt_builder
from app.core.prompts.templates import (
    build_key_value_extraction_prompt,
    convert_to_json_schema,
)

__all__ = [
    "build_key_value_extraction_prompt",
    "convert_to_json_schema",
    "get_prompt_builder",
    "FILLED_HTML_DOCUMENT_PROMPT",
    "FILLED_HTML_PAGE_PROMPT",
    "FILL_PAGE_OVERLAYS_PROMPT",
    "MAP_SLOTS_TO_DATA_PROMPT",
    "CLASSIFY_PDF_FILLABLE_PROMPT",
    "FILLED_FTL_VLM_PROMPT",
    "FILL_FTL_CHUNK_PROMPT",
    "MAP_FIELDS_TO_ACROFORM_PROMPT",
    "FILL_FTL_WITH_DATA_PROMPT",
    "ENHANCE_CUSTOM_PROMPT_WITH_FORMAT",
]
