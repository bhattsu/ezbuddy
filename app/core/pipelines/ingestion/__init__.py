"""
Data Ingestion Module

This module provides document loading, chunking, and embedding pipeline
for ingesting data into vector stores.

Supported Loaders:
- TextLoader: Plain text files (.txt, .md)
- PDFLoader: PDF files using PyPDF2/pdfplumber
- JSONLoader: JSON and JSONL files
- TextractLoader: AWS Textract for images, scanned PDFs, forms, and tables
"""

from app.core.pipelines.ingestion.pipeline import IngestionPipeline
from app.core.pipelines.ingestion.chunkers.base import ChunkingStrategy, Chunk
from app.core.pipelines.ingestion.loaders.base import DocumentLoader, Document
from app.core.pipelines.ingestion.loaders.textract import TextractLoader

__all__ = [
    "IngestionPipeline",
    "ChunkingStrategy",
    "Chunk",
    "DocumentLoader",
    "Document",
    "TextractLoader",
]

