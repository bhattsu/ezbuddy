"""AWS Textract utilities (PDF/image helpers, temp dir, etc.)."""

import pymupdf
from typing import List, Dict, Any, Tuple
from PIL import Image
from io import BytesIO
import base64
import tempfile
import os
import logging
from app.adapters.aws_clients import aws_clients
import pypandoc

logger = logging.getLogger(__name__)
textract = aws_clients.get_textract()


def get_temp_dir():
    """Get temporary directory, using /tmp in Lambda environment"""
    if os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
        return "/tmp"
    return tempfile.gettempdir()


def extract_page_texts(pdf_bytes: bytes) -> List[str]:
    if not pdf_bytes:
        raise ValueError("PDF bytes cannot be empty or None")
    doc = None
    try:
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        if doc.page_count == 0:
            logger.warning("PDF document contains no pages")
            return []
        page_texts = []
        for page_num in range(doc.page_count):
            try:
                page = doc.load_page(page_num)
                text = page.get_text().strip()
                page_texts.append(text)
                logger.info(f"Successfully extracted text from page {page_num + 1}")
            except Exception as page_error:
                logger.error(f"Failed to extract text from page {page_num + 1}: {str(page_error)}")
                page_texts.append("")
        return page_texts
    except Exception as e:
        logger.error(f"Unexpected error extracting PDF text: {str(e)}")
        raise RuntimeError(f"Failed to extract text from PDF: {str(e)}")
    finally:
        if doc:
            try:
                doc.close()
            except Exception as close_error:
                logger.warning(f"Failed to close PDF document: {str(close_error)}")


def extract_structured_textract(pdf_bytes: bytes, page_num: int) -> Dict[str, Any]:
    def cleanup_temp_file(path):
        if path and os.path.exists(path):
            try:
                os.unlink(path)
                logger.debug(f"Cleaned up temporary file: {path}")
            except Exception as cleanup_error:
                logger.warning(f"Failed to clean up temporary file {path}: {str(cleanup_error)}")

    if not pdf_bytes:
        raise ValueError("PDF bytes cannot be empty or None")
    if page_num < 0:
        raise ValueError("Page number must be non-negative")
    if not pdf_bytes.startswith(b'%PDF-'):
        raise ValueError("Invalid PDF format - Missing PDF signature")

    tmp_file_path = None
    try:
        temp_dir = get_temp_dir()
        with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf', dir=temp_dir) as tmp_file:
            tmp_file_path = tmp_file.name
            tmp_file.write(pdf_bytes)
            tmp_file.flush()
            tmp_file.seek(0, 2)
            if tmp_file.tell() == 0:
                raise ValueError("Failed to write PDF content to temporary file")

        logger.info(f"Processing page {page_num} with Textract")
        response = textract.analyze_document(
            Document={'Bytes': pdf_bytes},
            FeatureTypes=["FORMS", "TABLES"]
        )
        if not response:
            logger.warning("Textract returned empty response")
            return {}
        return response

    except (ValueError,
            textract.exceptions.InvalidParameterException,
            textract.exceptions.UnsupportedDocumentException,
            textract.exceptions.DocumentTooLargeException) as e:
        logger.error(f"Textract or validation exception: {str(e)}")
        raise ValueError(str(e))
    except textract.exceptions.ThrottlingException as throttle_error:
        logger.error(f"Textract throttling error: {str(throttle_error)}")
        raise RuntimeError(f"Service temporarily unavailable: {str(throttle_error)}")
    except Exception as e:
        logger.error(f"Unexpected error with Textract: {str(e)}")
        raise RuntimeError(f"Failed to analyze document with Textract: {str(e)}")
    finally:
        cleanup_temp_file(tmp_file_path)


