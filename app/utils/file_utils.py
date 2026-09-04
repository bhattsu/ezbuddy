import os
import tempfile
import asyncio
import aiofiles
from pathlib import Path
from typing import Optional, Tuple
from fastapi import UploadFile, HTTPException
import logging
# import pymupdf (moved to function to avoid import errors in some environments)

from app.config.settings import settings
from app.api.schemas.document import FileType

logger = logging.getLogger(__name__)

# Lambda-specific: Use /tmp directory explicitly for Lambda
# Lambda provides /tmp with 512MB-10GB storage (depending on configuration)
def get_temp_dir():
    """Get temporary directory, using /tmp in Lambda environment"""
    if os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
        return "/tmp"
    return tempfile.gettempdir()

class FileValidator:
    """File validation and type detection"""

    MIME_TO_FILETYPE = {
        'application/pdf': FileType.PDF,
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': FileType.EXCEL,
        'application/vnd.ms-excel': FileType.EXCEL,
        'text/csv': FileType.CSV,
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document': FileType.WORD,
        'application/msword': FileType.WORD,
        'text/plain': FileType.TXT,
        'text/x-freemarker': FileType.FTL,
        'application/x-ftl': FileType.FTL,
        'image/png': FileType.IMAGE,
        'image/jpeg': FileType.IMAGE,
        'image/jpg': FileType.IMAGE,
        'image/gif': FileType.IMAGE,
        'image/bmp': FileType.IMAGE,
        'image/tiff': FileType.IMAGE,
    }

    FILETYPE_TO_EXTENSION = {
        FileType.PDF: '.pdf',
        FileType.EXCEL: '.xlsx',  # Changed from .excel to .xlsx
        FileType.CSV: '.csv',
        FileType.WORD: '.docx',
        FileType.DOCX: '.docx',
        FileType.TXT: '.txt',
        FileType.FTL: '.ftl',
        FileType.IMAGE: '.png'  # Default image extension
    }

    FILE_SIGNATURES = {
        b'%PDF-': FileType.PDF,
        b'\x89PNG\r\n\x1a\n': FileType.IMAGE,
        b'\xff\xd8\xff': FileType.IMAGE,
        b'GIF8': FileType.IMAGE,
        b'BM': FileType.IMAGE,
        b'PK\x03\x04': None,  # ZIP-based (DOCX, XLSX)
    }

    @staticmethod
    async def create_temp_file(content: bytes, file_type: FileType = None) -> str:
        """
        Safely create a temporary file with the given content and proper extension
        Returns the path to the temporary file
        Lambda-compatible: Uses /tmp directory in Lambda environment
        """
        try:
            # Get proper file extension
            suffix = FileValidator.FILETYPE_TO_EXTENSION.get(file_type, '')
            # Use Lambda /tmp directory if in Lambda environment
            temp_dir = get_temp_dir()
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix, dir=temp_dir) as tmp_file:
                # Write content and flush to disk
                tmp_file.write(content)
                tmp_file.flush()
                os.fsync(tmp_file.fileno())

                # Verify content was written
                tmp_file.seek(0, 2)  # Seek to end
                if tmp_file.tell() == 0:
                    raise ValueError("Failed to write content to temporary file")

                # For PDFs, perform additional validation
                if suffix and suffix.lower().endswith('.pdf'):
                    tmp_file.seek(0)
                    if not tmp_file.read().startswith(b'%PDF-'):
                        raise ValueError("Invalid PDF format")

                return tmp_file.name
        except Exception as e:
            logger.error(f"Error creating temporary file: {str(e)}")
            raise RuntimeError(f"Failed to create temporary file: {str(e)}")

    @staticmethod
    async def validate_file_size(file: UploadFile) -> int:
        """Validate file size without reading entire file"""
        try:
            file.file.seek(0, 2)  # Seek to end
            file_size = file.file.tell()
            file.file.seek(0)  # Reset to beginning

            max_size = settings.MAX_FILE_SIZE_MB * 1024 * 1024
            if file_size > max_size:
                raise HTTPException(
                    status_code=413,
                    detail=f"File too large. Maximum size: {settings.MAX_FILE_SIZE_MB}MB"
                )

            logger.info(f"File size validated: {file_size / 1024 / 1024:.2f}MB")
            return file_size

        except Exception as e:
            logger.error(f"Error validating file size: {str(e)}")
            raise HTTPException(status_code=400, detail=f"Failed to validate file size: {str(e)}")

    @staticmethod
    async def validate_pdf(file_path: str) -> bool:
        """
        Validate if a PDF file is properly formatted and readable
        Returns True if valid, raises ValueError with details if invalid
        """
        try:
            import pymupdf
            # Try to open and read the PDF
            doc = pymupdf.open(file_path)
            if doc.page_count == 0:
                doc.close()
                raise ValueError("PDF contains no pages")

            # Check if the PDF has a root object
            if not doc.pdf_catalog:
                doc.close()
                raise ValueError("PDF is corrupted: No root object found")

            # Try to access first page to verify content
            first_page = doc[0]
            if not first_page:
                doc.close()
                raise ValueError("Unable to access PDF content")

            doc.close()
            return True

        except pymupdf.FileDataError as e:
            raise ValueError(f"Invalid PDF file structure: {str(e)}")
        except Exception as e:
            raise ValueError(f"Failed to validate PDF: {str(e)}")

    @staticmethod
    async def detect_file_type(file: UploadFile, file_bytes: Optional[bytes] = None) -> FileType:
        """Detect file type using multiple methods"""

        # Extension-first for ambiguous MIME types (browser often sends text/plain / octet-stream)
        if file.filename:
            ext = Path(file.filename).suffix.lower()
            ext_map = {
                '.pdf': FileType.PDF,
                '.xlsx': FileType.EXCEL,
                '.xlsm': FileType.EXCEL,
                '.xls': FileType.EXCEL,
                '.csv': FileType.CSV,
                '.docx': FileType.WORD,
                '.doc': FileType.WORD,
                '.ftl': FileType.FTL,
                '.txt': FileType.TXT,
                '.png': FileType.IMAGE,
                '.jpg': FileType.IMAGE,
                '.jpeg': FileType.IMAGE,
                '.gif': FileType.IMAGE,
                '.bmp': FileType.IMAGE,
                '.tiff': FileType.IMAGE,
            }
            ext_type = ext_map.get(ext)
            ambiguous_mime = (file.content_type or "").lower() in {
                "text/plain",
                "application/octet-stream",
                "",
            }
            if ext_type and ambiguous_mime:
                logger.info("File type detected from extension (ambiguous MIME): %s", ext_type)
                return ext_type

        # Method 1: Check MIME type from upload
        if file.content_type:
            file_type = FileValidator.MIME_TO_FILETYPE.get(file.content_type.lower())
            if file_type:
                logger.info(f"File type detected from MIME: {file_type}")
                return file_type

        # Method 2: Check file signature (magic bytes)
        if file_bytes:
            for signature, ftype in FileValidator.FILE_SIGNATURES.items():
                if file_bytes.startswith(signature):
                    if ftype:
                        logger.info(f"File type detected from signature: {ftype}")
                        return ftype
                    # ZIP-based format, check extension
                    break

        # Method 3: Check file extension
        if file.filename:
            ext = Path(file.filename).suffix.lower()
            file_type = ext_map.get(ext)
            if file_type:
                logger.info(f"File type detected from extension: {file_type}")
                return file_type

        raise HTTPException(
            status_code=415,
            detail=(
                "Unsupported file type. Supported: PDF, DOCX, FTL, Excel, CSV, TXT, Images"
            ),
        )

    @staticmethod
    def detect_file_type_from_bytes(file_bytes: bytes, filename: str) -> FileType:
        """Detect file type from bytes and filename (WebSocket upload path)."""
        ext_map = {
            ".pdf": FileType.PDF,
            ".docx": FileType.WORD,
            ".doc": FileType.WORD,
        }
        if filename:
            ext = Path(filename).suffix.lower()
            if ext in ext_map:
                return ext_map[ext]
        for signature, ftype in FileValidator.FILE_SIGNATURES.items():
            if file_bytes.startswith(signature) and ftype:
                return ftype
        if file_bytes.startswith(b"PK\x03\x04") and filename.lower().endswith(".docx"):
            return FileType.WORD
        raise HTTPException(status_code=415, detail="Unsupported file type for upload")

