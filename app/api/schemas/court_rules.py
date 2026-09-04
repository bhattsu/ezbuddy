"""Schemas for court-rules knowledge ingestion."""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class CourtRulesIngestResponse(BaseModel):
    success: bool
    collection_name: str
    vector_store: str = "opensearch"
    source_key: str
    source_file: str
    state_code: str
    case_type: str
    doc_type: str
    total_documents: int
    total_chunks: int
    vectors_stored: int
    failed_chunks: int = 0
    vector_dim: Optional[int] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class CourtRulesQueryRequest(BaseModel):
    question: str = Field(..., min_length=2, description="Court rules question")
    state_code: Optional[str] = Field(default=None, description="e.g. TX")
    case_type: Optional[str] = Field(default=None, description="e.g. divorce")
    top_k: Optional[int] = Field(default=None, ge=1, le=20)


class CourtRulesQueryResponse(BaseModel):
    answer: str
    sources: List[Dict[str, Any]] = Field(default_factory=list)
    used_rag: bool = True
    collection_name: Optional[str] = None
    vector_store: Optional[str] = None
    filters: Dict[str, Any] = Field(default_factory=dict)


class CourtRulesRetrieveRequest(BaseModel):
    """Semantic search only — returns top-k chunks, no LLM answer."""

    query: str = Field(..., min_length=2, description="Search text / question")
    state_code: Optional[str] = Field(default=None, description="e.g. TX")
    case_type: Optional[str] = Field(default=None, description="e.g. divorce")
    top_k: Optional[int] = Field(
        default=None,
        ge=1,
        le=20,
        description="Number of chunks to return (default COURT_RULES_TOP_K)",
    )


class CourtRulesChunkHit(BaseModel):
    rank: int
    id: Optional[str] = None
    score: Optional[float] = None
    content: str = ""
    source_file: Optional[str] = None
    source_key: Optional[str] = None
    section_title: Optional[str] = None
    state_code: Optional[str] = None
    case_type: Optional[str] = None
    doc_type: Optional[str] = None
    chunk_index: Optional[int] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class CourtRulesResetIndexResponse(BaseModel):
    collection_name: str
    vector_store: str
    existed: bool
    deleted: bool
    previous_embedding_type: Optional[str] = None


class CourtRulesRetrieveResponse(BaseModel):
    query: str
    top_k: int
    chunk_count: int
    collection_name: str
    vector_store: str
    filters: Dict[str, Any] = Field(default_factory=dict)
    chunks: List[CourtRulesChunkHit] = Field(default_factory=list)
