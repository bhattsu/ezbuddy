"""
Extraction enhancement agent.

LLM calling code for IDP structured extraction enhancement.
Uses prompt builders from ``app.core.prompts``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

from app.adapters.llm.bedrock import Bedrock, get_bedrock
from app.api.schemas.document import ExtractionResponse
from app.config.settings import settings
from app.core.prompts.context import format_llm_prompt
from app.core.prompts.document_generation import ENHANCE_CUSTOM_PROMPT_WITH_FORMAT
from app.core.prompts.loader import get_prompt_builder
from app.core.prompts.templates import convert_to_json_schema

logger = logging.getLogger(__name__)


class ExtractionAgent:
    """Enhance IDP extraction results with LLM structured output."""

    def __init__(self, bedrock: Optional[Bedrock] = None):
        self.bedrock = bedrock or get_bedrock()

    async def enhance_extraction(
        self,
        extraction_response: ExtractionResponse,
        custom_prompt: str = None,
        custom_output_format: Optional[Dict[str, Any]] = None,
        prompt_version: Optional[str] = None,
    ) -> Dict[str, Any]:
        try:
            version_used = prompt_version or "default"
            logger.info(
                "Enhancing extraction with LLM: %s, prompt_version=%s",
                extraction_response.document_id,
                version_used,
            )

            prompt_body = self._build_prompt_request(
                extraction_response,
                custom_prompt,
                custom_output_format,
                prompt_version=prompt_version,
            )

            try:
                llm_output = await asyncio.wait_for(
                    self.bedrock.invoke_prompt(prompt_body),
                    timeout=settings.LLM_TIMEOUT,
                )
            except asyncio.TimeoutError:
                logger.error("LLM call timed out after %ss", settings.LLM_TIMEOUT)
                raise RuntimeError("LLM processing timed out")

            logger.info("LLM processing completed for %s", extraction_response.document_id)
            return llm_output

        except Exception as e:
            logger.error("LLM enhancement failed: %s", e)
            raise

    async def process_pages_batch(
        self,
        pages_data: List[Dict[str, Any]],
        custom_prompt: str = None,
        custom_output_format: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        tasks = [
            self._process_single_page(page_data, custom_prompt, custom_output_format)
            for page_data in pages_data
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        successful_results = []
        for idx, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error("Page %d LLM processing failed: %s", idx + 1, result)
            else:
                successful_results.append(result)
        return successful_results

    async def _process_single_page(
        self,
        page_data: Dict[str, Any],
        custom_prompt: Optional[str] = None,
        custom_output_format: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        text = page_data.get("text_content", "")
        forms = page_data.get("forms", [])
        tables = page_data.get("tables", [])

        if custom_prompt:
            prompt_body = {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 4096,
                "messages": [{"role": "user", "content": custom_prompt}],
                "temperature": 0.1,
                "top_p": 0.9,
            }
        else:
            builder = get_prompt_builder(None)
            prompt_body = builder(
                text=text,
                forms=forms,
                tables=tables,
                file_name=f"Page {page_data.get('page_number', 1)}",
                file_type="",
                pages_processed=1,
                total_pages=1,
                custom_output_format=custom_output_format,
            )

        return await self.bedrock.invoke_prompt(prompt_body)

    def _build_prompt_request(
        self,
        extraction_response: ExtractionResponse,
        custom_prompt: str = None,
        custom_output_format: Optional[Dict[str, Any]] = None,
        prompt_version: Optional[str] = None,
    ) -> Dict[str, Any]:
        if custom_prompt:
            prompt_body = {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 4096,
                "messages": [{"role": "user", "content": custom_prompt}],
                "temperature": 0.1,
                "top_p": 0.9,
            }
            if custom_output_format:
                try:
                    json_schema = convert_to_json_schema(custom_output_format)
                    prompt_body["response_format"] = {
                        "type": "json_schema",
                        "json_schema": {
                            "schema": json_schema,
                            "strict": True,
                            "name": "extraction_output",
                            "description": "Structured document extraction output",
                        },
                    }
                except Exception as e:
                    logger.warning("Failed to create JSON schema: %s", e)
                prompt_body["messages"][0]["content"] = format_llm_prompt(
                    ENHANCE_CUSTOM_PROMPT_WITH_FORMAT,
                    custom_prompt=custom_prompt,
                    custom_output_format=json.dumps(custom_output_format, indent=2),
                )
            return prompt_body

        all_text = []
        all_forms = []
        all_tables = []
        for page in extraction_response.pages_data:
            if page.text_content:
                all_text.append(f"=== Page {page.page_number} ===\n{page.text_content}")
            for form in page.forms:
                all_forms.append({"key": form.key, "value": form.value, "page": page.page_number})
            for table in page.tables:
                all_tables.append({
                    "page": page.page_number,
                    "table_id": table.table_id,
                    "headers": table.headers,
                    "rows": table.rows[:5],
                })

        full_text = "\n\n".join(all_text)
        builder = get_prompt_builder(prompt_version)
        return builder(
            text=full_text,
            forms=all_forms,
            tables=all_tables,
            file_name=extraction_response.file_name,
            file_type=extraction_response.file_type,
            pages_processed=extraction_response.pages_processed,
            total_pages=extraction_response.total_pages,
            custom_output_format=custom_output_format,
        )
