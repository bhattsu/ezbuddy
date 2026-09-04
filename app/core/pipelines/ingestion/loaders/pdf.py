"""
PDF Document Loader

Loader for PDF files.
"""

import logging
from typing import List
from pathlib import Path

from app.core.pipelines.ingestion.loaders.base import DocumentLoader, Document

logger = logging.getLogger(__name__)


class PDFLoader(DocumentLoader):
    """
    Loader for PDF files.
    
    Uses PyPDF2 or pdfplumber for text extraction.
    """

    def __init__(self, extract_images: bool = False):
        """
        Initialize the PDF loader.
        
        Args:
            extract_images: Whether to extract images (not implemented).
        """
        self.extract_images = extract_images
        logger.debug(f"Initializing PDFLoader. extract_images={extract_images}")
        self._check_dependencies()

    def _check_dependencies(self):
        """Check if required PDF libraries are available."""
        self._pypdf2_available = False
        self._pdfplumber_available = False
        
        try:
            import PyPDF2
            self._pypdf2_available = True
            logger.debug("PyPDF2 is available.")
        except ImportError:
            logger.debug("PyPDF2 is NOT available.")
        
        try:
            import pdfplumber
            self._pdfplumber_available = True
            logger.debug("pdfplumber is available.")
        except ImportError:
            logger.debug("pdfplumber is NOT available.")
        
        if not self._pypdf2_available and not self._pdfplumber_available:
            logger.warning(
                "No PDF library found. Install PyPDF2 or pdfplumber: "
                "pip install PyPDF2 pdfplumber"
            )

    def _extract_with_pypdf2(self, file_path: Path) -> str:
        """Extract text using PyPDF2."""
        import PyPDF2
        logger.debug(f"Extracting text from PDF using PyPDF2: {file_path}")
        text_parts = []
        with open(file_path, "rb") as f:
            reader = PyPDF2.PdfReader(f)
            logger.debug(f"PyPDF2 found {len(reader.pages)} pages.")
            for page_num, page in enumerate(reader.pages):
                page_text = page.extract_text()
                logger.debug(f"PyPDF2 page {page_num + 1}: {'[Text found]' if page_text else '[No text]'}")
                if page_text:
                    text_parts.append(f"[Page {page_num + 1}]\n{page_text}")
        
        logger.debug(f"PyPDF2 extracted {len(text_parts)} non-empty pages from {file_path}")
        return "\n\n".join(text_parts)

    def _extract_with_pdfplumber(self, file_path: Path) -> str:
        """Extract text using pdfplumber."""
        import pdfplumber
        logger.debug(f"Extracting text from PDF using pdfplumber: {file_path}")
        text_parts = []
        with pdfplumber.open(file_path) as pdf:
            logger.debug(f"pdfplumber found {len(pdf.pages)} pages.")
            for page_num, page in enumerate(pdf.pages):
                page_text = page.extract_text()
                logger.debug(f"pdfplumber page {page_num + 1}: {'[Text found]' if page_text else '[No text]'}")
                if page_text:
                    text_parts.append(f"[Page {page_num + 1}]\n{page_text}")
        
        logger.debug(f"pdfplumber extracted {len(text_parts)} non-empty pages from {file_path}")
        return "\n\n".join(text_parts)

    async def load(self, source: str) -> List[Document]:
        """
        Load a PDF file.
        
        Args:
            source: Path to the PDF file.
            
        Returns:
            List containing one Document.
        """
        file_path = Path(source)
        
        logger.info(f"Loading PDF file: {source}")
        
        if not file_path.exists():
            logger.error(f"File not found: {source}")
            return []
        
        if not file_path.suffix.lower() == ".pdf":
            logger.warning(f"File is not a PDF: {source}")
            return []
        
        try:
            # Try pdfplumber first (better text extraction)
            if self._pdfplumber_available:
                logger.info("Using pdfplumber for PDF extraction.")
                content = self._extract_with_pdfplumber(file_path)
            elif self._pypdf2_available:
                logger.info("Using PyPDF2 for PDF extraction.")
                content = self._extract_with_pypdf2(file_path)
            else:
                logger.error("No PDF library available")
                return []
            
            if not content.strip():
                logger.warning(f"No text extracted from PDF: {source}")
                return []
            
            metadata = self._get_file_metadata(file_path)
            metadata["loader"] = "PDFLoader"
            
            doc = Document(
                content=content,
                metadata=metadata,
                source=str(file_path),
            )
            
            logger.info(f"Loaded PDF: {source} ({len(content)} chars)")
            return [doc]
            
        except Exception as e:
            logger.exception(f"Failed to load PDF {source}: {e}")
            return []

    async def load_directory(self, directory: str, pattern: str = "*.pdf") -> List[Document]:
        """
        Load all PDF files from a directory.
        
        Args:
            directory: Path to directory.
            pattern: Glob pattern for file matching.
            
        Returns:
            List of Document objects.
        """
        dir_path = Path(directory)
        
        logger.info(f"Loading PDFs from directory: {directory}, pattern: {pattern}")
        
        if not dir_path.exists():
            logger.error(f"Directory not found: {directory}")
            return []
        
        documents = []
        files_found = list(dir_path.glob(pattern))
        logger.debug(f"Found {len(files_found)} files matching pattern {pattern} in {directory}")
        for file_path in files_found:
            if file_path.is_file():
                logger.debug(f"Processing file: {file_path}")
                docs = await self.load(str(file_path))
                if docs:
                    logger.debug(f"Loaded {len(docs)} document(s) from {file_path}")
                else:
                    logger.warning(f"No documents loaded from {file_path}")
                documents.extend(docs)
            else:
                logger.debug(f"Skipping non-file path: {file_path}")
        
        logger.info(f"Loaded {len(documents)} PDF files from {directory}")
        return documents

