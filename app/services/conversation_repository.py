"""
RDS data access for conversation history and lifecycle.

SQL lives in ``app/config/sql_queries/conversations.yaml``.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.adapters.data_sources.AIM_rds import RDSRepository, RDSQueryError
from app.utils.sql_queries import get_sql

logger = logging.getLogger(__name__)

SQL_INSERT_CONVERSATION = get_sql("conversations.insert_conversation")
SQL_FETCH_BY_ID = get_sql("conversations.fetch_by_id")
SQL_FETCH_ACTIVE_BY_USER = get_sql("conversations.fetch_active_by_user")
SQL_FETCH_HISTORY = get_sql("conversations.fetch_history")
SQL_FETCH_HISTORY_AFTER = get_sql("conversations.fetch_history_after")
SQL_INSERT_USER_MESSAGE = get_sql("conversations.insert_user_message")
SQL_INSERT_AI_MESSAGE = get_sql("conversations.insert_ai_message")
SQL_INSERT_SYSTEM_MESSAGE = get_sql("conversations.insert_system_message")
SQL_COMPLETE_CONVERSATION = get_sql("conversations.complete_conversation")
SQL_INSERT_ATTACHMENT = get_sql("conversations.insert_attachment")
SQL_LIST_ATTACHMENTS = get_sql("conversations.list_attachments")
SQL_FETCH_USER_MESSAGES_BY_USER_ID = get_sql("conversations.fetch_user_messages_by_user_id")


class ConversationRepository:
    """RDS-backed conversation and message persistence."""

    def __init__(self, rds: Optional[RDSRepository] = None):
        self.rds = rds

    async def _safe_fetch(self, query: str, *params: Any) -> List[Dict[str, Any]]:
        if self.rds is None:
            logger.debug("RDS not configured; returning empty result")
            return []
        try:
            return await self.rds.fetch(query, *params)
        except RDSQueryError as exc:
            logger.warning("Conversation RDS query failed: %s", exc)
            return []
        except Exception as exc:  # noqa: BLE001
            logger.warning("Unexpected conversation RDS error: %s", exc)
            return []

    async def _safe_fetchrow(self, query: str, *params: Any) -> Optional[Dict[str, Any]]:
        rows = await self._safe_fetch(query, *params)
        return rows[0] if rows else None

    async def create_conversation(
        self,
        user_id: str,
        case_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        return await self._safe_fetchrow(
            SQL_INSERT_CONVERSATION, user_id, case_id, session_id
        )

    async def get_conversation(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        return await self._safe_fetchrow(SQL_FETCH_BY_ID, conversation_id)

    async def get_active_conversation(
        self, user_id: str, case_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        return await self._safe_fetchrow(SQL_FETCH_ACTIVE_BY_USER, user_id, case_id)

    async def get_history(
        self, conversation_id: str, limit: int = 500
    ) -> List[Dict[str, Any]]:
        return await self._safe_fetch(SQL_FETCH_HISTORY, conversation_id, limit)

    async def get_history_after(
        self, conversation_id: str, after_ts: Any, limit: int = 200
    ) -> List[Dict[str, Any]]:
        return await self._safe_fetch(
            SQL_FETCH_HISTORY_AFTER, conversation_id, after_ts, limit
        )

    async def insert_user_message(
        self, conversation_id: str, message: str
    ) -> Optional[Dict[str, Any]]:
        return await self._safe_fetchrow(
            SQL_INSERT_USER_MESSAGE, conversation_id, message
        )

    async def insert_ai_message(
        self, conversation_id: str, message: str
    ) -> Optional[Dict[str, Any]]:
        return await self._safe_fetchrow(SQL_INSERT_AI_MESSAGE, conversation_id, message)

    async def insert_system_message(
        self, conversation_id: str, message: str
    ) -> Optional[Dict[str, Any]]:
        return await self._safe_fetchrow(
            SQL_INSERT_SYSTEM_MESSAGE, conversation_id, message
        )

    async def complete_conversation(
        self, conversation_id: str
    ) -> Optional[Dict[str, Any]]:
        return await self._safe_fetchrow(SQL_COMPLETE_CONVERSATION, conversation_id)

    async def insert_attachment(
        self, conversation_id: str, document_id: str
    ) -> Optional[Dict[str, Any]]:
        return await self._safe_fetchrow(
            SQL_INSERT_ATTACHMENT, conversation_id, document_id
        )

    async def list_attachments(self, conversation_id: str) -> List[Dict[str, Any]]:
        return await self._safe_fetch(SQL_LIST_ATTACHMENTS, conversation_id)

    async def get_user_messages_by_user_id(
        self, user_id: str
    ) -> List[Dict[str, Any]]:
        return await self._safe_fetch(SQL_FETCH_USER_MESSAGES_BY_USER_ID, user_id)

    @staticmethod
    def history_to_llm_messages(
        rows: List[Dict[str, Any]], limit: int = 50
    ) -> List[Dict[str, str]]:
        """Map RDS message rows to LLM chat roles."""
        role_map = {"USER": "user", "AI": "assistant", "SYSTEM": "system"}
        messages: List[Dict[str, str]] = []
        for row in rows[-limit:]:
            sender = str(row.get("sender") or "").upper()
            content = str(row.get("message") or "").strip()
            if not content or content in ("[session_start]", "[workflow_start]"):
                continue
            role = role_map.get(sender, "user")
            messages.append({"role": role, "content": content})
        return messages
