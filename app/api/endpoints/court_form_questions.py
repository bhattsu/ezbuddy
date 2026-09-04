"""Court-form Textract API: upload a PDF or pass an S3 link, return field questions."""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile

from app.api.dependencies.auth import get_optional_auth
from app.api.dependencies.rate_limit import limiter, rate_limit_string
from app.api.schemas.court_form_questions import CourtFormQuestionsResponse
from app.api.schemas.document import FileType
from app.services.court_form_question_service import CourtFormQuestionService
from app.utils.file_utils import FileValidator, TempFileManager

logger = logging.getLogger(__name__)
router = APIRouter()


def get_court_form_question_service() -> CourtFormQuestionService:
    return CourtFormQuestionService()


@router.post(
    "/court-form/questions",
    response_model=CourtFormQuestionsResponse,
    summary="Extract court-form fields as questions",
    description=(
        "Upload a court-form PDF or provide an S3 link. AWS Textract reads the "
        "form fields and the API returns each field as a question with a simple "
        "string answer (blank when the field is empty)."
    ),
)
@limiter.limit(rate_limit_string)
async def extract_court_form_questions(
    request: Request,
    file: Optional[UploadFile] = File(None, description="Court form PDF"),
    s3_url: Optional[str] = Form(
        None,
        description="s3://bucket/key or Amazon S3 HTTPS URL of a court-form PDF",
    ),
    _auth: Optional[Any] = Depends(get_optional_auth),
    service: CourtFormQuestionService = Depends(get_court_form_question_service),
):
    has_upload = bool(file and file.filename)
    has_s3 = bool((s3_url or "").strip())
    if has_upload == has_s3:
        raise HTTPException(
            status_code=400,
            detail="Provide either a PDF upload or an s3_url, not both or neither.",
        )

    temp_file_path = None
    try:
        if has_upload:
            await FileValidator.validate_file_size(file)
            file_bytes = await file.read()
            if not file_bytes:
                raise HTTPException(status_code=400, detail="Uploaded file is empty.")
            file_type = await FileValidator.detect_file_type(file, file_bytes)
            source_name = file.filename or "court_form.pdf"
            source = "upload"
        else:
            try:
                file_bytes, source_name = await service.download_s3(s3_url.strip())
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            except RuntimeError as exc:
                raise HTTPException(status_code=502, detail=str(exc)) from exc
            if not file_bytes:
                raise HTTPException(status_code=400, detail="S3 object is empty.")
            file_type = FileValidator.detect_file_type_from_bytes(file_bytes, source_name)
            source = "s3"

        if file_type not in (FileType.PDF, FileType.IMAGE):
            raise HTTPException(
                status_code=400,
                detail=f"Only PDF court forms are supported (got {file_type}).",
            )

        temp_file_path = await TempFileManager.save_bytes_to_temp(
            file_bytes, source_name, file_type
        )
        return await service.extract_questions(
            file_bytes=file_bytes,
            file_name=source_name,
            file_type=file_type,
            file_path=temp_file_path,
            source=source,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Court-form question extraction failed")
        raise HTTPException(
            status_code=500,
            detail=f"Court-form question extraction failed: {exc}",
        ) from exc
    finally:
        if temp_file_path:
            await TempFileManager.cleanup_temp_file(temp_file_path)
