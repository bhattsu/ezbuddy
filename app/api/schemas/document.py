from pydantic import BaseModel, Field, field_validator, model_validator
from typing import List, Dict, Any, Optional, Union
from enum import Enum
from app.config.settings import settings

class FileType(str, Enum):
    PDF = "pdf"
    EXCEL = "excel"
    CSV = "csv"
    WORD = "word"
    DOCX = "docx"
    FTL = "ftl"
    TXT = "txt"
    IMAGE = "image"

class ProcessingStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"

class ExtractionMethod(str, Enum):
    TEXTRACT = "textract"
    CUSTOM = "custom_extractor"

class PageRange(BaseModel):
    """Model for specifying page ranges"""
    start: int = Field(1, ge=1, description="Start page (1-indexed)")
    end: Optional[int] = Field(None, ge=1, description="End page (inclusive, optional)")

    @model_validator(mode='after')
    def validate_range(self) -> 'PageRange':
        if self.end is not None and self.end < self.start:
            raise ValueError('end page must be >= start page')
        return self

class ExtractionRequest(BaseModel):
    """Request model for document extraction"""
    method: ExtractionMethod = Field(ExtractionMethod.TEXTRACT)
    process_with_llm: bool = Field(True, description="Process with LLM")
    extract_text: bool = Field(True)
    extract_tables: bool = Field(True)
    extract_images: bool = Field(True)

    # Page/sheet selection
    pages: Optional[List[int]] = Field(None, description="Specific pages to extract (1-indexed)")
    page_range: Optional[PageRange] = Field(None, description="Page range to extract")
    sheets: Optional[List[str]] = Field(None, description="Specific Excel sheets to process")
    process_all: bool = Field(False, description="Process all pages without limit (use with caution)")

    @field_validator('pages')
    @classmethod
    def validate_pages(cls, v):
        if v is not None:
            if not v:
                raise ValueError('pages list cannot be empty')
            if any(p < 1 for p in v):
                raise ValueError('page numbers must be >= 1')
        return v

class TableInfo(BaseModel):
    table_id: str
    page_number: int
    headers: List[str]
    rows: List[List[str]]
    confidence: Optional[float] = None

    @field_validator('rows', mode='before')
    @classmethod
    def validate_rows(cls, v):
        if v is None:
            return []
        # Convert any None values in rows to empty strings
        return [[str(cell) if cell is not None else '' for cell in row] for row in v]

    @field_validator('headers', mode='before')
    @classmethod
    def validate_headers(cls, v):
        if v is None:
            return []
        # Convert any None values in headers to empty strings
        return [str(header) if header is not None else '' for header in v]

class ImageInfo(BaseModel):
    image_id: str
    file_path: str
    page_number: int
    format: str
    width: Optional[int] = None
    height: Optional[int] = None

class FormField(BaseModel):
    """Key-value pair from forms"""
    key: str
    value: str
    confidence: Optional[float] = None

class PageData(BaseModel):
    page_number: int
    text_content: str = ""
    tables: List[TableInfo] = []
    images: List[ImageInfo] = []
    forms: List[FormField] = []
    metadata: Dict[str, Any] = {}

class ExtractionResponse(BaseModel):
    """Response model for extraction"""
    document_id: str
    file_name: str
    file_type: FileType
    status: ProcessingStatus
    method: ExtractionMethod

    # Results
    total_pages: int
    pages_processed: int
    pages_data: List[PageData] = []

    llm_output: Optional[Dict[str, Any]] = None

    processing_time: float
    summary: Dict[str, Any] = {}
    metadata: Dict[str, Any] = {}
    error_message: Optional[str] = None

    s3_key: Optional[str] = None
    textract_job_id: Optional[str] = None

class TextractJobStatus(BaseModel):
    """Status check for async Textract jobs"""
    job_id: str
    status: ProcessingStatus
    document_id: str
    progress: Optional[float] = None
    result: Optional[ExtractionResponse] = None