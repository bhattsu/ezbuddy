"""Resolve dropdown options for configuration questions (static lookup or US Legal Pro API)."""

import json
import logging
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.dependencies.auth import get_optional_auth
from app.api.dependencies.rate_limit import limiter, rate_limit_string
from app.api.dependencies.rds import require_rds_repo
from app.api.schemas.question_options import (
    QuestionOptionsRequest,
    QuestionOptionsResponse,
)
from app.api.schemas.rag import ErrorResponse
from app.adapters.data_sources.AIM_rds import RDSRepository
from app.services.question_options_resolver import resolve_question_options
from app.services.uslegalpro_api_client import USLegalProApiClient

logger = logging.getLogger(__name__)

router = APIRouter()


def get_uslegalpro_api_client() -> USLegalProApiClient:
    return USLegalProApiClient()


@router.post(
    "/{question_code}/options",
    response_model=QuestionOptionsResponse,
    responses={
        400: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
    },
    summary="Resolve question dropdown options",
    description=(
        "Returns options for a question: static values from the ingested lookup list, "
        "or dynamic values from the US Legal Pro API when validation_rules.options_source is api."
    ),
)
@limiter.limit(rate_limit_string)
async def post_question_options(
    request: Request,
    question_code: str,
    body: QuestionOptionsRequest,
    _auth: Optional[Any] = Depends(get_optional_auth),
    api_client: USLegalProApiClient = Depends(get_uslegalpro_api_client),
    rds: RDSRepository = Depends(require_rds_repo),
):
    return await _resolve_and_respond(question_code, body.answers, api_client, rds)


@router.get(
    "/{question_code}/options",
    response_model=QuestionOptionsResponse,
    responses={
        400: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
    },
    summary="Resolve question dropdown options (GET)",
    description="Same as POST; pass parent answers as JSON in the `context` query parameter.",
)
@limiter.limit(rate_limit_string)
async def get_question_options(
    request: Request,
    question_code: str,
    context: Optional[str] = Query(
        None,
        description='JSON object of answers, e.g. {"JURISDICTION":"harris:dc"}',
    ),
    _auth: Optional[Any] = Depends(get_optional_auth),
    api_client: USLegalProApiClient = Depends(get_uslegalpro_api_client),
    rds: RDSRepository = Depends(require_rds_repo),
):
    answers: dict[str, str] = {}
    if context:
        try:
            parsed = json.loads(context)
            if not isinstance(parsed, dict):
                raise ValueError("context must be a JSON object")
            answers = {str(k): str(v) for k, v in parsed.items()}
        except (json.JSONDecodeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"Invalid context JSON: {exc}") from exc
    return await _resolve_and_respond(question_code, answers, api_client, rds)


async def _resolve_and_respond(
    question_code: str,
    answers: dict[str, str],
    api_client: USLegalProApiClient,
    rds: RDSRepository,
) -> QuestionOptionsResponse:
    try:
        source, options = await resolve_question_options(
            question_code,
            answers,
            rds=rds,
            api_client=api_client,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except httpx.HTTPStatusError as exc:
        logger.warning("Upstream US Legal Pro API failed for %s: %s", question_code, exc)
        raise HTTPException(
            status_code=502,
            detail=f"US Legal Pro API error: {exc.response.status_code}",
        ) from exc
    except httpx.HTTPError as exc:
        logger.warning("Upstream US Legal Pro API unreachable for %s: %s", question_code, exc)
        raise HTTPException(status_code=502, detail="US Legal Pro API unreachable") from exc

    return QuestionOptionsResponse(
        question_code=question_code.upper(),
        options_source=source,
        options=options,
    )
