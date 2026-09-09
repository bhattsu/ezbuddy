"""Conversation history endpoints."""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, Request

from app.api.dependencies.rate_limit import limiter, rate_limit_string
from app.api.schemas.conversations import (
    UserMessageItem,
    UserMessagesRequest,
    UserMessagesResponse,
)
from app.api.schemas.rag import ErrorResponse
from app.services.conversation_repository import ConversationRepository
from app.services.operational_user_repository import OperationalUserRepository

logger = logging.getLogger(__name__)

router = APIRouter()


def _row_to_message_item(row: Dict[str, Any]) -> UserMessageItem:
    return UserMessageItem(
        message_id=row["message_id"],
        conversation_id=row["conversation_id"],
        message=row["message"],
        created_at=row["created_at"],
        conversation_session_id=row.get("conversation_session_id"),
        conversation_status=row.get("conversation_status"),
    )


@router.post(
    "/user-messages",
    response_model=UserMessagesResponse,
    responses={404: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
    summary="Get user messages by email and session",
    description=(
        "Looks up the user in operational.users by email and session_id, then returns "
        "all USER-sent messages from the conversations schema."
    ),
)
@limiter.limit(rate_limit_string)
async def get_user_messages(request: Request, body: UserMessagesRequest):
    from app.services.aim_factory import get_or_create_rds_repo

    try:
        rds = await get_or_create_rds_repo(request.app)
    except Exception as exc:  # noqa: BLE001
        logger.error("RDS unavailable for user messages: %s", exc)
        raise HTTPException(status_code=503, detail="Database unavailable") from exc

    if rds is None:
        raise HTTPException(status_code=503, detail="Database not configured")

    user_repo = OperationalUserRepository(rds=rds)
    conversation_repo = ConversationRepository(rds=rds)

    user = await user_repo.get_by_email_and_session(body.email, body.session_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found for email and session_id")

    rows = await conversation_repo.get_user_messages_by_user_id(str(user["user_id"]))
    messages: List[UserMessageItem] = [_row_to_message_item(row) for row in rows]

    return UserMessagesResponse(
        user_id=user["user_id"],
        email=user["email"],
        session_id=user["session_id"],
        messages=messages,
    )
