"""
Court document generation service.

Upload blank PDF/DOCX/FTL + user data → filled HTML or filled FTL.

PDF/DOCX path:
  1) Lossless-compress PDF
  2) Split into pages
  3) Parallel VLM (default 3 pages at a time) recreates each page as filled HTML
  4) Assemble into one HTML document
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from typing import Any, Dict, Optional, Union

import pymupdf

from app.adapters.file_extraction.extractors.textract.utils import docx_to_pdf_bytes
from app.api.schemas.document_generation import GenerateHtmlResponse
from app.config.settings import settings
from app.services.llm_service import LLMService

logger = logging.getLogger(__name__)


def field_data_to_string(field_data: Union[Dict[str, Any], str]) -> str:
    if isinstance(field_data, str):
        return field_data
    return json.dumps(field_data, ensure_ascii=False, indent=2)


class DocumentGenerationService:
    """Generate filled court forms from blank PDF/DOCX/FTL + user data."""

    def __init__(self, llm_service: Optional[LLMService] = None):
        self.llm_service = llm_service or LLMService()

    def pdf_page_count(self, pdf_bytes: bytes) -> int:
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        try:
            return doc.page_count
        finally:
            doc.close()

    @staticmethod
    def compress_pdf_lossless(
        pdf_bytes: bytes,
        expected_page_count: int,
    ) -> tuple[bytes, Dict[str, Any]]:
        """Structurally compress PDF without lossy rasterization when safe."""
        original_size = len(pdf_bytes)
        metadata: Dict[str, Any] = {
            "applied": False,
            "original_bytes": original_size,
            "vlm_input_bytes": original_size,
            "saved_bytes": 0,
            "ratio": 1.0,
            "method": "lossless_structural",
        }
        if original_size < 256 * 1024:
            metadata["reason"] = "below_256kb_threshold"
            return pdf_bytes, metadata

        original = None
        compressed_doc = None
        try:
            original = pymupdf.open(stream=pdf_bytes, filetype="pdf")
            compressed = original.tobytes(
                garbage=3,
                deflate=True,
                deflate_images=True,
                deflate_fonts=True,
                use_objstms=1,
            )
            if len(compressed) >= original_size:
                metadata["reason"] = "no_size_reduction"
                return pdf_bytes, metadata

            compressed_doc = pymupdf.open(stream=compressed, filetype="pdf")
            if (
                original.page_count != expected_page_count
                or compressed_doc.page_count != expected_page_count
            ):
                metadata["reason"] = "page_count_mismatch"
                return pdf_bytes, metadata

            matrix = pymupdf.Matrix(1, 1)
            for index in range(expected_page_count):
                original_page = original[index]
                compressed_page = compressed_doc[index]
                if original_page.rect != compressed_page.rect:
                    metadata["reason"] = f"page_{index + 1}_geometry_mismatch"
                    return pdf_bytes, metadata

                original_pix = original_page.get_pixmap(matrix=matrix, alpha=False)
                compressed_pix = compressed_page.get_pixmap(matrix=matrix, alpha=False)
                if (
                    original_pix.width != compressed_pix.width
                    or original_pix.height != compressed_pix.height
                    or hashlib.sha256(original_pix.samples).digest()
                    != hashlib.sha256(compressed_pix.samples).digest()
                ):
                    metadata["reason"] = f"page_{index + 1}_render_mismatch"
                    return pdf_bytes, metadata

            compressed_size = len(compressed)
            metadata.update(
                {
                    "applied": True,
                    "vlm_input_bytes": compressed_size,
                    "saved_bytes": original_size - compressed_size,
                    "ratio": round(compressed_size / original_size, 4),
                    "render_verified": True,
                }
            )
            return compressed, metadata
        except Exception as exc:
            logger.warning("Lossless PDF compression skipped: %s", exc)
            metadata["reason"] = "compression_error"
            return pdf_bytes, metadata
        finally:
            if compressed_doc is not None:
                compressed_doc.close()
            if original is not None:
                original.close()

    async def docx_to_pdf(self, docx_path: str) -> bytes:
        return await asyncio.to_thread(docx_to_pdf_bytes, docx_path)

    async def generate_filled_html(
        self,
        pdf_bytes: bytes,
        file_name: str,
        field_data: Union[Dict[str, Any], str],
        *,
        source_file_type: str = "pdf",
    ) -> GenerateHtmlResponse:
        """
        Compress PDF → split pages → VLM 3-at-a-time → assemble filled HTML.

        Each page image + user data is sent to the VLM to recreate that page as
        filled HTML matching alignment, colors, fonts, and font sizes.
        """
        document_id = str(uuid.uuid4())
        data_string = field_data_to_string(field_data)
        max_pages = int(getattr(settings, "MAX_PAGES_PER_REQUEST", 50) or 50)
        concurrency = max(
            1,
            int(
                getattr(settings, "DOC_GEN_VLM_CONCURRENCY", None)
                or getattr(settings, "MAX_CONCURRENT_PAGES", 3)
                or 3
            ),
        )

        page_count = self.pdf_page_count(pdf_bytes)
        if page_count == 0:
            raise ValueError("PDF has no pages.")
        if page_count > max_pages:
            raise ValueError(
                f"PDF has {page_count} pages; max allowed is {max_pages}."
            )

        logger.info(
            "[HTML Generation] Starting '%s' (%d page(s), %d bytes, timeout=%ss)",
            file_name,
            page_count,
            len(pdf_bytes),
            self.llm_service.doc_gen_timeout,
        )

        vlm_pdf_bytes, compression = await asyncio.to_thread(
            self.compress_pdf_lossless,
            pdf_bytes,
            page_count,
        )
        logger.info(
            "[HTML Generation] PDF compression applied=%s, %d -> %d bytes",
            compression["applied"],
            compression["original_bytes"],
            compression["vlm_input_bytes"],
        )

        mode = "page_parallel_spec_absolute_html"
        logger.info(
            "[HTML Generation] Mode=%s (%d page(s), concurrency=%d)",
            mode,
            page_count,
            concurrency,
        )
        html_content, pages_html = await self.llm_service.generate_filled_html_pages_vlm(
            pdf_bytes=vlm_pdf_bytes,
            field_data=data_string,
            page_count=page_count,
            file_name=file_name,
            max_concurrent=concurrency,
        )

        logger.info(
            "[HTML Generation] Completed '%s': %d page(s), %d HTML chars (mode=%s)",
            file_name,
            page_count,
            len(html_content),
            mode,
        )

        return GenerateHtmlResponse(
            document_id=document_id,
            source_file_name=file_name,
            source_file_type=source_file_type,
            html_content=html_content,
            page_count=page_count,
            field_data=data_string,
            pages_html=pages_html,
            metadata={
                "mode": mode,
                "timeout_seconds": self.llm_service.doc_gen_timeout,
                "vlm_calls": page_count,
                "vlm_concurrency": concurrency,
                "batches": (page_count + concurrency - 1) // concurrency,
                "pdf_compression": compression,
                "stages": [
                    "lossless_pdf_compression",
                    "split_pages_to_images",
                    "parallel_vlm_page_spec_batches_of_3",
                    "deterministic_absolute_html_render",
                    "assemble_html_document",
                ],
            },
        )

    async def generate_filled_ftl(
        self,
        ftl_template: str,
        file_name: str,
        field_data: Union[Dict[str, Any], str],
    ) -> GenerateHtmlResponse:
        """Fill an uploaded FreeMarker (.ftl) template with user data."""
        document_id = str(uuid.uuid4())
        data_string = field_data_to_string(field_data)

        if not ftl_template.strip():
            raise ValueError("FTL template is empty.")

        logger.info(
            "[FTL Generation] Filling template '%s' (%d chars, timeout=%ss)",
            file_name,
            len(ftl_template),
            self.llm_service.doc_gen_timeout,
        )

        ftl_content = await self.llm_service.fill_ftl_with_data(
            ftl_template=ftl_template,
            field_data=data_string,
        )

        logger.info(
            "[FTL Generation] Completed '%s': %d FTL chars",
            file_name,
            len(ftl_content),
        )

        return GenerateHtmlResponse(
            document_id=document_id,
            source_file_name=file_name,
            source_file_type="ftl",
            ftl_content=ftl_content,
            page_count=0,
            field_data=data_string,
            pages_html=[],
            metadata={
                "mode": "ftl_fill",
                "timeout_seconds": self.llm_service.doc_gen_timeout,
            },
        )
