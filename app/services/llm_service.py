"""
LLM Service

Backward-compatible facade over agents in ``app.agents``.
Prompts live in ``app.core.prompts``; invoke logic lives in agents.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.adapters.llm.bedrock import Bedrock, get_bedrock
from app.agents.document_analysis.extraction_agent import ExtractionAgent
from app.agents.document_generation.document_generation_agent import DocumentGenerationAgent
from app.api.schemas.document import ExtractionResponse


class LLMService:
    """Service facade for LLM / VLM operations via agents."""

    def __init__(self, bedrock: Optional[Bedrock] = None):
        self._bedrock = bedrock or get_bedrock()
        self._doc_gen = DocumentGenerationAgent(self._bedrock)
        self._extraction = ExtractionAgent(self._bedrock)

    @property
    def doc_gen_timeout(self) -> int:
        return self._bedrock.doc_gen_timeout

    async def _invoke_with_timeout(
        self,
        prompt_body: Dict[str, Any],
        timeout: Optional[int] = None,
        *,
        stream: bool = False,
    ) -> Dict[str, Any]:
        return await self._bedrock.invoke_prompt_with_timeout(
            prompt_body, timeout=timeout, stream=stream
        )

    async def _invoke(self, prompt_body: Dict[str, Any]) -> Dict[str, Any]:
        return await self._bedrock.invoke_prompt(prompt_body)

    async def generate_filled_html_document_vlm(self, *args, **kwargs) -> str:
        return await self._doc_gen.generate_filled_html_document_vlm(*args, **kwargs)

    async def generate_filled_html_pages_vlm(self, *args, **kwargs):
        return await self._doc_gen.generate_filled_html_pages_vlm(*args, **kwargs)

    async def generate_filled_html_page_vlm(self, *args, **kwargs) -> str:
        return await self._doc_gen.generate_filled_html_page_vlm(*args, **kwargs)

    async def classify_pdf_fillable_vlm(self, *args, **kwargs) -> Dict[str, Any]:
        return await self._doc_gen.classify_pdf_fillable_vlm(*args, **kwargs)

    async def generate_filled_ftl_vlm(self, *args, **kwargs) -> Dict[str, str]:
        return await self._doc_gen.generate_filled_ftl_vlm(*args, **kwargs)

    async def fill_ftl_chunk(self, *args, **kwargs) -> str:
        return await self._doc_gen.fill_ftl_chunk(*args, **kwargs)

    async def fill_ftl_pages_chunked(self, *args, **kwargs) -> str:
        return await self._doc_gen.fill_ftl_pages_chunked(*args, **kwargs)

    async def map_fields_to_acroform(self, *args, **kwargs) -> Dict[str, str]:
        return await self._doc_gen.map_fields_to_acroform(*args, **kwargs)

    async def fill_ftl_with_data(self, *args, **kwargs) -> str:
        return await self._doc_gen.fill_ftl_with_data(*args, **kwargs)

    async def enhance_extraction(
        self,
        extraction_response: ExtractionResponse,
        custom_prompt: str = None,
        custom_output_format: Optional[Dict[str, Any]] = None,
        prompt_version: Optional[str] = None,
    ) -> Dict[str, Any]:
        return await self._extraction.enhance_extraction(
            extraction_response,
            custom_prompt=custom_prompt,
            custom_output_format=custom_output_format,
            prompt_version=prompt_version,
        )

    async def process_pages_batch(
        self,
        pages_data: List[Dict[str, Any]],
        custom_prompt: str = None,
        custom_output_format: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        return await self._extraction.process_pages_batch(
            pages_data,
            custom_prompt=custom_prompt,
            custom_output_format=custom_output_format,
        )
