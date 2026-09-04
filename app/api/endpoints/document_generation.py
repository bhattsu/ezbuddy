"""
Document generation endpoint.

POST /generate — upload a blank court PDF/DOCX/FTL + user data string.
- PDF/DOCX: compress → split pages → 3 parallel VLM calls → each page returns a
  structured page_spec (absolute geometry + filled fields) → deterministic
  absolute-position HTML → assemble locked multi-page HTML.
- FTL: LLM fills the FreeMarker template with user data.
"""

import json
import logging
import time
from functools import lru_cache
from typing import Any, Dict, Optional, Union

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

from app.api.dependencies.auth import get_optional_auth
from app.api.dependencies.rate_limit import limiter, rate_limit_string
from app.api.schemas.document import FileType
from app.api.schemas.document_generation import GenerateHtmlResponse
from app.services.document_generation_service import DocumentGenerationService
from app.utils.file_utils import FileValidator, TempFileManager

logger = logging.getLogger(__name__)
router = APIRouter()

SUPPORTED_TEMPLATE_TYPES = (FileType.PDF, FileType.WORD, FileType.DOCX, FileType.FTL)


@lru_cache(maxsize=1)
def get_document_generation_service() -> DocumentGenerationService:
    return DocumentGenerationService()


def _parse_fields_payload(fields: str) -> Union[Dict[str, Any], str]:
    """Accept nested JSON or freeform string user data."""
    try:
        parsed = json.loads(fields)
    except json.JSONDecodeError:
        return fields

    if isinstance(parsed, dict):
        return parsed
    if isinstance(parsed, str):
        return parsed
    return fields


@router.post(
    "/generate",
    summary="Generate a filled court document (HTML or FTL)",
    response_model=GenerateHtmlResponse,
    responses={
        200: {
            "description": (
                "PDF/DOCX: VLM returns filled HTML matching the form layout. "
                "FTL: LLM returns filled FreeMarker. "
                "Use response_format=html or ftl to download raw output."
            ),
            "content": {
                "application/json": {
                    "schema": GenerateHtmlResponse.model_json_schema()
                },
                "text/html": {"schema": {"type": "string"}},
                "text/plain": {"schema": {"type": "string"}},
            },
        }
    },
)
@limiter.limit(rate_limit_string)
async def generate_document(
    request: Request,
    file: UploadFile = File(
        ...,
        description="Blank court form template (PDF, DOCX, or FreeMarker .ftl).",
    ),
    fields: str = Form(
        ...,
        description=(
            "User data string to fill into the form blanks. Prefer nested JSON, e.g. "
            '{"cause_number":"2026-DR-001245","petitioner":{"full_name":"John Anderson"}}'
        ),
    ),
    response_format: str = Form(
        "json",
        description=(
            "json (default) returns GenerateHtmlResponse; "
            "html returns raw HTML (PDF/DOCX); "
            "ftl returns raw filled FTL (.ftl uploads)."
        ),
    ),
    _auth: Optional[Any] = Depends(get_optional_auth),
    generation_service: DocumentGenerationService = Depends(get_document_generation_service),
):
    temp_file_path = None

    try:
        from app.config.settings import get_settings

        settings = get_settings()
        if settings.ALLOWED_UPLOAD_CONTENT_TYPES:
            allowed = [
                s.strip().lower()
                for s in settings.ALLOWED_UPLOAD_CONTENT_TYPES.split(",")
                if s.strip()
            ]
            ct = (file.content_type or "").strip().lower()
            if ct and allowed and ct not in allowed:
                raise HTTPException(
                    status_code=400,
                    detail=f"Content-Type '{file.content_type}' not allowed. Allowed: {allowed}",
                )

        if not fields or not str(fields).strip():
            raise HTTPException(status_code=400, detail="fields string data is required.")

        field_data = _parse_fields_payload(fields)

        await FileValidator.validate_file_size(file)
        file_bytes = await file.read()
        if not file_bytes:
            raise HTTPException(status_code=400, detail="Uploaded file is empty.")

        file_type = await FileValidator.detect_file_type(file, file_bytes)
        if file_type not in SUPPORTED_TEMPLATE_TYPES:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Only PDF, DOCX, and FTL templates are supported for generation "
                    f"(got {file_type})."
                ),
            )

        source_name = file.filename or "court_template.pdf"
        logger.info(
            "=== [POST /api/generate] START generation for '%s' type=%s (%d bytes) ===",
            source_name,
            file_type.value,
            len(file_bytes),
        )

        await file.seek(0)
        temp_file_path = await TempFileManager.save_upload_to_temp(file, file_type)

        start = time.time()
        try:
            if file_type == FileType.FTL:
                try:
                    ftl_template = file_bytes.decode("utf-8-sig")
                except UnicodeDecodeError as exc:
                    raise HTTPException(
                        status_code=400,
                        detail="FTL file must be UTF-8 encoded text.",
                    ) from exc
                result = await generation_service.generate_filled_ftl(
                    ftl_template=ftl_template,
                    file_name=source_name,
                    field_data=field_data,
                )
            elif file_type in (FileType.WORD, FileType.DOCX):
                pdf_bytes = await generation_service.docx_to_pdf(temp_file_path)
                pdf_name = source_name.rsplit(".", 1)[0] + ".pdf"
                result = await generation_service.generate_filled_html(
                    pdf_bytes=pdf_bytes,
                    file_name=pdf_name,
                    field_data=field_data,
                    source_file_type="docx",
                )
            else:
                result = await generation_service.generate_filled_html(
                    pdf_bytes=file_bytes,
                    file_name=source_name,
                    field_data=field_data,
                    source_file_type="pdf",
                )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        elapsed = time.time() - start
        logger.info(
            "[POST /api/generate] Done in %.2fs | type=%s | pages=%d | html_chars=%d | ftl_chars=%s | id=%s",
            elapsed,
            result.source_file_type,
            result.page_count,
            len(result.html_content),
            len(result.ftl_content or ""),
            result.document_id,
        )

        headers = {
            "X-Document-Id": result.document_id,
            "X-Page-Count": str(result.page_count),
            "X-Source-File-Type": result.source_file_type,
        }
        out_base = source_name.rsplit(".", 1)[0]
        fmt = (response_format or "json").strip().lower()

        if fmt == "ftl":
            if not result.ftl_content:
                raise HTTPException(
                    status_code=400,
                    detail="response_format=ftl requires an .ftl template upload.",
                )
            return PlainTextResponse(
                content=result.ftl_content,
                media_type="text/plain",
                headers={
                    **headers,
                    "Content-Disposition": f'attachment; filename="{out_base}_filled.ftl"',
                },
            )

        if fmt == "html":
            if not result.html_content:
                raise HTTPException(
                    status_code=400,
                    detail="response_format=html requires a PDF or DOCX template upload.",
                )
            return HTMLResponse(
                content=result.html_content,
                headers={
                    **headers,
                    "Content-Disposition": f'attachment; filename="{out_base}_filled.html"',
                },
            )

        return JSONResponse(
            content=result.model_dump(),
            headers={
                **headers,
                "Content-Disposition": f'attachment; filename="{out_base}_filled.json"',
            },
        )

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Document generation failed")
        raise HTTPException(status_code=500, detail=f"Document generation failed: {exc}") from exc
    finally:
        if temp_file_path:
            await TempFileManager.cleanup_temp_file(temp_file_path)
