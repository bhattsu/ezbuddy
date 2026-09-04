import asyncio
from typing import List, Dict, Any, Tuple, Optional
import logging
import pymupdf
import pdfplumber
from concurrent.futures import ThreadPoolExecutor
import os
import io
import traceback

from app.api.schemas.document import PageData, TableInfo, ImageInfo
from app.config.settings import settings

logger = logging.getLogger(__name__)

class AsyncPDFExtractor:
    """Async PDF extraction with pdfplumber and PyMuPDF"""

    def __init__(self):
        self.executor = ThreadPoolExecutor(max_workers=settings.MAX_WORKERS)

    async def validate_pdf(self, file_path: str) -> Tuple[bool, str]:
        """
        Validate PDF file using multiple approaches with repair attempts
        Returns (is_valid, error_message)
        """
        if not os.path.exists(file_path):
            return False, "File does not exist"
        if os.path.getsize(file_path) == 0:
            return False, "File is empty"

        with open(file_path, 'rb') as f:
            pdf_bytes = f.read()

        if not pdf_bytes.startswith(b'%PDF'):
            return False, "Not a valid PDF file - Missing PDF signature"

        try:
            doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")

            if doc.is_damaged:
                doc.close()
                return False, "PDF is damaged - Document structure is corrupted"

            try:
                _ = doc.page_count
                for i in range(min(1, len(doc))):
                    _ = doc[i]
            except Exception as struct_e:
                doc.close()
                if "No /Root object" in str(struct_e):
                    return False, "PDF structure error: Missing root object - The PDF file is corrupted"
                return False, f"PDF structure error: {str(struct_e)}"

            doc.close()
            return True, ""

        except Exception as e:
            fitz_error = str(e)
            logger.warning(f"PyMuPDF validation failed: {fitz_error}")

            try:
                with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                    _ = pdf.metadata
                    _ = len(pdf.pages)
                    if len(pdf.pages) > 0:
                        _ = pdf.pages[0].crop((0, 0, 1, 1))
                return True, ""
            except Exception as e2:
                plumber_error = str(e2)
                logger.error(f"PDF validation failed with both engines. Errors: PyMuPDF: {fitz_error}, pdfplumber: {plumber_error}")
                if "No /Root object" in fitz_error or "No /Root object" in plumber_error:
                    return False, "PDF is corrupted: Missing root object - The file may need repair"
                return False, f"Invalid PDF structure - PyMuPDF: {fitz_error}, pdfplumber: {plumber_error}"

    def _get_pdf_info(self, file_path: str) -> Dict[str, Any]:
        """Get PDF metadata and structure information"""
        try:
            doc = pymupdf.open(file_path)
            info = {
                "page_count": doc.page_count,
                "metadata": doc.metadata,
                "has_root": bool(doc.pdf_catalog),
                "is_encrypted": doc.is_encrypted,
                "needs_pass": doc.needs_pass,
                "is_repaired": doc.is_repaired
            }
            doc.close()
            return info
        except Exception as e:
            logger.error(f"Failed to get PDF info: {str(e)}")
            return {}

    async def extract_pages(
        self,
        file_path: str,
        document_id: str,
        pages_to_process: List[int],
        extract_text: bool = True,
        extract_tables: bool = True,
        extract_images: bool = True
    ) -> List[PageData]:
        """Extract data from specified PDF pages concurrently"""
        is_valid, error_msg = await self.validate_pdf(file_path)
        if not is_valid:
            raise ValueError(f"Invalid PDF: {error_msg}")

        pdf_info = self._get_pdf_info(file_path)
        logger.info(f"Processing PDF: {pdf_info}")

        loop = asyncio.get_event_loop()
        batch_size = settings.MAX_CONCURRENT_PAGES
        all_pages_data = []

        for i in range(0, len(pages_to_process), batch_size):
            batch = pages_to_process[i:i + batch_size]
            tasks = [
                loop.run_in_executor(
                    self.executor,
                    self._extract_single_page,
                    file_path,
                    document_id,
                    page_num,
                    extract_text,
                    extract_tables,
                    extract_images
                )
                for page_num in batch
            ]
            batch_results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in batch_results:
                if isinstance(result, Exception):
                    logger.error(f"Page extraction failed: {str(result)}")
                    continue
                if result:
                    all_pages_data.append(result)

        return sorted(all_pages_data, key=lambda x: x.page_number)

    def _extract_single_page(
        self,
        file_path: str,
        document_id: str,
        page_num: int,
        extract_text: bool,
        extract_tables: bool,
        extract_images: bool
    ) -> PageData:
        """Extract data from a single PDF page (runs in executor)"""
        page_data = PageData(page_number=page_num + 1)

        try:
            if extract_text or extract_tables:
                with pdfplumber.open(file_path) as pdf:
                    if page_num < len(pdf.pages):
                        page = pdf.pages[page_num]
                        if extract_text:
                            text = page.extract_text() or ""
                            page_data.text_content = text
                        if extract_tables:
                            tables = page.extract_tables()
                            for table_idx, table in enumerate(tables):
                                if table and len(table) > 0:
                                    headers = [str(cell) if cell is not None else '' for cell in (table[0] if table else [])]
                                    rows = []
                                    if len(table) > 1:
                                        for row in table[1:]:
                                            clean_row = [str(cell) if cell is not None else '' for cell in row]
                                            rows.append(clean_row)
                                    table_info = TableInfo(
                                        table_id=f"{document_id}_p{page_num + 1}_t{table_idx}",
                                        page_number=page_num + 1,
                                        headers=headers,
                                        rows=rows,
                                        confidence=1.0
                                    )
                                    page_data.tables.append(table_info)

            if extract_images:
                doc = pymupdf.open(file_path)
                if page_num < len(doc):
                    page = doc[page_num]
                    image_list = page.get_images()
                    for img_idx, img in enumerate(image_list):
                        try:
                            xref = img[0]
                            base_image = doc.extract_image(xref)
                            image_info = ImageInfo(
                                image_id=f"{document_id}_p{page_num + 1}_img{img_idx}",
                                file_path="",
                                page_number=page_num + 1,
                                format=base_image.get("ext", "unknown")
                            )
                            page_data.images.append(image_info)
                        except Exception as img_error:
                            logger.warning(f"Failed to extract image: {str(img_error)}")
                doc.close()

            logger.info(f"Extracted page {page_num + 1}: {len(page_data.text_content)} chars, "
                       f"{len(page_data.tables)} tables, {len(page_data.images)} images")

        except Exception as e:
            logger.error(f"Failed to extract page {page_num + 1}: {str(e)}")
            page_data.metadata['error'] = str(e)

        return page_data

    async def get_page_count(self, file_path: str) -> int:
        """Get total page count asynchronously"""
        loop = asyncio.get_event_loop()
        def _count_pages():
            with pdfplumber.open(file_path) as pdf:
                return len(pdf.pages)
        try:
            count = await loop.run_in_executor(self.executor, _count_pages)
            logger.info(f"PDF has {count} pages")
            return count
        except Exception as e:
            logger.error(f"Failed to get page count: {str(e)}")
            raise RuntimeError(f"Failed to read PDF: {str(e)}")
