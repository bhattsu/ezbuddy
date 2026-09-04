import uuid
import logging
from typing import Optional

from app.api.schemas.document import (
    FileType, ExtractionRequest, ExtractionResponse,
    ExtractionMethod
)
from app.adapters.file_extraction.factory import ExtractorFactory
from app.services.llm_service import LLMService

logger = logging.getLogger(__name__)


class ExtractionService:
    """
    Main orchestrator for document extraction
    Uses configured extractor (from .env) and optionally enhances with LLM
    """

    def __init__(self):
        self.extractor = ExtractorFactory.create()
        self.llm_service = LLMService()
        logger.info(f"ExtractionService initialized with extractor: {self.extractor.get_extractor_name()}")

    async def process_document(
        self,
        file_path: str,
        file_bytes: bytes,
        file_name: str,
        file_type: FileType,
        request: ExtractionRequest,
        document_id: Optional[str] = None
    ) -> ExtractionResponse:
        """
        Main entry point for document processing
        Uses configured extractor and optionally enhances with LLM
        """

        if document_id is None:
            document_id = str(uuid.uuid4())

        logger.info(f"Processing document {document_id}: {file_name} ({file_type}) "
                   f"with extractor: {self.extractor.get_extractor_name()}")

        try:
            extraction_response = await self.extractor.extract(
                file_path=file_path,
                file_bytes=file_bytes,
                file_name=file_name,
                file_type=file_type,
                request=request,
                document_id=document_id
            )

            if request.process_with_llm and extraction_response.pages_data:
                try:
                    logger.info(f"Enhancing extraction with LLM: {document_id}")
                    llm_output = await self.llm_service.enhance_extraction(
                        extraction_response=extraction_response
                    )
                    extraction_response.llm_output = llm_output
                    logger.info(f"LLM enhancement completed: {document_id}")

                except Exception as llm_error:
                    logger.error(f"LLM enhancement failed: {str(llm_error)}")
                    extraction_response.error_message = f"LLM processing failed: {str(llm_error)}"

            return extraction_response

        except Exception as e:
            logger.error(f"Document processing failed: {str(e)}")
            raise
