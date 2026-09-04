"""LangGraph-based filing conversation orchestrator."""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Dict, Optional

from app.agents.conversation.orchestration.context import FilingOrchestratorContext
from app.agents.conversation.orchestration.graph import get_filing_graph
from app.agents.conversation.orchestration.state import (
    FilingGraphState,
    OrchestratorResult,
)
from app.services.conversation_repository import ConversationRepository
from app.services.legal_filing_repository import LegalFilingRepository

# Re-export types used by services/tests
from app.agents.conversation.orchestration.session_manager import FilingSessionManager
from app.agents.conversation.orchestration.state import (
    ChecklistItem,
    FilingSession,
    WorkflowChecklist,
)

logger = logging.getLogger(__name__)

__all__ = [
    "ChecklistItem",
    "FilingOrchestratorAgent",
    "FilingSession",
    "FilingSessionManager",
    "OrchestratorResult",
    "WorkflowChecklist",
]


class FilingOrchestratorAgent:
    """Runs the filing assistant LangGraph for connect, message, and upload triggers."""

    def __init__(
        self,
        filing_repo: Optional[LegalFilingRepository] = None,
        conversation_repo: Optional[ConversationRepository] = None,
        context: Optional[FilingOrchestratorContext] = None,
        **context_kwargs: Any,
    ):
        if context is not None:
            self._ctx = context
        else:
            self._ctx = FilingOrchestratorContext.create(
                filing_repo=filing_repo,
                conversation_repo=conversation_repo,
                **context_kwargs,
            )
        self.filing_repo = self._ctx.filing_repo
        self.conversation_repo = self._ctx.conversation_repo
        self._graph = get_filing_graph(self._ctx)

    async def _run(
        self,
        state: FilingGraphState,
        on_notification: Optional[Callable[[Dict[str, Any]], Awaitable[None] | None]] = None,
    ) -> OrchestratorResult:
        self._ctx.notifications = []
        self._ctx.on_notification = on_notification
        try:
            final = await self._graph.ainvoke(state)
            result = final.get("result")
            if not isinstance(result, OrchestratorResult):
                raise RuntimeError("Filing graph completed without OrchestratorResult")
            from app.services.process_notifications import (
                merge_notifications,
                notifications_for_result,
            )

            result.notifications = merge_notifications(
                self._ctx.notifications,
                result.notifications,
            )
            result.notifications = merge_notifications(
                result.notifications,
                notifications_for_result(
                    event_kind=result.event_kind,
                    phase=result.phase,
                    assistant_message=result.assistant_message,
                    mode=result.mode,
                ),
            )
            return result
        finally:
            self._ctx.on_notification = None

    async def on_connect(
        self,
        user_id: str,
        conversation_id: Optional[str] = None,
        case_id: Optional[str] = None,
        on_notification: Optional[Callable[[Dict[str, Any]], Awaitable[None] | None]] = None,
    ) -> OrchestratorResult:
        return await self._run(
            {
                "trigger": "connect",
                "user_id": user_id,
                "conversation_id": conversation_id or "",
                "case_id": case_id,
            },
            on_notification=on_notification,
        )

    async def handle_user_message(
        self,
        conversation_id: str,
        content: str,
        on_notification: Optional[Callable[[Dict[str, Any]], Awaitable[None] | None]] = None,
    ) -> OrchestratorResult:
        return await self._run(
            {
                "trigger": "message",
                "conversation_id": conversation_id,
                "user_message": content,
            },
            on_notification=on_notification,
        )

    async def handle_user_upload(
        self,
        conversation_id: str,
        file_bytes: bytes = b"",
        file_name: str = "",
        file_path: str = "",
        file_type: Any = None,
        files: Optional[list] = None,
        on_notification: Optional[Callable[[Dict[str, Any]], Awaitable[None] | None]] = None,
    ) -> OrchestratorResult:
        uploads = [dict(item) for item in (files or [])]
        if file_bytes or file_name:
            single = {
                "file_bytes": file_bytes,
                "file_name": file_name or "document",
                "file_path": file_path,
                "file_type": file_type,
            }
            if not any(u.get("file_name") == single["file_name"] for u in uploads):
                uploads.insert(0, single)
        first = uploads[0] if uploads else {}
        return await self._run(
            {
                "trigger": "upload",
                "conversation_id": conversation_id,
                "file_bytes": first.get("file_bytes") or b"",
                "file_name": first.get("file_name") or "",
                "file_path": first.get("file_path") or "",
                "file_type": first.get("file_type"),
                "uploads": uploads,
            },
            on_notification=on_notification,
        )