class TempFileManager:
    """Async temporary file management"""

    @staticmethod
    async def save_upload_to_temp(
        file: UploadFile,
        file_type: Optional[FileType] = None
    ) -> str:
        """
        Save uploaded file to temporary location with enhanced validation
        Returns the path to the temporary file
        Raises ValueError if validation fails
        Lambda-compatible: Uses /tmp directory in Lambda environment
        """
        try:
            # Get proper file extension based on file type
            suffix = FileValidator.FILETYPE_TO_EXTENSION.get(file_type, '')
            if suffix is None and file.filename:
                suffix = Path(file.filename).suffix

            logger.info(f"Using extension {suffix} for file type {file_type}")

            # Use Lambda /tmp directory if in Lambda environment
            temp_dir = get_temp_dir()

            # Create a temporary file
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix or '', dir=temp_dir) as tmp_file:
                # Read chunks and write
                file.file.seek(0)
                while chunk := await file.read(8192):  # 8KB chunks
                    tmp_file.write(chunk)
                tmp_file.flush()
                os.fsync(tmp_file.fileno())  # Ensure content is written to disk

                # Verify content was written
                tmp_file.seek(0, 2)  # Seek to end
                file_size = tmp_file.tell()
                if file_size == 0:
                    raise ValueError("No content was written to temporary file")

                # For PDFs, perform additional validation
                if suffix and suffix.lower().endswith('.pdf'):
                    tmp_file.seek(0)
                    pdf_header = tmp_file.read(5)
                    if not pdf_header.startswith(b'%PDF-'):
                        raise ValueError("Invalid PDF format: Missing PDF signature")

                logger.info(f"Successfully created temporary file: {tmp_file.name} (size: {file_size} bytes)")
                return tmp_file.name

        except Exception as e:
            logger.error(f"Failed to save upload to temp file: {str(e)}")
            if 'tmp_file' in locals() and os.path.exists(tmp_file.name):
                try:
                    os.unlink(tmp_file.name)
                except Exception as cleanup_error:
                    logger.warning(f"Failed to clean up temp file: {str(cleanup_error)}")
            raise ValueError(f"Failed to save upload: {str(e)}")

    @staticmethod
    async def save_bytes_to_temp(
        file_bytes: bytes,
        filename: str,
        file_type: Optional[FileType] = None,
    ) -> str:
        """Save raw bytes to a temp file (WebSocket base64 upload path)."""
        return await FileValidator.create_temp_file(file_bytes, file_type=file_type)

    @staticmethod
    async def cleanup_temp_file(file_path: str):
        """Safely delete temporary file"""
        try:
            if os.path.exists(file_path):
                await asyncio.get_event_loop().run_in_executor(
                    None,
                    os.unlink,
                    file_path
                )
                logger.info(f"Cleaned up temp file: {file_path}")
        except Exception as e:
            logger.warning(f"Failed to cleanup temp file {file_path}: {str(e)}")