async def extract_from_image(image_bytes: bytes) -> Tuple[List[str], Dict[str, Any]]:
    if not image_bytes:
        raise ValueError("Image bytes cannot be empty or None")
    try:
        logger.info("Detecting text from image using Textract")
        text_response = textract.detect_document_text(Document={'Bytes': image_bytes})
        if not text_response or 'Blocks' not in text_response:
            logger.warning("No text blocks found in image")
            text_lines = []
        else:
            text_lines = [
                item.get('Text', '') for item in text_response.get('Blocks', [])
                if item.get('BlockType') == 'LINE'
            ]
        text = " ".join(text_lines) if text_lines else ""
        logger.info(f"Extracted {len(text_lines)} text lines from image")
    except textract.exceptions.InvalidParameterException as param_error:
        logger.error(f"Invalid parameters for text detection: {str(param_error)}")
        raise ValueError(f"Invalid image format for text detection: {str(param_error)}")
    except textract.exceptions.UnsupportedDocumentException as doc_error:
        logger.error(f"Unsupported image format: {str(doc_error)}")
        raise ValueError(f"Unsupported image format: {str(doc_error)}")
    except Exception as text_error:
        logger.error(f"Error detecting text from image: {str(text_error)}")
        text = ""

    try:
        logger.info("Analyzing document structure from image using Textract")
        structured_response = textract.analyze_document(
            Document={'Bytes': image_bytes},
            FeatureTypes=["FORMS", "TABLES"]
        )
        if not structured_response:
            logger.warning("No structured data found in image")
            structured_response = {}
        logger.info("Successfully analyzed image structure with Textract")
    except textract.exceptions.InvalidParameterException as param_error:
        logger.error(f"Invalid parameters for structure analysis: {str(param_error)}")
        structured_response = {"error": f"Invalid parameters: {str(param_error)}"}
    except textract.exceptions.UnsupportedDocumentException as doc_error:
        logger.error(f"Unsupported image format for structure analysis: {str(doc_error)}")
        structured_response = {"error": f"Unsupported format: {str(doc_error)}"}
    except Exception as struct_error:
        logger.error(f"Error analyzing image structure: {str(struct_error)}")
        structured_response = {"error": str(struct_error)}

    return [text] if text else [""], structured_response


def pdf_page_png_bytes(pdf_bytes: bytes, page_number: int = 0, dpi: int = 150) -> bytes:
    """Render one PDF page to PNG bytes for Textract's sync image API."""
    if not pdf_bytes:
        raise ValueError("PDF bytes cannot be empty or None")
    if page_number < 0:
        raise ValueError("Page number must be non-negative")
    doc = None
    try:
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        if page_number >= doc.page_count:
            raise ValueError(
                f"Page number {page_number} exceeds document page count ({doc.page_count})"
            )
        page = doc.load_page(page_number)
        pix = page.get_pixmap(dpi=dpi)
        return pix.tobytes("png")
    finally:
        if doc:
            doc.close()


def pdf_to_image(pdf_bytes: bytes, page_number: int = 0, dpi: int = 300) -> str:
    if not pdf_bytes:
        raise ValueError("PDF bytes cannot be empty or None")
    if page_number < 0:
        raise ValueError("Page number must be non-negative")
    doc = None
    try:
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        if page_number >= doc.page_count:
            raise ValueError(f"Page number {page_number} exceeds document page count ({doc.page_count})")
        page = doc.load_page(page_number)
        try:
            pix = page.get_pixmap(dpi=dpi)
        except Exception as pixmap_error:
            logger.error(f"Failed to create pixmap for page {page_number}: {str(pixmap_error)}")
            raise RuntimeError(f"Failed to render page {page_number}: {str(pixmap_error)}")
        try:
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        except Exception as img_error:
            logger.error(f"Failed to create PIL image: {str(img_error)}")
            raise RuntimeError(f"Failed to create image from page data: {str(img_error)}")
        try:
            img_buffer = BytesIO()
            img.save(img_buffer, format="PNG")
            img_base64 = base64.b64encode(img_buffer.getvalue()).decode("utf-8")
            logger.info(f"Successfully converted page {page_number} to base64 image")
            return img_base64
        except Exception as encode_error:
            logger.error(f"Failed to encode image to base64: {str(encode_error)}")
            raise RuntimeError(f"Failed to encode image: {str(encode_error)}")
    except ValueError:
        raise
    except Exception as e:
        logger.error(f"Unexpected error converting PDF to image: {str(e)}")
        raise RuntimeError(f"Failed to convert PDF to image: {str(e)}")
    finally:
        if doc:
            try:
                doc.close()
            except Exception as close_error:
                logger.warning(f"Failed to close PDF document: {str(close_error)}")


def docx_to_pdf_bytes(input_path: str) -> bytes:
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"Input file does not exist: {input_path}")
    if not os.path.isfile(input_path):
        raise ValueError(f"Input path is not a file: {input_path}")

    temp_base_dir = get_temp_dir()
    with tempfile.TemporaryDirectory(dir=temp_base_dir) as temp_dir:
        output_path = os.path.join(temp_dir, "output.pdf")
        try:
            pypandoc.convert_file(
                input_path,
                'pdf',
                outputfile=output_path,
                format='docx'
            )
        except Exception as e:
            raise RuntimeError(f"Failed to convert DOCX to PDF: {str(e)}")
        with open(output_path, "rb") as f:
            pdf_bytes = f.read()
        if not pdf_bytes:
            raise RuntimeError("Converted PDF file is empty")
        return pdf_bytes
