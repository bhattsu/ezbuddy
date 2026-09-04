"""
Document Loaders

This module provides document loaders for various file formats.
"""

from app.core.pipelines.ingestion.loaders.base import DocumentLoader, Document
from app.core.pipelines.ingestion.loaders.text import TextLoader
from app.core.pipelines.ingestion.loaders.pdf import PDFLoader
from app.core.pipelines.ingestion.loaders.json_loader import JSONLoader
from app.core.pipelines.ingestion.loaders.textract import TextractLoader
from app.core.pipelines.ingestion.loaders.image import ImageLoader
from app.core.pipelines.ingestion.loaders.docx_loader import DocxLoader

__all__ = [
    "DocumentLoader",
    "Document",
    "TextLoader",
    "PDFLoader",
    "JSONLoader",
    "TextractLoader",
    "ImageLoader",
    "DocxLoader",
]