def get_page_numbers_to_process(
    total_pages: int,
    pages: Optional[list] = None,
    page_range: Optional[Tuple[int, Optional[int]]] = None,
    process_all: bool = False  # New parameter
) -> list:
    """
    Determine which page numbers to process
    Returns 0-indexed page numbers

    Args:
        total_pages: Total pages in document
        pages: Specific page numbers (1-indexed) [1, 3, 5]
        page_range: Tuple of (start, end) pages (1-indexed)
        process_all: If True, ignores MAX_PAGES_PER_REQUEST limit
    """
    if pages:
        # Convert 1-indexed to 0-indexed
        page_nums = [p - 1 for p in pages if 0 < p <= total_pages]
        if not page_nums:
            raise ValueError("No valid page numbers specified")
        return sorted(set(page_nums))

    if page_range:
        start, end = page_range
        end = end or total_pages
        if start > total_pages:
            raise ValueError(f"Start page {start} exceeds total pages {total_pages}")
        # Convert 1-indexed to 0-indexed
        return list(range(start - 1, min(end, total_pages)))

    # No specific pages/range = process all pages
    if process_all:
        # Process ALL pages without limit
        logger.info(f"Processing all {total_pages} pages (no limit)")
        return list(range(total_pages))
    else:
        # Apply safety limit (default behavior)
        max_pages = min(total_pages, settings.MAX_PAGES_PER_REQUEST)
        if total_pages > max_pages:
            logger.warning(f"Limiting processing to first {max_pages} pages. "
                          f"Use page_range or set process_all=True for more.")
        return list(range(max_pages))