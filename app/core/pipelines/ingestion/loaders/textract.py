"""
AWS Textract Document Loader

Loader for extracting text from images, PDFs, and scanned documents using AWS Textract.

Supports:
- Images: PNG, JPEG, TIFF
- PDFs: Single and multi-page
- Scanned documents with OCR
- Form field extraction
- Table extraction

Usage:
    from app.core.pipelines.ingestion.loaders.textract import TextractLoader
    from app.core.pipelines.ingestion.pipeline import IngestionPipeline, IngestionConfig
    
    # Initialize loader
    loader = TextractLoader(
        extract_tables=True,
        extract_forms=True
    )
    
    # Load a scanned PDF or image
    documents = await loader.load("path/to/document.pdf")
    
    # Or use in pipeline
    pipeline = IngestionPipeline(vector_store, embedder, chunker, loader)
    result = await pipeline.ingest_file("document.pdf", config)

Requirements:
- AWS credentials configured (via boto3)
- IAM permissions: textract:DetectDocumentText, textract:AnalyzeDocument
- For async PDF processing: textract:StartDocumentTextDetection, S3 access

Note: Textract has page limits for sync operations. Use async mode for large PDFs.
"""

import logging
import boto3
import asyncio
from typing import List, Optional
from pathlib import Path
import io

from app.core.pipelines.ingestion.loaders.base import DocumentLoader, Document
from app.config.settings import settings

logger = logging.getLogger(__name__)


