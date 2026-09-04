"""
Court rules knowledge endpoints.

POST /api/court-rules/ingest   — upload .txt / .docx into OpenSearch
POST /api/court-rules/retrieve — semantic search; return top-k chunks (no LLM)
POST /api/court-rules/query    — retrieve + answer from court_rules index
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from app.api.dependencies import get_embedder, get_llm_client
from app.api.schemas.court_rules import (
    CourtRulesIngestResponse,
    CourtRulesQueryRequest,
    CourtRulesQueryResponse,
    CourtRulesResetIndexResponse,
    CourtRulesRetrieveRequest,
    CourtRulesRetrieveResponse,
)
from app.api.schemas.rag import ErrorResponse
from app.services.court_rules_service import CourtRulesService

logger = logging.getLogger(__name__)
router = APIRouter()


def get_court_rules_service(
    embedder=Depends(get_embedder),
    llm_client=Depends(get_llm_client),
) -> CourtRulesService:
    return CourtRulesService(embedder=embedder, llm_client=llm_client)


@router.post(
    "/ingest",
    response_model=CourtRulesIngestResponse,
    responses={
        400: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
    summary="Ingest a court-rules document into OpenSearch",
    description=(
        "Upload a .txt or .docx court-rules / FAQ document. "
        "Text is structure-chunked, embedded with Bedrock, and stored in the "
        "court_rules OpenSearch index. Re-uploading the same logical source "
        "replaces prior chunks."
    ),
)
async def ingest_court_rules(
    file: UploadFile = File(..., description="Court rules file (.txt or .docx)"),
    state_code: str = Form("TX"),
    case_type: str = Form("divorce"),
    doc_type: Optional[str] = Form(
        None,
        description="Optional: faq | standard_rules | statewide_rule | court_rule",
    ),
    replace_existing: bool = Form(True),
    service: CourtRulesService = Depends(get_court_rules_service),
):
    file_name = file.filename or "court_rules.txt"
    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    try:
        result = await service.ingest_file_bytes(
            file_bytes=file_bytes,
            file_name=file_name,
            state_code=state_code,
            case_type=case_type,
            doc_type=doc_type,
            replace_existing=replace_existing,
        )
        return CourtRulesIngestResponse(**result)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Court-rules ingest failed for %s", file_name)
        raise HTTPException(
            status_code=500,
            detail=f"Court-rules ingest failed: {exc}",
        ) from exc


@router.delete(
    "/index",
    response_model=CourtRulesResetIndexResponse,
    responses={500: {"model": ErrorResponse}},
    summary="Delete the court_rules index (then re-ingest)",
    description=(
        "Drops the court_rules OpenSearch index. Use this when the index was "
        "auto-created with a wrong mapping (embedding not knn_vector). "
        "Re-ingest the documents afterwards."
    ),
)
async def reset_court_rules_index(
    service: CourtRulesService = Depends(get_court_rules_service),
):
    try:
        result = await service.reset_index()
        return CourtRulesResetIndexResponse(**result)
    except Exception as exc:
        logger.exception("Court-rules index reset failed")
        raise HTTPException(
            status_code=500,
            detail=f"Court-rules index reset failed: {exc}",
        ) from exc


@router.post(
    "/retrieve",
    response_model=CourtRulesRetrieveResponse,
    responses={
        400: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
    summary="Retrieve top-k court-rules chunks (semantic search only)",
    description=(
        "Embed the input query with Bedrock Titan and return the top-k nearest "
        "chunks from the court_rules OpenSearch index. Does not call the LLM — "
        "use this to inspect what RAG would fetch."
    ),
)
async def retrieve_court_rules_chunks(
    body: CourtRulesRetrieveRequest,
    service: CourtRulesService = Depends(get_court_rules_service),
):
    try:
        result = await service.retrieve_chunks(
            body.query,
            state_code=body.state_code,
            case_type=body.case_type,
            top_k=body.top_k,
        )
        return CourtRulesRetrieveResponse(**result)
    except Exception as exc:
        logger.exception("Court-rules retrieve failed")
        raise HTTPException(
            status_code=500,
            detail=f"Court-rules retrieve failed: {exc}",
        ) from exc


@router.post(
    "/query",
    response_model=CourtRulesQueryResponse,
    responses={
        400: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
    summary="Ask a court-rules question (RAG over OpenSearch)",
    description="Retrieve relevant court-rule chunks from OpenSearch and generate an answer.",
)
async def query_court_rules(
    body: CourtRulesQueryRequest,
    service: CourtRulesService = Depends(get_court_rules_service),
):
    try:
        result = await service.answer_question(
            body.question,
            state_code=body.state_code,
            case_type=body.case_type,
            top_k=body.top_k,
        )
        return CourtRulesQueryResponse(**result)
    except Exception as exc:
        logger.exception("Court-rules query failed")
        raise HTTPException(
            status_code=500,
            detail=f"Court-rules query failed: {exc}",
        ) from exc
