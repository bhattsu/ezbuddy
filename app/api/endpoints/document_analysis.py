"""
Document analysis endpoint.

POST /analyze — VLM visually inspects PDFs for filled and blank form fields.
DOCX files use text/form extraction followed by LLM normalization.
"""

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile

from app.api.dependencies.auth import get_optional_auth
from app.api.dependencies.rate_limit import limiter, rate_limit_string
from app.api.schemas.document import FileType
from app.api.schemas.legal_filing import DocumentAnalysisResponse
from app.services.document_analysis_service import DocumentAnalysisService
from app.utils.file_utils import FileValidator, TempFileManager

logger = logging.getLogger(__name__)
router = APIRouter()


def get_document_analysis_service() -> DocumentAnalysisService:
    return DocumentAnalysisService()


@router.post(
    "/analyze",
    response_model=DocumentAnalysisResponse,
    summary="Analyze a court document and detect blank form fields",
    description=(
        "Upload a PDF or DOCX court file. PDFs are visually inspected page-by-page "
        "in one full-document VLM call to identify filled values and every blank "
        "form field. DOCX files use extracted text/forms with LLM normalization."
    ),
)
@limiter.limit(rate_limit_string)
async def analyze_document(
    request: Request,
    file: UploadFile = File(..., description="Court document (PDF or DOCX)"),
    _auth: Optional[Any] = Depends(get_optional_auth),
    analysis_service: DocumentAnalysisService = Depends(get_document_analysis_service),
):
    temp_file_path = None
    try:
        await FileValidator.validate_file_size(file)
        file_bytes = await file.read()
        if not file_bytes:
            raise HTTPException(status_code=400, detail="Uploaded file is empty.")

        file_type = await FileValidator.detect_file_type(file, file_bytes)
        if file_type not in (FileType.PDF, FileType.DOCX, FileType.WORD):
            raise HTTPException(
                status_code=400,
                detail=f"Only PDF/DOCX are supported for analysis (got {file_type}).",
            )

        await file.seek(0)
        temp_file_path = await TempFileManager.save_upload_to_temp(file, file_type)
        source_name = file.filename or "court_document.pdf"

        result = await analysis_service.analyze_document(
            file_path=temp_file_path,
            file_bytes=file_bytes,
            file_name=source_name,
            file_type=file_type,
        )
        return result
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Document analysis failed")
        raise HTTPException(
            status_code=500, detail=f"Document analysis failed: {exc}"
        ) from exc
    finally:
        if temp_file_path:
            await TempFileManager.cleanup_temp_file(temp_file_path)
