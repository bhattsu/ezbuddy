"""Extract court-form fields with Textract and return them as Q&A strings."""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any, List, Optional, Tuple
from urllib.parse import unquote, urlparse

from app.api.schemas.court_form_questions import (
    CourtFormQuestionsLLMOutput,
    CourtFormQuestionsResponse,
    FormQuestion,
)
from app.api.schemas.document import FileType
from app.core.prompts.context import format_llm_prompt
from app.utils.s3_utils import S3Manager

logger = logging.getLogger(__name__)

_YES_NO_PREFIXES = (
    "is ",
    "are ",
    "do ",
    "does ",
    "did ",
    "has ",
    "have ",
    "had ",
    "was ",
    "were ",
    "can ",
    "will ",
    "would ",
    "should ",
    "may ",
)

QUESTIONS_PROMPT = """You are extracting fillable fields from a US court form.

Turn every form field into a short question a filer can answer in one string.
Prefer Textract key/value pairs when present. Otherwise use the extracted page text.
If a value is blank or only an underline/checkbox, keep the question and set answer to "".
Do not invent values. Do not include legal boilerplate as a question.

Return ONLY valid JSON:
{{
  "questions": [
    {{"question": "What is the cause number?", "answer": "2025-1234", "field": "Cause Number", "page": 1}}
  ]
}}

Document file name: {file_name}

Textract form fields:
{fields}

Extracted text:
{text}
"""


def _is_textract_unavailable(exc: BaseException) -> bool:
    text = str(exc)
    names = {type(exc).__name__}
    cause = exc.__cause__ or exc.__context__
    if cause is not None:
        names.add(type(cause).__name__)
        text = f"{text} {cause}"
    code = getattr(exc, "response", {}).get("Error", {}).get("Code", "") if hasattr(exc, "response") else ""
    if cause is not None and hasattr(cause, "response"):
        code = code or (cause.response or {}).get("Error", {}).get("Code", "")
    haystack = f"{code} {text} {' '.join(names)}".lower()
    return any(
        token in haystack
        for token in (
            "subscriptionrequiredexception",
            "needs a subscription for the service",
            "textract is not authorized",
            "not subscribed to this service",
        )
    )


def field_label_to_question(label: str) -> str:
    """Turn a Textract form key into a short English question."""
    cleaned = re.sub(r"\s+", " ", (label or "").strip()).rstrip(" :.?")
    if not cleaned:
        return "What is this field?"
    lower = cleaned.lower()
    if lower.startswith(_YES_NO_PREFIXES) or lower.startswith(("check ", "select ")):
        return cleaned if cleaned.endswith("?") else f"{cleaned}?"
    if lower.endswith("?"):
        return cleaned
    article = "an" if cleaned[:1].lower() in "aeiou" else "the"
    return f"What is {article} {cleaned}?"


def parse_s3_location(raw: str) -> Tuple[Optional[str], Optional[str]]:
    """Parse s3:// or Amazon S3 HTTPS URLs into (bucket, key)."""
    text = (raw or "").strip()
    if not text:
        return None, None
    if text.startswith("s3://"):
        without = text[5:]
        bucket, _, key = without.partition("/")
        return (bucket or None, unquote(key) if key else None)

    parsed = urlparse(text)
    host = (parsed.netloc or "").lower()
    path = unquote(parsed.path.lstrip("/"))
    if not host or "amazonaws.com" not in host:
        return None, None

    # virtual-hosted: bucket.s3.amazonaws.com / bucket.s3.region.amazonaws.com
    if ".s3." in host or host.endswith(".s3.amazonaws.com"):
        bucket = host.split(".s3", 1)[0]
        return bucket or None, path or None

    # path-style: s3.amazonaws.com/bucket/key or s3.region.amazonaws.com/bucket/key
    if host.startswith("s3.") or host == "s3.amazonaws.com":
        bucket, _, key = path.partition("/")
        return bucket or None, key or None
    return None, None


