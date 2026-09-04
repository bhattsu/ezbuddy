import time
from typing import List
import logging

from app.adapters.file_extraction.extractors.base import BaseExtractor
from app.adapters.file_extraction.extractors.custom_extractor.pdf_extractor import AsyncPDFExtractor
from app.adapters.file_extraction.extractors.custom_extractor.excel_extractor import AsyncExcelExtractor
from app.adapters.file_extraction.extractors.custom_extractor.image_extractor import (
    AsyncImageExtractor, AsyncWordExtractor,
    AsyncCSVExtractor, AsyncTextExtractor
)
from app.api.schemas.document import (
    FileType, ExtractionRequest, ExtractionResponse,
    ProcessingStatus, ExtractionMethod, PageData
)
from app.utils.file_utils import get_page_numbers_to_process

logger = logging.getLogger(__name__)


class GOMLCustomExtractor(BaseExtractor):
    """GOML Custom extractor using local libraries"""

    def __init__(self):
        self.pdf_extractor = AsyncPDFExtractor()
        self.excel_extractor = AsyncExcelExtractor()
        self.image_extractor = AsyncImageExtractor()
        self.word_extractor = AsyncWordExtractor()
        self.csv_extractor = AsyncCSVExtractor()
        self.text_extractor = AsyncTextExtractor()

    async def extract(
        self,
        file_path: str,
        file_bytes: bytes,
        file_name: str,
        file_type: FileType,
        request: ExtractionRequest,
        document_id: str
    ) -> ExtractionResponse:
        """
        Extract data using local libraries (GOML custom extractor).
        Routes to appropriate file-type specific extractor.
        """
        start_time = time.time()

        try:
            logger.info(f"Starting GOML custom extraction: {file_type} - {file_name}")

            if file_type == FileType.PDF:
                pages_data = await self._process_pdf(
                    file_path, document_id, request
                )
            elif file_type == FileType.EXCEL:
                pages_data = await self._process_excel(
                    file_path, document_id, request
                )
            elif file_type == FileType.IMAGE:
                pages_data = await self._process_image(
                    file_path, document_id
                )
            elif file_type in [FileType.WORD, FileType.DOCX]:
                pages_data = await self._process_word(
                    file_path, document_id, request
                )
            elif file_type == FileType.CSV:
                pages_data = await self._process_csv(
                    file_bytes, document_id
                )
            elif file_type == FileType.TXT:
                pages_data = await self._process_text(file_bytes)
            else:
                raise ValueError(f"Unsupported file type: {file_type}")

            processing_time = time.time() - start_time

            response = ExtractionResponse(
                document_id=document_id,
                file_name=file_name,
                file_type=file_type,
                status=ProcessingStatus.COMPLETED,
                method=ExtractionMethod.CUSTOM,
                total_pages=len(pages_data),
                pages_processed=len([p for p in pages_data if p.text_content]),
                pages_data=pages_data,
                processing_time=processing_time,
                summary=self._generate_summary(pages_data)
            )

            logger.info(f"GOML custom extraction completed in {processing_time:.2f}s: "
                       f"{len(pages_data)} pages processed")

            return response

        except Exception as e:
            logger.error(f"GOML custom extraction failed: {str(e)}")
            processing_time = time.time() - start_time

            return ExtractionResponse(
                document_id=document_id,
                file_name=file_name,
                file_type=file_type,
                status=ProcessingStatus.FAILED,
                method=ExtractionMethod.CUSTOM,
                total_pages=0,
                pages_processed=0,
                pages_data=[],
                processing_time=processing_time,
                summary={},
                error_message=str(e)
            )

    async def _process_pdf(
        self,
        file_path: str,
        document_id: str,
        request: ExtractionRequest
    ) -> List[PageData]:
        total_pages = await self.pdf_extractor.get_page_count(file_path)
        page_range = None
        if request.page_range:
            page_range = (request.page_range.start, request.page_range.end)
        pages_to_process = get_page_numbers_to_process(
            total_pages=total_pages,
            pages=request.pages,
            page_range=page_range,
            process_all=request.process_all
        )
        logger.info(f"Processing {len(pages_to_process)} of {total_pages} PDF pages")
        pages_data = await self.pdf_extractor.extract_pages(
            file_path=file_path,
            document_id=document_id,
            pages_to_process=pages_to_process,
            extract_text=request.extract_text,
            extract_tables=request.extract_tables,
            extract_images=request.extract_images
        )
        return pages_data

    async def _process_excel(
        self,
        file_path: str,
        document_id: str,
        request: ExtractionRequest
    ) -> List[PageData]:
        return await self.excel_extractor.extract_sheets(
            file_path=file_path,
            document_id=document_id,
            sheets_to_process=request.sheets,
            extract_text=request.extract_text,
            extract_tables=request.extract_tables
        )

    async def _process_image(
        self,
        file_path: str,
        document_id: str
    ) -> List[PageData]:
        return await self.image_extractor.extract_from_image(
            file_path=file_path,
            document_id=document_id
        )

    async def _process_word(
        self,
        file_path: str,
        document_id: str,
        request: ExtractionRequest
    ) -> List[PageData]:
        return await self.word_extractor.extract_from_word(
            file_path=file_path,
            document_id=document_id,
            extract_text=request.extract_text,
            extract_tables=request.extract_tables
        )

    async def _process_csv(
        self,
        file_bytes: bytes,
        document_id: str
    ) -> List[PageData]:
        return await self.csv_extractor.extract_from_csv(
            file_bytes=file_bytes,
            document_id=document_id
        )

    async def _process_text(
        self,
        file_bytes: bytes
    ) -> List[PageData]:
        return await self.text_extractor.extract_from_text(file_bytes)

    def _generate_summary(self, pages_data: List[PageData]) -> dict:
        total_images = sum(len(page.images) for page in pages_data)
        total_tables = sum(len(page.tables) for page in pages_data)
        total_text_length = sum(len(page.text_content) for page in pages_data)
        total_forms = sum(len(page.forms) for page in pages_data)
        return {
            "total_pages": len(pages_data),
            "total_images": total_images,
            "total_tables": total_tables,
            "total_forms": total_forms,
            "total_text_length": total_text_length,
            "pages_with_images": len([p for p in pages_data if p.images]),
            "pages_with_tables": len([p for p in pages_data if p.tables]),
            "pages_with_forms": len([p for p in pages_data if p.forms])
        }