class TextractLoader(DocumentLoader):
    """
    AWS Textract loader for OCR and document analysis.
    
    Supports:
    - Images (PNG, JPEG, TIFF)
    - PDFs (single and multi-page)
    - Scanned documents
    - Forms and tables extraction
    """

    def __init__(
        self,
        region_name: Optional[str] = None,
        extract_tables: bool = True,
        extract_forms: bool = True,
        extract_layout: bool = False
    ):
        """
        Initialize the Textract loader.
        
        Args:
            region_name: AWS region for Textract (defaults to settings.AWS_REGION).
            extract_tables: Whether to extract tables from documents.
            extract_forms: Whether to extract form fields.
            extract_layout: Whether to extract layout information.
        """
        self.region_name = region_name or settings.AWS_REGION
        self.extract_tables = extract_tables
        self.extract_forms = extract_forms
        self.extract_layout = extract_layout
        
        try:
            self.textract_client = boto3.client(
                "textract",
                region_name=self.region_name
            )
            logger.info(f"Textract client initialized for region: {self.region_name}")
        except Exception as e:
            logger.error(f"Failed to initialize Textract client: {e}")
            raise

    def _detect_document_type(self, file_path: Path) -> str:
        """
        Detect the document type for Textract.
        
        Args:
            file_path: Path to the file.
            
        Returns:
            Document type: 'image' or 'pdf'.
        """
        ext = file_path.suffix.lower()
        image_extensions = {".png", ".jpg", ".jpeg", ".tiff", ".tif"}
        
        logger.debug(f"Detecting document type for: {file_path} (Extension: {ext})")  # log added
        if ext == ".pdf":
            logger.debug("Document detected as PDF.")  # log added
            return "pdf"
        elif ext in image_extensions:
            logger.debug("Document detected as image.")  # log added
            return "image"
        else:
            logger.error(f"Unsupported file type for Textract: {ext}")  # log added
            raise ValueError(f"Unsupported file type for Textract: {ext}")

    def _extract_text_sync(self, file_path: Path) -> str:
        """
        Extract text synchronously using Textract.
        
        Note: Textract's sync APIs (analyze_document, detect_document_text) only support
        PNG and JPEG images, NOT PDFs. For PDFs, use async mode with S3 or use PDFLoader.
        
        Args:
            file_path: Path to the file.
            
        Returns:
            Extracted text.
            
        Raises:
            ValueError: If file is a PDF (not supported in sync mode).
        """
        logger.info(f"Starting synchronous Textract extraction for: {file_path}")
        doc_type = self._detect_document_type(file_path)
        
        # Textract sync APIs do NOT support PDFs - only PNG/JPEG images
        if doc_type == "pdf":
            error_msg = (
                f"Textract sync mode does not support PDF files. "
                f"PDFs require async processing with S3, or use PDFLoader instead. "
                f"File: {file_path}"
            )
            logger.error(error_msg)
            raise ValueError(error_msg)
        
        with open(file_path, "rb") as file:
            file_bytes = file.read()
        logger.debug(f"Loaded {len(file_bytes)} bytes from {file_path}")
        
        # For images, use detect_document_text or analyze_document
        if self.extract_tables or self.extract_forms:
            logger.info(f"Calling analyze_document (sync) for image: {file_path}")
            response = self.textract_client.analyze_document(
                Document={"Bytes": file_bytes},
                FeatureTypes=self._get_feature_types()
            )
        else:
            logger.info(f"Calling detect_document_text (sync) for image: {file_path}")
            response = self.textract_client.detect_document_text(
                Document={"Bytes": file_bytes}
            )
        
        logger.debug("Textract response received (sync).")
        parsed = self._parse_textract_response(response)
        logger.info(f"Finished synchronous Textract extraction for: {file_path} (length={len(parsed)})")
        return parsed

    def _extract_text_async(self, file_path: Path, s3_bucket: str, s3_key: str) -> str:
        """
        Extract text asynchronously for large PDFs.
        
        Args:
            file_path: Local path to the file.
            s3_bucket: S3 bucket name.
            s3_key: S3 object key.
            
        Returns:
            Extracted text.
        """
        logger.info(f"Starting async Textract extraction for S3 object: {s3_bucket}/{s3_key} (local file: {file_path})")  # log added
        # Start async job
        response = self.textract_client.start_document_text_detection(
            DocumentLocation={
                "S3Object": {
                    "Bucket": s3_bucket,
                    "Name": s3_key
                }
            }
        )
        
        job_id = response["JobId"]
        logger.info(f"Started Textract async job: {job_id} for file: {file_path}")  # log added
        
        # Poll for completion
        import time
        poll_count = 0  # log counter
        while True:
            response = self.textract_client.get_document_text_detection(JobId=job_id)
            status = response["JobStatus"]
            
            logger.debug(f"Polling Textract job {job_id} (poll_count={poll_count}): status={status}")  # log added
            if status == "SUCCEEDED":
                logger.info(f"Textract async job {job_id} succeeded after {poll_count} polls.")  # log added
                break
            elif status == "FAILED":
                logger.error(f"Textract async job {job_id} failed: {response.get('StatusMessage')}")  # log added
                raise Exception(f"Textract job failed: {response.get('StatusMessage')}")
            time.sleep(2)
            poll_count += 1
        
        # Get all pages
        text_parts = []
        next_token = None
        page_count = 0  # log page counter
        while True:
            if next_token:
                logger.debug(f"Fetching next async Textract page for job {job_id} with NextToken {next_token}")  # log added
                response = self.textract_client.get_document_text_detection(
                    JobId=job_id,
                    NextToken=next_token
                )
            else:
                logger.debug(f"Fetching first async Textract page for job {job_id}")  # log added
                response = self.textract_client.get_document_text_detection(JobId=job_id)
            
            text_parts.append(self._parse_textract_response(response))
            page_count += 1
            next_token = response.get("NextToken")
            if not next_token:
                logger.info(f"All async Textract pages retrieved for job {job_id} (pages={page_count})")  # log added
                break
        
        joined = "\n\n".join(text_parts)
        logger.info(f"Async Textract extraction complete for: {file_path} (total chars={len(joined)})")  # log added
        return joined

    def _get_feature_types(self) -> List[str]:
        """Get feature types based on configuration."""
        features = []
        if self.extract_tables:
            features.append("TABLES")
        if self.extract_forms:
            features.append("FORMS")
        if self.extract_layout:
            features.append("LAYOUT")
        logger.debug(f"Textract feature types: {features if features else ['TABLES', 'FORMS']}")  # log added
        return features if features else ["TABLES", "FORMS"]

    def _parse_textract_response(self, response: dict) -> str:
        """
        Parse Textract response into plain text.
        
        Args:
            response: Textract API response.
            
        Returns:
            Extracted text.
        """
        logger.debug("Parsing Textract response.")  # log added
        text_parts = []
        
        # Extract plain text blocks
        blocks = response.get("Blocks", [])
        logger.debug(f"Found {len(blocks)} blocks in Textract response.")  # log added
        
        # Get text blocks
        text_blocks = [b for b in blocks if b.get("BlockType") == "LINE"]
        logger.debug(f"Found {len(text_blocks)} LINE blocks in Textract response.")  # log added
        
        # Sort by geometry if available
        if text_blocks and "Geometry" in text_blocks[0]:
            text_blocks.sort(
                key=lambda b: (
                    b["Geometry"]["BoundingBox"]["Top"],
                    b["Geometry"]["BoundingBox"]["Left"]
                )
            )
        
        # Extract text
        for block in text_blocks:
            text = block.get("Text", "")
            if text:
                text_parts.append(text)
        
        # Extract tables if enabled
        if self.extract_tables:
            logger.debug("Extracting tables from Textract response.")  # log added
            tables = self._extract_tables(blocks)
            if tables:
                logger.info(f"Extracted {len(tables)} tables from response.")  # log added
                text_parts.append("\n\n[TABLES]\n")
                text_parts.extend(tables)
            else:
                logger.debug("No tables found in response.")  # log added
        
        # Extract forms if enabled
        if self.extract_forms:
            logger.debug("Extracting form fields from Textract response.")  # log added
            forms = self._extract_forms(blocks)
            if forms:
                logger.info(f"Extracted {len(forms)} form fields from response.")  # log added
                text_parts.append("\n\n[FORM FIELDS]\n")
                text_parts.extend(forms)
            else:
                logger.debug("No form fields found in response.")  # log added
        
        result = "\n".join(text_parts)
        logger.debug(f"Parsing complete (length={len(result)}).")  # log added
        return result

    def _extract_tables(self, blocks: List[dict]) -> List[str]:
        """
        Extract tables from Textract blocks.
        
        Args:
            blocks: List of Textract blocks.
            
        Returns:
            List of table representations as strings.
        """
        tables = []
        table_blocks = [b for b in blocks if b.get("BlockType") == "TABLE"]
        logger.debug(f"Found {len(table_blocks)} TABLE blocks.")  # log added
        
        for i, table_block in enumerate(table_blocks):
            table_id = table_block["Id"]
            logger.debug(f"Processing TABLE block {i+1}/{len(table_blocks)} with ID {table_id}")  # log added
            
            # Get cells for this table
            cells = [
                b for b in blocks
                if b.get("BlockType") == "CELL" and b.get("Relationships")
                and any(
                    rel.get("Type") == "CHILD" and table_id in rel.get("Ids", [])
                    for rel in b.get("Relationships", [])
                )
            ]
            
            logger.debug(f"Found {len(cells)} CELL blocks for TABLE ID {table_id}")  # log added
            if not cells:
                logger.debug(f"No cells found for TABLE ID {table_id}, skipping table.")  # log added
                continue
            
            # Build table representation
            table_rows = {}
            table_cols = {}
            
            for cell in cells:
                row = cell.get("RowIndex", 0)
                col = cell.get("ColumnIndex", 0)
                
                # Get cell text
                cell_text = ""
                if "Relationships" in cell:
                    for rel in cell["Relationships"]:
                        if rel["Type"] == "CHILD":
                            for child_id in rel.get("Ids", []):
                                child_block = next(
                                    (b for b in blocks if b["Id"] == child_id),
                                    None
                                )
                                if child_block and child_block.get("BlockType") == "WORD":
                                    cell_text += child_block.get("Text", "") + " "
                
                cell_text = cell_text.strip()
                
                if row not in table_rows:
                    table_rows[row] = {}
                table_rows[row][col] = cell_text
            
            # Format as markdown table
            if table_rows:
                max_col = max(max(row.keys()) for row in table_rows.values())
                table_lines = []
                
                # Header row
                header = "| " + " | ".join(
                    table_rows[1].get(i, "") for i in range(1, max_col + 1)
                ) + " |"
                table_lines.append(header)
                table_lines.append("| " + " | ".join(["---"] * max_col) + " |")
                
                # Data rows
                for row_idx in sorted(table_rows.keys()):
                    if row_idx == 1:  # Skip header
                        continue
                    row_data = "| " + " | ".join(
                        table_rows[row_idx].get(i, "") for i in range(1, max_col + 1)
                    ) + " |"
                    table_lines.append(row_data)
                
                tables.append("\n".join(table_lines))
                logger.debug(f"Extracted table (rows={len(table_rows)}) from TABLE ID {table_id}")  # log added
        
        logger.debug(f"{len(tables)} tables extracted in total.")  # log added
        return tables

    def _extract_forms(self, blocks: List[dict]) -> List[str]:
        """
        Extract form fields from Textract blocks.
        
        Args:
            blocks: List of Textract blocks.
            
        Returns:
            List of form field representations.
        """
        form_fields = []
        key_blocks = [b for b in blocks if b.get("BlockType") == "KEY_VALUE_SET" and b.get("EntityTypes") == ["KEY"]]
        logger.debug(f"Found {len(key_blocks)} KEY blocks for form extraction.")  # log added
        
        for i, key_block in enumerate(key_blocks):
            key_id = key_block["Id"]
            logger.debug(f"Processing KEY block {i+1}/{len(key_blocks)} with ID {key_id}")  # log added
            
            # Find associated value
            value_block = None
            if "Relationships" in key_block:
                for rel in key_block["Relationships"]:
                    if rel["Type"] == "VALUE":
                        value_id = rel["Ids"][0] if rel["Ids"] else None
                        if value_id:
                            value_block = next(
                                (b for b in blocks if b["Id"] == value_id),
                                None
                            )
            
            # Extract key text
            key_text = ""
            if "Relationships" in key_block:
                for rel in key_block["Relationships"]:
                    if rel["Type"] == "CHILD":
                        for child_id in rel.get("Ids", []):
                            child_block = next(
                                (b for b in blocks if b["Id"] == child_id),
                                None
                            )
                            if child_block and child_block.get("BlockType") == "WORD":
                                key_text += child_block.get("Text", "") + " "
            
            key_text = key_text.strip()
            
            # Extract value text
            value_text = ""
            if value_block and "Relationships" in value_block:
                for rel in value_block["Relationships"]:
                    if rel["Type"] == "CHILD":
                        for child_id in rel.get("Ids", []):
                            child_block = next(
                                (b for b in blocks if b["Id"] == child_id),
                                None
                            )
                            if child_block and child_block.get("BlockType") == "WORD":
                                value_text += child_block.get("Text", "") + " "
            
            value_text = value_text.strip()
            
            if key_text:
                form_fields.append(f"{key_text}: {value_text}")
                logger.debug(f"Extracted form field: {key_text}: {value_text}")  # log added
        
        logger.info(f"Total form fields extracted: {len(form_fields)}")  # log added
        return form_fields

    async def load(self, source: str, use_async: bool = False, s3_bucket: Optional[str] = None, s3_key: Optional[str] = None) -> List[Document]:
        """
        Load a document using Textract.
        
        Args:
            source: Path to the file (image or PDF).
            use_async: Whether to use async Textract API (for large PDFs).
            s3_bucket: S3 bucket name (required for async).
            s3_key: S3 object key (required for async).
            
        Returns:
            List containing one Document with extracted text.
        """
        file_path = Path(source)
        logger.info(f"Preparing to load document: {source}")  # log added
        
        if not file_path.exists():
            logger.error(f"File not found: {source}")
            return []
        
        try:
            # Check if this is a PDF - Textract sync mode doesn't support PDFs
            doc_type = self._detect_document_type(file_path)
            if doc_type == "pdf" and not (use_async and s3_bucket and s3_key):
                error_msg = (
                    f"Textract sync mode does not support PDF files. "
                    f"PDFs require async processing with S3 (set use_async=True and provide s3_bucket/s3_key), "
                    f"or use PDFLoader instead. File: {source}"
                )
                logger.error(error_msg)
                raise ValueError(error_msg)
            
            if use_async and s3_bucket and s3_key:
                logger.info(f"Using async Textract mode for {source} with S3 bucket {s3_bucket} and key {s3_key}")
                content = await asyncio.to_thread(self._extract_text_async, file_path, s3_bucket, s3_key)
            else:
                logger.info(f"Using sync Textract mode for {source}")
                content = await asyncio.to_thread(self._extract_text_sync, file_path)
            
            if not content.strip():
                logger.warning(f"No text extracted from {source}")
                return []
            
            metadata = self._get_file_metadata(file_path)
            metadata["loader"] = "TextractLoader"
            metadata["extract_tables"] = self.extract_tables
            metadata["extract_forms"] = self.extract_forms
            metadata["extract_layout"] = self.extract_layout
            
            doc = Document(
                content=content,
                metadata=metadata,
                source=str(file_path),
            )
            
            logger.info(f"Extracted text from {source} using Textract ({len(content)} chars)")
            return [doc]
            
        except Exception as e:
            logger.error(f"Failed to extract text from {source}: {e}", exc_info=True)  # log with stack trace
            return []

    async def load_directory(self, directory: str, pattern: str = "*") -> List[Document]:
        """
        Load all supported files from a directory using Textract.
        
        Args:
            directory: Path to directory.
            pattern: Glob pattern for file matching.
            
        Returns:
            List of Document objects.
        """
        dir_path = Path(directory)
        logger.info(f"Loading directory with Textract: {directory}, pattern={pattern}")  # log added
        
        if not dir_path.exists():
            logger.error(f"Directory not found: {directory}")
            return []
        
        supported_extensions = {".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".tif"}
        documents = []
        matched_files = 0  # log counter
        
        for file_path in dir_path.glob(pattern):
            if file_path.is_file() and file_path.suffix.lower() in supported_extensions:
                logger.info(f"Textract processing file {file_path}")  # log added
                docs = await self.load(str(file_path))
                documents.extend(docs)
                matched_files += 1
        
        logger.info(f"Extracted text from {len(documents)} files using Textract in {directory} (matched files: {matched_files})")
        return documents

