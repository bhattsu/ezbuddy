"""Document generation 2.0: extracted PDF fields + user answers → filled JSON."""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.dependencies.auth import get_optional_auth
from app.api.dependencies.rate_limit import limiter, rate_limit_string
from app.api.schemas.document_generation_v2 import GenerateV2Request, GenerateV2Response
from app.services.document_generation_v2_service import DocumentGenerationV2Service

logger = logging.getLogger(__name__)
router = APIRouter()


def get_document_generation_v2_service() -> DocumentGenerationV2Service:
    return DocumentGenerationV2Service()


@router.post(
    "/generate/v2",
    response_model=GenerateV2Response,
    summary="Generate filled form JSON (document generation 2.0)",
    description=(
        "Pass extracted court-form text/fields plus the user's answers. "
        "The LLM returns JSON whose keys are the exact PDF field labels and "
        "whose values are the matching user answers. This does not call the "
        "legacy /api/generate HTML/FTL pipeline."
    ),
)
@limiter.limit(rate_limit_string)
async def generate_document_v2(
    request: Request,
    payload: GenerateV2Request,
    _auth: Optional[Any] = Depends(get_optional_auth),
    service: DocumentGenerationV2Service = Depends(get_document_generation_v2_service),
) -> GenerateV2Response:
    has_text = bool((payload.extracted_text or "").strip())
    has_fields = bool(payload.extracted_fields)
    if not has_text and not has_fields:
        raise HTTPException(
            status_code=400,
            detail="Provide extracted_text and/or extracted_fields from the court PDF.",
        )
    try:
        return await service.generate(
            extracted_text=payload.extracted_text or "",
            extracted_fields=payload.extracted_fields,
            answers=payload.answers or {},
            file_name=payload.file_name or "",
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Document generation 2.0 failed")
        raise HTTPException(
            status_code=500,
            detail=f"Document generation 2.0 failed: {exc}",
        ) from exc
