"""
Document analysis service.

Upload a court PDF/DOCX → Textract (or configured extractor) → LLM formats
user details + missing fields for the filing form.
"""

from __future__ import annotations

import base64
import json
import logging
import uuid
from typing import Any, Dict, List, Optional

from app.api.schemas.document import (
    ExtractionMethod,
    ExtractionRequest,
    FileType,
)
from app.api.schemas.legal_filing import DocumentAnalysisResponse
from app.config.settings import settings
from app.core.prompts.context import format_llm_prompt
from app.services.extraction_service import ExtractionService
from app.services.llm_service import LLMService

logger = logging.getLogger(__name__)

ANALYSIS_OUTPUT_FORMAT: Dict[str, Any] = {
    "document_classification": "petition | summons | order | affidavit | other",
    "case_type": "string or null",
    "sub_case_type": "string or null",
    "user_details": {
        "cause_number": "string or empty",
        "court_name": "string or empty",
        "county": "string or empty",
        "filing_date": "string or empty",
        "petitioner": {
            "full_name": "string or empty",
            "address": "string or empty",
            "city": "string or empty",
            "state": "string or empty",
            "zip": "string or empty",
            "phone": "string or empty",
            "email": "string or empty",
        },
        "respondent": {
            "full_name": "string or empty",
            "address": "string or empty",
            "city": "string or empty",
            "state": "string or empty",
            "zip": "string or empty",
        },
    },
    "extracted_fields": {"any_key": "any_value from the document"},
    "missing_fields": ["list of dotted field paths still needed, e.g. petitioner.email"],
    "missing_field_details": [
        {
            "field_path": "stable dotted path or snake_case field name",
            "label": "exact visible field label",
            "page": "1-based page number",
            "section": "nearest visible section heading",
            "field_type": "text | date | checkbox | radio | signature | initials | other",
        }
    ],
}

ANALYSIS_PROMPT = """You are a US court-document analyst for US Legal Pro.

Given OCR / Textract extraction from a court filing (PDF or DOCX), produce a JSON object that:
1. Classifies the document (petition, summons, order, affidavit, or other).
2. Extracts all identifiable party and case fields into user_details.
3. Lists every field that is blank, illegible, or missing in missing_fields
   (use dotted paths like petitioner.full_name, respondent.address).
4. Puts a flat key/value map of everything found in extracted_fields.

Return ONLY valid JSON matching this structure:
{format}

Document file name: {file_name}

Extracted text / forms / tables:
{extraction}
"""

VLM_FORM_ANALYSIS_PROMPT = """You are a vision-based US court-form analyst.

Inspect the COMPLETE attached court-form PDF visually, page by page. Your main
task is to identify every field that exists on the form and determine whether
it is filled or blank. Do not rely only on extracted text: inspect blank lines,
empty boxes, table cells, checkboxes, radio buttons, signature areas, initials,
dates, and handwritten or typed entries.

Rules:
1. Treat the PDF's visible form layout as the source of truth.
2. Extract values that are visibly filled into `user_details` and
   `extracted_fields`.
3. Put every genuinely unfilled input field in `missing_fields`.
4. Do not mark static legal text, decorative lines, paragraph underlines, or
   intentionally unused options as missing fields.
5. For checkbox/radio groups, report the group as missing only when the form
   expects a choice and no option is selected.
6. A signature/date/initial field is missing when its designated area is visibly
   blank. Do not claim a value that is illegible; mark that field missing.
7. Use stable dotted paths where possible, such as
   `petitioner.full_name`, `respondent.address`, or
   `children[0].date_of_birth`. Otherwise derive a clear snake_case name from
   the exact visible label.
8. Do not include fields that do not appear in this PDF merely because they are
   present in the example output structure.
9. Include one `missing_field_details` item for each missing field, with its
   exact label, 1-based page number, nearest section, and field type.
10. Return each missing path once, in page and reading order.

Return ONLY valid JSON matching this structure:
{format}

Document file name: {file_name}
Expected page count: {pages_hint}
"""


