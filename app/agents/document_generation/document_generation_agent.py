"""
Document generation agent.

LLM / VLM calling code for court-form HTML, FTL, and AcroForm workflows.
Prompts live in ``app.core.prompts.document_generation``.

Primary HTML path:
  compress (service) → render each page → VLM batches of 3 produce page_spec JSON
  (absolute geometry + filled fields) → deterministic absolute-position HTML →
  assemble locked multi-page document.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

import pymupdf

from app.adapters.file_extraction.extractors.textract.utils import pdf_to_image
from app.adapters.llm.bedrock import Bedrock, get_bedrock
from app.config.settings import settings
from app.core.prompts.context import format_llm_prompt
from app.core.prompts.document_generation import (
    CLASSIFY_PDF_FILLABLE_PROMPT,
    FILL_FTL_CHUNK_PROMPT,
    FILL_FTL_WITH_DATA_PROMPT,
    FILLED_FTL_VLM_PROMPT,
    FILLED_HTML_DOCUMENT_PROMPT,
    FILLED_HTML_PAGE_PROMPT,
    MAP_FIELDS_TO_ACROFORM_PROMPT,
)
from app.services.page_spec_renderer import extract_page_spec, render_page_spec_to_html

logger = logging.getLogger(__name__)


class DocumentGenerationAgent:
    """Court document generation via Bedrock/OpenAI VLM and text LLMs."""

    def __init__(self, bedrock: Optional[Bedrock] = None):
        self.bedrock = bedrock or get_bedrock()

    @property
    def doc_gen_timeout(self) -> int:
        return self.bedrock.doc_gen_timeout

    @property
    def doc_gen_max_tokens(self) -> int:
        return int(getattr(settings, "DOC_GEN_MAX_TOKENS", 30000) or 30000)

    async def generate_filled_html_document_vlm(
        self,
        pdf_bytes: bytes,
        field_data: str,
        file_name: str = "court_form.pdf",
        page_count: Optional[int] = None,
    ) -> str:
        """Legacy single-call path: entire PDF + user data in one VLM request."""
        pdf_b64 = base64.b64encode(pdf_bytes).decode("utf-8")
        pages_hint = f"{page_count} page(s)" if page_count else "all pages"
        text_prompt = format_llm_prompt(
            FILLED_HTML_DOCUMENT_PROMPT,
            file_name=file_name,
            pages_hint=pages_hint,
            field_data=field_data,
        )

        content: List[Dict[str, Any]] = [
            {
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": pdf_b64,
                },
            },
            {"type": "text", "text": text_prompt},
        ]

        prompt_body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": self.doc_gen_max_tokens,
            "messages": [{"role": "user", "content": content}],
            "temperature": 0,
        }

        logger.info(
            "[VLM HTML] Single-call full-PDF generation starting (%s, %s bytes, timeout=%ss)",
            file_name,
            len(pdf_bytes),
            self.doc_gen_timeout,
        )
        llm_output = await self.bedrock.invoke_prompt_with_timeout(
            prompt_body, timeout=self.doc_gen_timeout, stream=True
        )
        html = self.bedrock.recover_html_from_llm_output(llm_output)
        if not html:
            raise RuntimeError("VLM did not return HTML for the full PDF")

        html = self.bedrock.strip_html_fences(html)
        if "<html" not in html.lower():
            html = (
                "<!DOCTYPE html><html><head><meta charset=\"utf-8\"/>"
                "<title>Filled Court Form</title></head><body>\n"
                f"{html}\n</body></html>"
            )
        return html

    @staticmethod
    def _pdf_page_sizes_inches(pdf_bytes: bytes, page_count: int) -> List[Tuple[float, float]]:
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        try:
            sizes: List[Tuple[float, float]] = []
            for i in range(page_count):
                rect = doc[i].rect
                sizes.append((float(rect.width) / 72.0, float(rect.height) / 72.0))
            return sizes
        finally:
            doc.close()

    @staticmethod
    def _assemble_html_document(page_sections: List[str], title: str) -> str:
        body = "\n".join(page_sections)
        safe_title = title.replace("<", "").replace(">", "")
        return (
            "<!DOCTYPE html><html><head><meta charset=\"utf-8\"/>"
            f"<title>{safe_title}</title>"
            "<style>"
            "html,body{margin:0;padding:0;background:#c8c8c8;}"
            ".pdf-page{"
            "box-sizing:border-box;position:relative;overflow:hidden;"
            "background:#fff;margin:12px auto;"
            "box-shadow:0 1px 4px rgba(0,0,0,.25);"
            "page-break-after:always;break-after:page;"
            "letter-spacing:normal!important;word-spacing:normal!important;"
            "-webkit-print-color-adjust:exact;print-color-adjust:exact;"
            "}"
            ".pdf-page *{letter-spacing:normal!important;word-spacing:normal!important;}"
            "@media print{"
            "html,body{background:#fff;}"
            ".pdf-page{margin:0;box-shadow:none;page-break-after:always;}"
            "}"
            "</style></head><body>\n"
            f"{body}\n</body></html>"
        )

    @staticmethod
    def _normalize_page_section(html: str, page_number: int) -> str:
        html = (html or "").strip()
        lower = html.lower()
        start = lower.find("<section")
        if start != -1:
            end = lower.rfind("</section>")
            if end != -1:
                html = html[start : end + len("</section>")]
                lower = html.lower()
        if "<section" not in lower:
            return (
                f'<section class="pdf-page" data-page="{page_number}">'
                f"{html}</section>"
            )
        if 'data-page="' not in lower and "data-page='" not in lower:
            html = html.replace(
                "<section",
                f'<section class="pdf-page" data-page="{page_number}"',
                1,
            )
        return html

    @staticmethod
    def _sanitize_page_html(html: str) -> str:
        """
        Hard-correct common VLM CSS/text failures that destroy court-form fidelity:
        exploded letter-spacing, excessive word-spacing, and character-spaced words.
        """
        import re

        # Neutralize destructive spacing declarations in CSS + inline styles.
        html = re.sub(
            r"letter-spacing\s*:\s*[^;\"'}]+",
            "letter-spacing:normal",
            html,
            flags=re.IGNORECASE,
        )
        html = re.sub(
            r"word-spacing\s*:\s*[^;\"'}]+",
            "word-spacing:normal",
            html,
            flags=re.IGNORECASE,
        )
        html = re.sub(
            r"font-stretch\s*:\s*[^;\"'}]+;?",
            "",
            html,
            flags=re.IGNORECASE,
        )

        # Collapse "n e g o t i a b l e" style runs inside text nodes only.
        def _fix_text_node(match: "re.Match[str]") -> str:
            text = match.group(1)

            def _fix_run(m: "re.Match[str]") -> str:
                return m.group(0).replace(" ", "")

            fixed = re.sub(
                r"(?<!\S)(?:[A-Za-z0-9]\s){3,}[A-Za-z0-9](?!\S)",
                _fix_run,
                text,
            )
            return f">{fixed}<"

        html = re.sub(r">([^<>]+)<", _fix_text_node, html)

        # Force normal spacing on the page regardless of model CSS.
        guard = (
            "<style data-docgen-guard=\"1\">"
            ".pdf-page,.pdf-page *{"
            "letter-spacing:normal!important;"
            "word-spacing:normal!important;"
            "}"
            "</style>"
        )
        lower = html.lower()
        idx = lower.find("<section")
        if idx != -1:
            gt = html.find(">", idx)
            if gt != -1:
                html = html[: gt + 1] + guard + html[gt + 1 :]
        return html

    async def generate_filled_html_pages_vlm(
        self,
        pdf_bytes: bytes,
        field_data: str,
        page_count: int,
        file_name: str = "court_form.pdf",
        max_concurrent: Optional[int] = None,
    ) -> Tuple[str, List[str]]:
        """
        Split PDF into page images and recreate each as filled HTML via VLM.

        Parallelism: batches of DOC_GEN_VLM_CONCURRENCY (default 3).
        Example: 9 pages → pages 1-3, then 4-6, then 7-9.
        """
        dpi = int(getattr(settings, "DOC_GEN_PDF_DPI", 200) or 200)
        batch_size = max(
            1,
            int(
                max_concurrent
                if max_concurrent is not None
                else getattr(settings, "DOC_GEN_VLM_CONCURRENCY", None)
                or getattr(settings, "MAX_CONCURRENT_PAGES", 3)
                or 3
            ),
        )
        page_sizes = self._pdf_page_sizes_inches(pdf_bytes, page_count)
        pages_html: List[Optional[str]] = [None] * page_count

        logger.info(
            "[VLM HTML] Page-parallel recreation for '%s' "
            "(%d pages, batch_size=%d, dpi=%d)",
            file_name,
            page_count,
            batch_size,
            dpi,
        )

        render_sem = asyncio.Semaphore(min(batch_size, 4))

        async def render_page(page_index: int) -> str:
            async with render_sem:
                return await asyncio.to_thread(
                    pdf_to_image, pdf_bytes, page_index, dpi
                )

        render_start = time.monotonic()
        images = await asyncio.gather(*(render_page(i) for i in range(page_count)))
        logger.info(
            "[VLM HTML] Split/rendered %d page image(s) in %.2fs",
            page_count,
            time.monotonic() - render_start,
        )

        async def process_page(page_index: int) -> None:
            page_number = page_index + 1
            width_in, height_in = page_sizes[page_index]
            vlm_start = time.monotonic()
            pages_html[page_index] = await self.generate_filled_html_page_vlm(
                page_image_b64=images[page_index],
                field_data=field_data,
                page_number=page_number,
                total_pages=page_count,
                page_width_in=width_in,
                page_height_in=height_in,
                file_name=file_name,
            )
            logger.info(
                "[VLM HTML] Page %d/%d completed in %.2fs (%d chars)",
                page_number,
                page_count,
                time.monotonic() - vlm_start,
                len(pages_html[page_index] or ""),
            )

        for batch_start in range(0, page_count, batch_size):
            batch_end = min(batch_start + batch_size, page_count)
            batch_pages = list(range(batch_start, batch_end))
            logger.info(
                "[VLM HTML] Starting parallel VLM batch pages %d-%d of %d",
                batch_start + 1,
                batch_end,
                page_count,
            )
            batch_start_ts = time.monotonic()
            await asyncio.gather(*(process_page(i) for i in batch_pages))
            logger.info(
                "[VLM HTML] Finished parallel VLM batch pages %d-%d in %.2fs",
                batch_start + 1,
                batch_end,
                time.monotonic() - batch_start_ts,
            )

        missing = [i + 1 for i, html in enumerate(pages_html) if not html]
        if missing:
            raise RuntimeError(f"VLM did not return HTML for page(s): {missing}")

        sections = [pages_html[i] for i in range(page_count)]  # type: ignore[misc]
        return self._assemble_html_document(sections, file_name), sections

    async def generate_filled_html_page_vlm(
        self,
        page_image_b64: str,
        field_data: str,
        page_number: int,
        total_pages: int,
        page_width_in: float = 8.5,
        page_height_in: float = 11.0,
        file_name: str = "court_form.pdf",
    ) -> str:
        """
        Reconstruct one PDF page:
        1) VLM → compact page specification (absolute geometry + fills)
        2) Deterministic absolute-position HTML renderer
        """
        page_width_pt = page_width_in * 72.0
        page_height_pt = page_height_in * 72.0
        text_prompt = format_llm_prompt(
            FILLED_HTML_PAGE_PROMPT,
            page_number=page_number,
            total_pages=total_pages,
            file_name=file_name,
            page_width_in=page_width_in,
            page_height_in=page_height_in,
            page_width_pt=page_width_pt,
            page_height_pt=page_height_pt,
            field_data=field_data,
        )

        content: List[Dict[str, Any]] = [{"type": "text", "text": text_prompt}]
        content.extend(self.bedrock.image_content_blocks([page_image_b64]))

        prompt_body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": self.doc_gen_max_tokens,
            "messages": [{"role": "user", "content": content}],
            "temperature": 0,
        }

        llm_output = await self.bedrock.invoke_prompt_with_timeout(
            prompt_body, timeout=self.doc_gen_timeout, stream=True
        )

        page_spec = extract_page_spec(llm_output)
        if page_spec:
            page_spec.setdefault("page_number", page_number)
            page_spec.setdefault("width_pt", page_width_pt)
            page_spec.setdefault("height_pt", page_height_pt)
            elements = page_spec.get("elements") or []
            logger.info(
                "[VLM HTML] Page %d page_spec elements=%d",
                page_number,
                len(elements) if isinstance(elements, list) else 0,
            )
            html = render_page_spec_to_html(page_spec)
            return self._sanitize_page_html(html)

        # Fallback: accept raw HTML if model ignored the JSON contract.
        logger.warning(
            "[VLM HTML] Page %d missing page_spec; falling back to raw HTML recovery",
            page_number,
        )
        html = self.bedrock.recover_html_from_llm_output(llm_output)
        if not html:
            raise RuntimeError(f"VLM did not return page_spec/HTML for page {page_number}")
        html = self.bedrock.strip_html_fences(html)
        html = self._normalize_page_section(html, page_number)
        return self._sanitize_page_html(html)

    async def classify_pdf_fillable_vlm(
        self,
        page_images_b64: List[str],
        acroform_field_names: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        if acroform_field_names:
            acro_hint = (
                "\nHeuristic hint from PDF metadata: AcroForm field names were detected: "
                + ", ".join(acroform_field_names[:40])
                + ".\nUse this as supporting evidence, but decide from the page images."
            )
        elif acroform_field_names is not None:
            acro_hint = (
                "\nHeuristic hint from PDF metadata: no AcroForm fields were detected."
            )
        else:
            acro_hint = ""
        text_prompt = format_llm_prompt(
            CLASSIFY_PDF_FILLABLE_PROMPT, acro_hint=acro_hint
        )

        content: List[Dict[str, Any]] = [{"type": "text", "text": text_prompt}]
        content.extend(self.bedrock.image_content_blocks(page_images_b64))

        prompt_body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": self.doc_gen_max_tokens,
            "messages": [{"role": "user", "content": content}],
            "temperature": 0.0,
        }

        result = await self.bedrock.invoke_prompt_with_timeout(
            prompt_body, timeout=self.doc_gen_timeout
        )

        classification = str(result.get("classification", "")).strip().lower()
        if classification in {"fillable", "non_fillable"}:
            return {
                "classification": classification,
                "confidence": float(result.get("confidence") or 0.0),
                "reason": str(result.get("reason") or ""),
            }

        raw = self.bedrock.extract_text_field(result, "output")
        if raw:
            lowered = raw.lower()
            if "non_fillable" in lowered or "non-fillable" in lowered:
                return {
                    "classification": "non_fillable",
                    "confidence": 0.5,
                    "reason": raw[:500],
                }
            if "fillable" in lowered:
                return {
                    "classification": "fillable",
                    "confidence": 0.5,
                    "reason": raw[:500],
                }

        raise RuntimeError(f"VLM returned unrecognized classification payload: {result}")

    async def generate_filled_ftl_vlm(
        self,
        page_images_b64: List[str],
        extracted_text: str,
        field_data: str,
    ) -> Dict[str, str]:
        text_prompt = format_llm_prompt(
            FILLED_FTL_VLM_PROMPT,
            extracted_text=extracted_text[:25000],
            field_data=field_data,
        )

        content: List[Dict[str, Any]] = [{"type": "text", "text": text_prompt}]
        content.extend(self.bedrock.image_content_blocks(page_images_b64[:2]))

        prompt_body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": self.doc_gen_max_tokens,
            "messages": [{"role": "user", "content": content}],
            "temperature": 0.1,
        }

        llm_output = await self.bedrock.invoke_prompt_with_timeout(
            prompt_body, timeout=self.doc_gen_timeout
        )
        ftl_content = self.bedrock.extract_text_field(llm_output, "ftl_content", "output")
        ftl_template = self.bedrock.extract_text_field(llm_output, "ftl_template") or ""
        if not ftl_content:
            raise RuntimeError("VLM did not return filled ftl_content")
        return {"ftl_template": ftl_template, "ftl_content": ftl_content}

    async def fill_ftl_chunk(
        self,
        page_text: str,
        field_data: str,
        page_start: int,
        page_end: int,
        page_image_b64: Optional[str] = None,
    ) -> str:
        text_prompt = format_llm_prompt(
            FILL_FTL_CHUNK_PROMPT,
            page_start=page_start,
            page_end=page_end,
            page_text=page_text[:20000],
            field_data=field_data,
        )
        content: List[Dict[str, Any]] = [{"type": "text", "text": text_prompt}]
        if page_image_b64:
            content.extend(self.bedrock.image_content_blocks([page_image_b64]))

        prompt_body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": self.doc_gen_max_tokens,
            "messages": [{"role": "user", "content": content}],
            "temperature": 0.1,
        }
        llm_output = await self.bedrock.invoke_prompt_with_timeout(
            prompt_body, timeout=self.doc_gen_timeout
        )
        ftl_content = self.bedrock.extract_text_field(llm_output, "ftl_content", "output")
        if not ftl_content:
            raise RuntimeError(
                f"LLM did not return ftl_content for pages {page_start}-{page_end}"
            )
        return ftl_content

    async def fill_ftl_pages_chunked(
        self,
        page_texts: List[str],
        field_data: str,
        pages_per_chunk: Optional[int] = None,
        page_images_b64: Optional[List[str]] = None,
    ) -> str:
        chunk_size = max(
            1,
            int(pages_per_chunk or getattr(settings, "DOC_GEN_PAGES_PER_CHUNK", 2) or 2),
        )
        filled_parts: List[str] = []
        images = page_images_b64 or []
        total = len(page_texts)
        for start in range(0, total, chunk_size):
            end = min(start + chunk_size, total)
            chunk_text = "\n\n".join(
                f"=== Page {i + 1} ===\n{page_texts[i]}"
                for i in range(start, end)
                if page_texts[i]
            )
            if not chunk_text.strip():
                continue
            image = images[start] if start < len(images) else None
            logger.info("Filling FTL chunk pages %s-%s of %s", start + 1, end, total)
            part = await self.fill_ftl_chunk(
                page_text=chunk_text,
                field_data=field_data,
                page_start=start + 1,
                page_end=end,
                page_image_b64=image,
            )
            filled_parts.append(f"<#-- Pages {start + 1}-{end} -->\n{part}")
        if not filled_parts:
            raise RuntimeError("No page text available to generate FTL")
        return "\n\n".join(filled_parts)

    async def map_fields_to_acroform(
        self,
        detected_fields: List[str],
        field_data: Dict[str, Any],
    ) -> Dict[str, str]:
        if not detected_fields or not field_data:
            return {}

        text_prompt = format_llm_prompt(
            MAP_FIELDS_TO_ACROFORM_PROMPT,
            detected_fields=json.dumps(detected_fields[:100], indent=2),
            field_data=json.dumps(field_data, indent=2),
        )

        prompt_body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": self.doc_gen_max_tokens,
            "messages": [{"role": "user", "content": text_prompt}],
            "temperature": 0.0,
            "response_format": {"type": "json_object"},
        }

        try:
            llm_output = await asyncio.wait_for(
                self.bedrock.invoke_prompt(prompt_body),
                timeout=settings.LLM_TIMEOUT,
            )
            if isinstance(llm_output, dict):
                valid_fields = set(detected_fields)
                mapping = {}
                for k, v in llm_output.items():
                    if k in valid_fields and v is not None:
                        mapping[k] = str(v)
                return mapping
        except Exception as exc:
            logger.warning("LLM field mapping failed: %s", exc)

        return {}

    async def fill_ftl_with_data(
        self,
        ftl_template: str,
        field_data: str,
    ) -> str:
        prompt = format_llm_prompt(
            FILL_FTL_WITH_DATA_PROMPT,
            ftl_template=ftl_template,
            field_data=field_data,
        )

        prompt_body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": self.doc_gen_max_tokens,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
        }

        try:
            llm_output = await self.bedrock.invoke_prompt_with_timeout(
                prompt_body, timeout=self.doc_gen_timeout
            )
        except RuntimeError:
            raise RuntimeError("LLM FTL fill timed out")

        ftl_content = self.bedrock.extract_text_field(llm_output, "ftl_content", "output")
        if ftl_content:
            return ftl_content
        raise RuntimeError("LLM did not return filled ftl_content")
