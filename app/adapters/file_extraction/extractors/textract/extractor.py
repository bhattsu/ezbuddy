import asyncio
import time
import os
from typing import Dict, Any, List, Optional
import logging

from app.adapters.file_extraction.extractors.base import BaseExtractor
from app.adapters.aws_clients import aws_clients
from app.utils.s3_utils import S3Manager
from app.config.settings import settings
from app.api.schemas.document import (
    FileType, ExtractionRequest, ExtractionResponse,
    ProcessingStatus, ExtractionMethod, PageData, TableInfo, FormField
)

logger = logging.getLogger(__name__)


class AWSTextractExtractor(BaseExtractor):
    """AWS Textract-based document extractor"""

    def __init__(self):
        self.textract = aws_clients.get_textract()
        self.s3_manager = S3Manager()

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
        Extract data using AWS Textract.

        For images, uses synchronous API.
        For PDFs and other documents, uses asynchronous API with S3.
        """
        start_time = time.time()
        s3_key = None

        try:
            if file_type == FileType.IMAGE:
                result = await self._process_image(
                    image_bytes=file_bytes,
                    document_id=document_id
                )
                pages_data = result['pages_data']
                processing_time = time.time() - start_time

                return ExtractionResponse(
                    document_id=document_id,
                    file_name=file_name,
                    file_type=file_type,
                    status=ProcessingStatus.COMPLETED,
                    method=ExtractionMethod.TEXTRACT,
                    total_pages=len(pages_data),
                    pages_processed=len(pages_data),
                    pages_data=pages_data,
                    processing_time=processing_time,
                    summary=self._generate_textract_summary(pages_data)
                )
            else:
                if not os.path.exists(file_path):
                    raise ValueError(f"File not found: {file_path}")

                file_size = os.path.getsize(file_path)
                if file_size == 0:
                    raise ValueError(f"File is empty: {file_path}")

                with open(file_path, 'rb') as f:
                    file_content = f.read()
                    if not file_content:
                        raise ValueError(f"Failed to read file content from {file_path}")
                    if file_content.startswith(b'%PDF-'):
                        logger.info(f"Valid PDF file detected: {file_path}")
                    else:
                        logger.warning(f"File may not be a valid PDF: {file_path}")

                logger.info(f"Processing file: {file_path} (size: {file_size} bytes)")

                s3_key = await self.s3_manager.upload_file(
                    file_path=file_path,
                    document_id=document_id,
                    file_name=file_name
                )

                try:
                    head_response = await asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda: self.s3_manager.s3_client.head_object(
                            Bucket=settings.BUCKET_NAME,
                            Key=s3_key
                        )
                    )
                    logger.info(f"File uploaded successfully. Size in S3: {head_response['ContentLength']} bytes")
                except Exception as e:
                    logger.error(f"Failed to verify S3 upload: {str(e)}")
                    raise ValueError(f"File upload verification failed: {str(e)}")

                job_id = await self._start_document_analysis(s3_key)
                response = await self._wait_for_completion(job_id)

                pages_to_process = None
                if request.pages:
                    pages_to_process = [p - 1 for p in request.pages]
                elif request.page_range:
                    start = request.page_range.start - 1
                    end = request.page_range.end or 9999
                    pages_to_process = list(range(start, end))

                parsed = self._parse_textract_response(
                    response,
                    pages_to_process=pages_to_process
                )

                processing_time = time.time() - start_time

                return ExtractionResponse(
                    document_id=document_id,
                    file_name=file_name,
                    file_type=file_type,
                    status=ProcessingStatus.COMPLETED,
                    method=ExtractionMethod.TEXTRACT,
                    total_pages=len(parsed),
                    pages_processed=len(parsed),
                    pages_data=parsed,
                    processing_time=processing_time,
                    summary=self._generate_textract_summary(parsed),
                    s3_key=s3_key,
                    textract_job_id=job_id
                )

        except asyncio.TimeoutError:
            logger.error(f"Textract job timed out: {document_id}")
            processing_time = time.time() - start_time
            return ExtractionResponse(
                document_id=document_id,
                file_name=file_name,
                file_type=file_type,
                status=ProcessingStatus.FAILED,
                method=ExtractionMethod.TEXTRACT,
                total_pages=0,
                pages_processed=0,
                pages_data=[],
                processing_time=processing_time,
                summary={},
                error_message="Textract processing timed out"
            )

        except Exception as e:
            logger.error(f"Textract processing failed: {str(e)}")
            processing_time = time.time() - start_time
            return ExtractionResponse(
                document_id=document_id,
                file_name=file_name,
                file_type=file_type,
                status=ProcessingStatus.FAILED,
                method=ExtractionMethod.TEXTRACT,
                total_pages=0,
                pages_processed=0,
                pages_data=[],
                processing_time=processing_time,
                summary={},
                error_message=str(e)
            )

        finally:
            if s3_key and settings.S3_TEMP_EXPIRY_DAYS == 0:
                try:
                    await self.s3_manager.delete_file(s3_key)
                except Exception:
                    pass

    async def extract_from_bytes(
        self,
        file_bytes: bytes,
        file_name: str,
        file_type: FileType,
        document_id: str,
    ) -> ExtractionResponse:
        """Run Textract on local bytes. PDFs are rendered page-by-page; no S3."""
        start_time = time.time()
        if file_type == FileType.IMAGE:
            result = await self._process_image(
                image_bytes=file_bytes, document_id=document_id
            )
            pages_data = result["pages_data"]
        elif file_type == FileType.PDF:
            pages_data = await self._process_pdf_bytes(file_bytes, document_id)
        else:
            raise ValueError(f"Local Textract extraction does not support {file_type}")

        processing_time = time.time() - start_time
        return ExtractionResponse(
            document_id=document_id,
            file_name=file_name,
            file_type=file_type,
            status=ProcessingStatus.COMPLETED,
            method=ExtractionMethod.TEXTRACT,
            total_pages=len(pages_data),
            pages_processed=len(pages_data),
            pages_data=pages_data,
            processing_time=processing_time,
            summary=self._generate_textract_summary(pages_data),
        )

    async def _process_pdf_bytes(
        self, pdf_bytes: bytes, document_id: str
    ) -> List[PageData]:
        from app.adapters.file_extraction.extractors.textract.utils import (
            pdf_page_png_bytes,
        )
        import pymupdf

        if not pdf_bytes.startswith(b"%PDF-"):
            raise ValueError("Invalid PDF format - missing PDF signature")

        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        try:
            page_count = doc.page_count
        finally:
            doc.close()
        if page_count == 0:
            return []

        pages_data: List[PageData] = []
        logger.info(
            "Extracting %s-page PDF with Textract sync (no S3 upload)", page_count
        )
        for index in range(page_count):
            image_bytes = pdf_page_png_bytes(pdf_bytes, index, dpi=150)
            result = await self._process_image(image_bytes, document_id)
            for page in result["pages_data"]:
                page.page_number = index + 1
                pages_data.append(page)
        return pages_data

    async def _start_document_analysis(self, s3_key: str) -> str:
        loop = asyncio.get_event_loop()
        try:
            response = await loop.run_in_executor(
                None,
                lambda: self.textract.start_document_analysis(
                    DocumentLocation={
                        'S3Object': {
                            'Bucket': settings.BUCKET_NAME,
                            'Name': s3_key
                        }
                    },
                    FeatureTypes=["FORMS", "TABLES"]
                )
            )
            job_id = response['JobId']
            logger.info(f"Textract job started: {job_id}")
            return job_id
        except Exception as e:
            logger.error(f"Failed to start Textract job: {str(e)}")
            raise RuntimeError(f"Textract job start failed: {str(e)}")

    async def _wait_for_completion(
        self,
        job_id: str,
        max_wait: int = None
    ) -> Dict[str, Any]:
        if max_wait is None:
            max_wait = settings.TEXTRACT_TIMEOUT
        start_time = time.time()
        loop = asyncio.get_event_loop()
        while True:
            if time.time() - start_time > max_wait:
                raise asyncio.TimeoutError(
                    f"Textract job {job_id} exceeded {max_wait}s timeout"
                )
            try:
                response = await loop.run_in_executor(
                    None,
                    lambda: self.textract.get_document_analysis(JobId=job_id)
                )
            except Exception as e:
                logger.error(f"Failed to get job status: {str(e)}")
                raise
            status = response['JobStatus']
            if status == 'SUCCEEDED':
                logger.info(f"Textract job completed: {job_id}")
                all_blocks = response.get('Blocks', [])
                next_token = response.get('NextToken')
                while next_token:
                    next_response = await loop.run_in_executor(
                        None,
                        lambda: self.textract.get_document_analysis(
                            JobId=job_id,
                            NextToken=next_token
                        )
                    )
                    all_blocks.extend(next_response.get('Blocks', []))
                    next_token = next_response.get('NextToken')
                response['Blocks'] = all_blocks
                return response
            elif status == 'FAILED':
                error_msg = response.get('StatusMessage', 'Unknown error')
                logger.error(f"Textract job failed: {job_id} - {error_msg}")
                raise RuntimeError(f"Textract job failed: {error_msg}")
            elif status == 'IN_PROGRESS':
                logger.debug(f"Textract job in progress: {job_id}")
                await asyncio.sleep(5)
            else:
                logger.warning(f"Unknown Textract status: {status}")
                await asyncio.sleep(5)

    def _parse_textract_response(
        self,
        response: Dict[str, Any],
        pages_to_process: Optional[List[int]] = None
    ) -> List[PageData]:
        blocks = response.get('Blocks', [])
        if not blocks:
            logger.warning("No blocks found in Textract response")
            return []
        pages_dict = {}
        block_map = {block['Id']: block for block in blocks}
        for block in blocks:
            page_num = block.get('Page', 1)
            if pages_to_process and (page_num - 1) not in pages_to_process:
                continue
            if page_num not in pages_dict:
                pages_dict[page_num] = {
                    'text_lines': [],
                    'tables': [],
                    'forms': []
                }
            block_type = block.get('BlockType')
            if block_type == 'LINE':
                pages_dict[page_num]['text_lines'].append(block.get('Text', ''))
            elif block_type == 'TABLE':
                table = self._parse_table_block(block, block_map)
                pages_dict[page_num]['tables'].append(table)
            elif block_type == 'KEY_VALUE_SET':
                if block.get('EntityTypes') == ['KEY']:
                    form_field = self._parse_form_field(block, block_map)
                    if form_field:
                        pages_dict[page_num]['forms'].append(form_field)
        pages_data = []
        for page_num in sorted(pages_dict.keys()):
            page_info = pages_dict[page_num]
            page_data = PageData(
                page_number=page_num,
                text_content='\n'.join(page_info['text_lines']),
                tables=page_info['tables'],
                forms=page_info['forms']
            )
            pages_data.append(page_data)
        return pages_data

    def _parse_table_block(
        self,
        table_block: Dict,
        block_map: Dict
    ) -> TableInfo:
        rows = []
        headers = []
        if 'Relationships' not in table_block:
            return TableInfo(
                table_id=table_block['Id'],
                page_number=table_block.get('Page', 1),
                headers=[],
                rows=[]
            )
        cells = []
        for relationship in table_block.get('Relationships', []):
            if relationship['Type'] == 'CHILD':
                for cell_id in relationship['Ids']:
                    if cell_id in block_map:
                        cell_block = block_map[cell_id]
                        if cell_block.get('BlockType') == 'CELL':
                            cells.append(cell_block)
        rows_dict = {}
        for cell in cells:
            row_index = cell.get('RowIndex', 1)
            col_index = cell.get('ColumnIndex', 1)
            if row_index not in rows_dict:
                rows_dict[row_index] = {}
            cell_text = self._get_cell_text(cell, block_map)
            rows_dict[row_index][col_index] = cell_text
        if rows_dict:
            max_cols = max(max(row.keys()) for row in rows_dict.values())
            for row_idx in sorted(rows_dict.keys()):
                row_data = [
                    rows_dict[row_idx].get(col_idx, '')
                    for col_idx in range(1, max_cols + 1)
                ]
                if row_idx == 1:
                    headers = row_data
                else:
                    rows.append(row_data)
        return TableInfo(
            table_id=table_block['Id'],
            page_number=table_block.get('Page', 1),
            headers=headers,
            rows=rows,
            confidence=table_block.get('Confidence')
        )

    def _get_cell_text(self, cell_block: Dict, block_map: Dict) -> str:
        text_parts = []
        for relationship in cell_block.get('Relationships', []):
            if relationship['Type'] == 'CHILD':
                for child_id in relationship['Ids']:
                    if child_id in block_map:
                        child_block = block_map[child_id]
                        if child_block.get('BlockType') == 'WORD':
                            text_parts.append(child_block.get('Text', ''))
        return ' '.join(text_parts)

    def _parse_form_field(
        self,
        key_block: Dict,
        block_map: Dict
    ) -> Optional[FormField]:
        key_text = self._get_kv_text(key_block, block_map)
        value_text = ""
        for relationship in key_block.get('Relationships', []):
            if relationship['Type'] == 'VALUE':
                for value_id in relationship['Ids']:
                    if value_id in block_map:
                        value_block = block_map[value_id]
                        value_text = self._get_kv_text(value_block, block_map)
                        break
        if not key_text:
            return None
        return FormField(
            key=key_text,
            value=value_text,
            confidence=key_block.get('Confidence')
        )

    def _get_kv_text(self, kv_block: Dict, block_map: Dict) -> str:
        text_parts = []
        for relationship in kv_block.get('Relationships', []):
            if relationship['Type'] == 'CHILD':
                for child_id in relationship['Ids']:
                    if child_id in block_map:
                        child_block = block_map[child_id]
                        if child_block.get('BlockType') == 'WORD':
                            text_parts.append(child_block.get('Text', ''))
        return ' '.join(text_parts)

    async def _process_image(
        self,
        image_bytes: bytes,
        document_id: str
    ) -> Dict[str, Any]:
        loop = asyncio.get_event_loop()
        try:
            analysis_response = await loop.run_in_executor(
                None,
                lambda: self.textract.analyze_document(
                    Document={'Bytes': image_bytes},
                    FeatureTypes=["FORMS", "TABLES"]
                )
            )
            pages_data = self._parse_textract_response(analysis_response)
            return {
                "document_id": document_id,
                "status": "completed",
                "pages_data": pages_data
            }
        except Exception as e:
            logger.error(f"Image processing failed: {str(e)}")
            raise RuntimeError(f"Image processing failed: {str(e)}")

    def _generate_textract_summary(self, pages_data: List[PageData]) -> dict:
        total_tables = sum(len(page.tables) for page in pages_data)
        total_forms = sum(len(page.forms) for page in pages_data)
        total_text_length = sum(len(page.text_content) for page in pages_data)
        return {
            "total_pages": len(pages_data),
            "total_tables": total_tables,
            "total_forms": total_forms,
            "total_text_length": total_text_length,
            "pages_with_tables": len([p for p in pages_data if p.tables]),
            "pages_with_forms": len([p for p in pages_data if p.forms])
        }