class CourtFormQuestionService:
    """Run Textract on a court PDF and return field questions with string answers."""

    def __init__(
        self,
        extractor=None,
        llm_service=None,
        s3_manager: Optional[S3Manager] = None,
    ):
        if extractor is None:
            from app.adapters.file_extraction.extractors.textract.extractor import (
                AWSTextractExtractor,
            )

            extractor = AWSTextractExtractor()
        if llm_service is None:
            from app.services.llm_service import LLMService

            llm_service = LLMService()
        self.extractor = extractor
        self.llm_service = llm_service
        self.s3_manager = s3_manager or S3Manager()

    @staticmethod
    def questions_from_forms(extraction: Any) -> List[FormQuestion]:
        questions: List[FormQuestion] = []
        seen: set[str] = set()
        for page in getattr(extraction, "pages_data", None) or []:
            page_no = getattr(page, "page_number", None)
            for form in getattr(page, "forms", None) or []:
                key = str(getattr(form, "key", None) or "").strip()
                if not key:
                    continue
                norm = re.sub(r"\s+", " ", key).lower()
                if norm in seen:
                    continue
                seen.add(norm)
                value = getattr(form, "value", None)
                questions.append(
                    FormQuestion(
                        question=field_label_to_question(key),
                        answer="" if value is None else str(value).strip(),
                        field=key,
                        page=page_no,
                    )
                )
        return questions

    async def _llm_questions(
        self, file_name: str, extraction: Any, fallback: List[FormQuestion]
    ) -> List[FormQuestion]:
        pages = [
            (
                getattr(page, "page_number", None),
                (getattr(page, "text_content", "") or "").strip(),
            )
            for page in getattr(extraction, "pages_data", None) or []
        ]
        pages = [(num, text) for num, text in pages if text]
        if not pages and not fallback:
            return []

        chunks: List[List[tuple]] = []
        if pages:
            for index in range(0, len(pages), 3):
                chunks.append(pages[index : index + 3])
        else:
            chunks = [[]]

        merged: List[FormQuestion] = []
        seen: set[str] = set()
        for chunk in chunks:
            chunk_text = "\n\n".join(
                f"=== Page {num} ===\n{text}" for num, text in chunk
            )
            page_hint = ", ".join(str(num) for num, _ in chunk if num is not None)
            field_lines = [
                f"p{item.page or '?'}: {item.field} = {item.answer!r}"
                for item in fallback
                if not chunk
                or item.page in {num for num, _ in chunk}
                or item.page is None
            ]
            prompt = format_llm_prompt(
                QUESTIONS_PROMPT,
                file_name=f"{file_name} (pages {page_hint or 'unknown'})",
                fields="\n".join(field_lines) or "(none)",
                text=chunk_text[:12000] or "(none)",
            )
            rows = await self._ask_question_model(prompt)
            for item in rows:
                key = item.question.strip().lower()
                if not key or key in seen:
                    continue
                seen.add(key)
                merged.append(item)
        return merged or fallback

    async def _ask_question_model(self, prompt: str) -> List[FormQuestion]:
        bedrock = getattr(self.llm_service, "_bedrock", None)
        if bedrock is not None:
            try:
                parsed = await bedrock.invoke_structured_prompt(
                    prompt, CourtFormQuestionsLLMOutput
                )
                return [
                    FormQuestion(
                        question=(
                            item.question
                            if item.question.endswith("?")
                            else f"{item.question}?"
                        ),
                        answer=str(item.answer or "").strip(),
                        field=item.field,
                        page=item.page,
                    )
                    for item in parsed.questions
                    if item.question and item.question.strip()
                ]
            except Exception as exc:  # noqa: BLE001
                logger.warning("Structured court-form question pass failed: %s", exc)

        body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 8192,
            "temperature": 0,
            "messages": [{"role": "user", "content": prompt}],
        }
        try:
            result = await self.llm_service._invoke_with_timeout(body)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Court-form question LLM pass failed: %s", exc)
            return []
        return self._questions_from_llm_payload(result)

    @staticmethod
    def _questions_from_llm_payload(result: Any) -> List[FormQuestion]:
        rows: Any = None
        if isinstance(result, dict):
            rows = result.get("questions")
            if not isinstance(rows, list):
                nested = result.get("result")
                if isinstance(nested, dict):
                    rows = nested.get("questions")
            if not isinstance(rows, list) and isinstance(result.get("output"), str):
                from app.adapters.llm.bedrock import _parse_json_response

                repaired = _parse_json_response(result["output"])
                if isinstance(repaired, dict):
                    rows = repaired.get("questions")
        if not isinstance(rows, list):
            return []
        questions: List[FormQuestion] = []
        seen: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            question = str(row.get("question") or "").strip()
            if not question:
                continue
            key = question.lower()
            if key in seen:
                continue
            seen.add(key)
            page = row.get("page")
            questions.append(
                FormQuestion(
                    question=question if question.endswith("?") else f"{question}?",
                    answer="" if row.get("answer") is None else str(row.get("answer")).strip(),
                    field=(str(row.get("field")).strip() if row.get("field") else None),
                    page=int(page) if isinstance(page, int) else None,
                )
            )
        return questions

    async def download_s3(self, s3_url: str) -> Tuple[bytes, str]:
        bucket, key = parse_s3_location(s3_url)
        if not bucket or not key:
            raise ValueError(
                "Provide an s3://bucket/key URI or an Amazon S3 HTTPS URL."
            )
        data = await self.s3_manager.download_bytes(f"s3://{bucket}/{key}")
        file_name = key.rsplit("/", 1)[-1] or "court_form.pdf"
        return data, file_name

    @staticmethod
    def _local_pdf_extraction(file_bytes: bytes, file_name: str, document_id: str):
        from app.adapters.file_extraction.extractors.textract.utils import (
            extract_page_texts,
        )
        from app.api.schemas.document import (
            ExtractionMethod,
            ExtractionResponse,
            PageData,
            ProcessingStatus,
        )

        pages = extract_page_texts(file_bytes)
        pages_data = [
            PageData(page_number=index + 1, text_content=text)
            for index, text in enumerate(pages)
        ]
        return ExtractionResponse(
            document_id=document_id,
            file_name=file_name,
            file_type=FileType.PDF,
            status=ProcessingStatus.COMPLETED,
            method=ExtractionMethod.CUSTOM,
            total_pages=len(pages_data),
            pages_processed=len(pages_data),
            pages_data=pages_data,
            processing_time=0.0,
            summary={"extractor": "pymupdf", "textract": "unavailable"},
        )

    async def extract_questions(
        self,
        *,
        file_bytes: bytes,
        file_name: str,
        file_type: FileType,
        file_path: str,
        source: str,
    ) -> CourtFormQuestionsResponse:
        document_id = str(uuid.uuid4())
        used_textract = True
        try:
            extraction = await self.extractor.extract_from_bytes(
                file_bytes=file_bytes,
                file_name=file_name,
                file_type=file_type,
                document_id=document_id,
            )
        except Exception as exc:
            if file_type != FileType.PDF or not _is_textract_unavailable(exc):
                raise
            logger.warning(
                "Textract is not enabled for this AWS account; extracting PDF text locally"
            )
            used_textract = False
            extraction = self._local_pdf_extraction(file_bytes, file_name, document_id)

        from_forms = self.questions_from_forms(extraction)
        questions = await self._llm_questions(file_name, extraction, from_forms)
        extractor_note = (
            "Textract form fields"
            if used_textract
            else "local PDF text (Textract is not subscribed on this AWS account)"
        )
        return CourtFormQuestionsResponse(
            document_id=extraction.document_id,
            file_name=file_name,
            source=source,
            questions=questions,
            message=(
                f"Extracted {len(questions)} court-form field"
                f"{'' if len(questions) == 1 else 's'} as questions from {extractor_note}."
            ),
        )