class DocumentAnalysisService:
    """Analyze uploaded court documents and identify missing form fields."""

    def __init__(
        self,
        extraction_service: Optional[ExtractionService] = None,
        llm_service: Optional[LLMService] = None,
    ):
        self.extraction_service = extraction_service or ExtractionService()
        self.llm_service = llm_service or LLMService()

    def _textract_key_values(self, extraction: Any) -> Dict[str, Any]:
        """Flatten form key/value pairs from extractor pages into a dict."""
        kv: Dict[str, Any] = {}
        pages = getattr(extraction, "pages_data", None) or []
        for page in pages:
            for form in getattr(page, "forms", None) or []:
                key = getattr(form, "key", None) or (form.get("key") if isinstance(form, dict) else None)
                value = getattr(form, "value", None) or (form.get("value") if isinstance(form, dict) else None)
                if key:
                    kv[str(key).strip()] = value if value is not None else ""
        return kv

    def _extraction_text_blob(self, extraction: Any) -> str:
        parts: List[str] = []
        pages = getattr(extraction, "pages_data", None) or []
        for page in pages:
            page_no = getattr(page, "page_number", "?")
            text = getattr(page, "text_content", "") or ""
            if text.strip():
                parts.append(f"=== Page {page_no} text ===\n{text}")
            forms = getattr(page, "forms", None) or []
            if forms:
                form_lines = []
                for form in forms:
                    key = getattr(form, "key", None) or (form.get("key") if isinstance(form, dict) else "")
                    value = getattr(form, "value", None) or (form.get("value") if isinstance(form, dict) else "")
                    form_lines.append(f"{key}: {value}")
                parts.append(f"=== Page {page_no} forms ===\n" + "\n".join(form_lines))
            tables = getattr(page, "tables", None) or []
            if tables:
                parts.append(f"=== Page {page_no} tables ===\n{json.dumps([t.model_dump() if hasattr(t, 'model_dump') else t for t in tables], default=str)[:4000]}")
        if getattr(extraction, "llm_output", None):
            parts.append(
                "=== Prior LLM extraction ===\n"
                + json.dumps(extraction.llm_output, default=str)[:6000]
            )
        return "\n\n".join(parts) if parts else "(no text extracted)"

    async def _llm_format_analysis(
        self, file_name: str, extraction: Any
    ) -> Dict[str, Any]:
        prompt = format_llm_prompt(
            ANALYSIS_PROMPT,
            format=json.dumps(ANALYSIS_OUTPUT_FORMAT, indent=2),
            file_name=file_name,
            extraction=self._extraction_text_blob(extraction)[:24000],
        )
        body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 4096,
            "temperature": 0.1,
            "top_p": 0.9,
            "messages": [{"role": "user", "content": prompt}],
        }
        try:
            result = await self.llm_service._invoke_with_timeout(body)
            if isinstance(result, dict) and result:
                # Some invokers wrap under "result"
                if "user_details" in result or "missing_fields" in result:
                    return result
                nested = result.get("result")
                if isinstance(nested, dict):
                    return nested
                return result
        except Exception as exc:  # noqa: BLE001
            logger.error("LLM document analysis formatting failed: %s", exc)
        return {}

    async def _vlm_analyze_pdf(
        self,
        file_name: str,
        pdf_bytes: bytes,
    ) -> Dict[str, Any]:
        """Visually inspect the complete PDF in one VLM call."""
        prompt = format_llm_prompt(
            VLM_FORM_ANALYSIS_PROMPT,
            format=json.dumps(ANALYSIS_OUTPUT_FORMAT, indent=2),
            file_name=file_name,
            pages_hint="all pages in the attached PDF",
        )
        body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 8192,
            "temperature": 0,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "document",
                            "source": {
                                "type": "base64",
                                "media_type": "application/pdf",
                                "data": base64.b64encode(pdf_bytes).decode("utf-8"),
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        }
        result = await self.llm_service._invoke_with_timeout(
            body,
            timeout=self.llm_service.doc_gen_timeout,
            stream=True,
        )
        if not isinstance(result, dict) or not result:
            raise RuntimeError("VLM returned no form analysis")
        nested = result.get("result")
        if isinstance(nested, dict):
            result = nested
        if not (
            "missing_fields" in result
            or "missing_field_details" in result
            or "extracted_fields" in result
        ):
            raise RuntimeError("VLM response did not contain form field analysis")
        return result

    @staticmethod
    def _normalize_missing_analysis(
        result: Dict[str, Any],
    ) -> tuple[List[str], List[Dict[str, Any]]]:
        """Normalize VLM missing-field output while preserving page order."""
        details_raw = result.get("missing_field_details") or []
        details = [item for item in details_raw if isinstance(item, dict)]

        fields_raw = result.get("missing_fields") or []
        fields: List[str] = []
        for item in fields_raw:
            if isinstance(item, str) and item.strip():
                fields.append(item.strip())
            elif isinstance(item, dict):
                path = item.get("field_path") or item.get("path") or item.get("name")
                if path:
                    fields.append(str(path).strip())

        if not fields:
            fields = [
                str(item.get("field_path")).strip()
                for item in details
                if item.get("field_path")
            ]

        # Deduplicate without changing the VLM's page/reading order.
        fields = list(dict.fromkeys(field for field in fields if field))
        return fields, details

    @staticmethod
    def _collect_missing_from_details(user_details: Dict[str, Any], prefix: str = "") -> List[str]:
        missing: List[str] = []
        for key, value in (user_details or {}).items():
            path = f"{prefix}.{key}" if prefix else key
            if isinstance(value, dict):
                missing.extend(DocumentAnalysisService._collect_missing_from_details(value, path))
            elif value is None or (isinstance(value, str) and not value.strip()):
                missing.append(path)
        return missing

    async def analyze_document(
        self,
        *,
        file_path: str,
        file_bytes: bytes,
        file_name: str,
        file_type: FileType,
    ) -> DocumentAnalysisResponse:
        document_id = str(uuid.uuid4())
        logger.info(
            "Analyzing court document %s (%s, %d bytes)",
            file_name,
            file_type,
            len(file_bytes),
        )

        if file_type == FileType.PDF:
            logger.info(
                "[Document Analysis] Mode=single_pdf_vlm_call (%d bytes)",
                len(file_bytes),
            )
            vlm_result = await self._vlm_analyze_pdf(file_name, file_bytes)
            missing_fields, missing_details = self._normalize_missing_analysis(
                vlm_result
            )
            user_details = vlm_result.get("user_details") or {}
            extracted_fields = vlm_result.get("extracted_fields") or {}
            if not isinstance(user_details, dict):
                user_details = {}
            if not isinstance(extracted_fields, dict):
                extracted_fields = {}

            return DocumentAnalysisResponse(
                document_id=document_id,
                file_name=file_name,
                extracted_fields=extracted_fields,
                user_details=user_details,
                missing_fields=missing_fields,
                missing_field_details=missing_details,
                document_classification=vlm_result.get(
                    "document_classification"
                ),
                case_type=vlm_result.get("case_type"),
                sub_case_type=vlm_result.get("sub_case_type"),
                raw_textract=None,
                metadata={
                    "analyzer": "vlm",
                    "mode": "single_pdf_vlm_call",
                    "vlm_calls": 1,
                },
            )

        method = ExtractionMethod.TEXTRACT

        request = ExtractionRequest(
            method=method,
            process_with_llm=False,
            extract_text=True,
            extract_tables=True,
            extract_images=False,
        )

        extraction = await self.extraction_service.process_document(
            file_path=file_path,
            file_bytes=file_bytes,
            file_name=file_name,
            file_type=file_type,
            request=request,
            document_id=document_id,
        )

        raw_kv = self._textract_key_values(extraction)
        llm_result = await self._llm_format_analysis(file_name, extraction)

        user_details = llm_result.get("user_details") or {}
        if not isinstance(user_details, dict):
            user_details = {}

        # Seed empty user_details from Textract key/values when LLM returned little
        if not user_details and raw_kv:
            user_details = {"extracted_raw": raw_kv}

        extracted_fields = llm_result.get("extracted_fields") or dict(raw_kv)
        if not isinstance(extracted_fields, dict):
            extracted_fields = dict(raw_kv)

        missing_fields = llm_result.get("missing_fields")
        if not isinstance(missing_fields, list) or not missing_fields:
            missing_fields = self._collect_missing_from_details(user_details)

        return DocumentAnalysisResponse(
            document_id=document_id,
            file_name=file_name,
            extracted_fields=extracted_fields,
            user_details=user_details,
            missing_fields=[str(m) for m in missing_fields],
            document_classification=llm_result.get("document_classification"),
            case_type=llm_result.get("case_type"),
            sub_case_type=llm_result.get("sub_case_type"),
            raw_textract={"key_values": raw_kv} if raw_kv else None,
            metadata={
                "extractor": getattr(extraction, "extraction_method", None)
                or settings.EXTRACTOR_TYPE,
                "pages_processed": getattr(extraction, "pages_processed", None),
            },
        )
