"""
Thin service facade for the LLM filing assistant.

Delegates to FilingOrchestratorAgent and maps results to WebSocket events.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional

from app.agents.conversation.filing_orchestrator_agent import (
    FilingOrchestratorAgent,
    OrchestratorResult,
)
from app.api.schemas.filing_events import (
    AnalysisCompletePayload,
    AssistantMessagePayload,
    CaseLocatedPayload,
    DocumentsOfferPayload,
    DocumentsReadyPayload,
    ErrorPayload,
    GeneratedDocumentPayload,
    NotificationPayload,
    SelectionOptionsPayload,
    SessionStartedPayload,
    WorkflowCompletePayload,
    server_event,
)
from app.services.conversation_repository import ConversationRepository
from app.services.legal_filing_repository import LegalFilingRepository
from app.services.process_notifications import merge_notifications, notifications_for_result

logger = logging.getLogger(__name__)


class FilingAssistantService:
    """WebSocket-facing filing assistant facade."""

    def __init__(
        self,
        filing_repo: Optional[LegalFilingRepository] = None,
        conversation_repo: Optional[ConversationRepository] = None,
        orchestrator: Optional[FilingOrchestratorAgent] = None,
    ):
        self.filing_repo = filing_repo or LegalFilingRepository()
        self.conversation_repo = conversation_repo or ConversationRepository()
        self.orchestrator = orchestrator or FilingOrchestratorAgent(
            filing_repo=self.filing_repo,
            conversation_repo=self.conversation_repo,
        )

    def _selection_options(
        self, metadata: Dict[str, Any]
    ) -> Optional[SelectionOptionsPayload]:
        raw = metadata.get("selection_options")
        if not raw:
            return None
        if isinstance(raw, SelectionOptionsPayload):
            return raw
        return SelectionOptionsPayload.model_validate(raw)

    def _chat_context(self, result: OrchestratorResult) -> List[Dict[str, Any]]:
        raw = (result.metadata or {}).get("chat_context") or []
        if not isinstance(raw, list):
            return []
        return [
            {
                "request": str(item.get("request") or ""),
                "response": str(item.get("response") or ""),
            }
            for item in raw
            if isinstance(item, dict)
        ]

    def _map_result(self, result: OrchestratorResult) -> Dict[str, Any]:
        cid = result.conversation_id
        kind = result.event_kind

        if kind == "session.started":
            payload = SessionStartedPayload(
                message=result.assistant_message,
                conversation_id=cid,
                user_id=result.metadata.get("user_id", ""),
                phase=result.phase,
                mode=result.mode,
                chat_context=self._chat_context(result),
                selection_options=self._selection_options(result.metadata),
                checklist=result.checklist,
                metadata=result.metadata,
            )
            return server_event("session.started", cid, payload)

        if kind == "workflow.complete":
            payload = WorkflowCompletePayload(
                conversation_id=cid,
                collected_data=result.collected_answers,
                selections=result.selections,
                checklist=result.checklist,
            )
            return server_event("workflow.complete", cid, payload)

        if kind == "case.located":
            payload = CaseLocatedPayload(
                conversation_id=cid,
                case_metadata=result.metadata.get("case_metadata")
                or result.selections.get("case_metadata")
                or {},
                selections=result.selections,
            )
            return server_event("case.located", cid, payload)

        if kind == "analysis.complete":
            payload = AnalysisCompletePayload(
                conversation_id=cid,
                analysis=result.analysis or {},
                message=result.assistant_message,
                prefilled_fields=result.metadata.get("prefilled_fields") or {},
                checklist=result.checklist,
                files=result.metadata.get("files") or [],
            )
            return server_event("analysis.complete", cid, payload)

        if kind == "documents.offer":
            payload = DocumentsOfferPayload(
                conversation_id=cid,
                message=result.assistant_message,
                phase=result.phase,
                required_documents=result.metadata.get("required_documents") or [],
                checklist=result.checklist,
            )
            return server_event("documents.offer", cid, payload)

        if kind == "documents.ready":
            raw_docs = result.metadata.get("generated_documents") or []
            documents = [
                GeneratedDocumentPayload(
                    template_code=d.get("template_code") or "",
                    template_name=d.get("template_name") or "",
                    file_name=d.get("file_name") or "",
                    html_content=d.get("html_content"),
                    ftl_content=d.get("ftl_content"),
                    download_url=d.get("download_url") or d.get("s3_url") or d.get("file_url"),
                    skipped_because_uploaded=bool(d.get("skipped_because_uploaded")),
                    error=d.get("error"),
                )
                for d in raw_docs
            ]
            payload = DocumentsReadyPayload(
                conversation_id=cid,
                message=result.assistant_message,
                collected_data=result.collected_answers,
                selections=result.selections,
                checklist=result.checklist,
                required_documents=result.metadata.get("required_documents") or [],
                documents=documents,
            )
            return server_event("documents.ready", cid, payload)

        payload = AssistantMessagePayload(
            message=result.assistant_message,
            phase=result.phase,
            mode=result.mode,
            selections=result.selections,
            collected_data=result.collected_answers,
            checklist=result.checklist,
            selection_options=self._selection_options(result.metadata),
            chat_context=self._chat_context(result),
            metadata=result.metadata,
        )
        return server_event("assistant.message", cid, payload)

    @staticmethod
    def notification_event(
        conversation_id: Optional[str], item: Dict[str, Any]
    ) -> Dict[str, Any]:
        level = item.get("level") if item.get("level") in ("info", "success", "error") else "info"
        return server_event(
            "notification",
            conversation_id=conversation_id,
            payload=NotificationPayload(
                message=str(item.get("message") or "Update"),
                process=str(item.get("process") or "update"),
                level=level,
            ),
        )

    def events_from_result(self, result: OrchestratorResult) -> List[Dict[str, Any]]:
        notes = merge_notifications(
            result.notifications,
            notifications_for_result(
                event_kind=result.event_kind,
                phase=result.phase,
                assistant_message=result.assistant_message,
                mode=result.mode,
            ),
        )
        events = [
            self.notification_event(result.conversation_id, item) for item in notes
        ]
        events.append(self._map_result(result))
        return events

    @staticmethod
    def error_event(
        message: str,
        detail: Optional[str] = None,
        conversation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        return server_event(
            "error",
            conversation_id=conversation_id,
            payload=ErrorPayload(message=message, detail=detail),
        )

    async def on_connect(
        self,
        user_id: str,
        conversation_id: Optional[str] = None,
        case_id: Optional[str] = None,
        on_notification: Optional[Callable[[Dict[str, Any]], Awaitable[None] | None]] = None,
    ) -> List[Dict[str, Any]]:
        try:
            result = await self.orchestrator.on_connect(
                user_id=user_id,
                conversation_id=conversation_id,
                case_id=case_id,
                on_notification=on_notification,
            )
            result.metadata["user_id"] = user_id
            return self.events_from_result(result)
        except Exception as exc:  # noqa: BLE001
            logger.error("on_connect failed: %s", exc, exc_info=True)
            return [
                self.notification_event(
                    None,
                    {"process": "error", "message": str(exc), "level": "error"},
                ),
                self.error_event(str(exc)),
            ]

    async def handle_user_message(
        self,
        conversation_id: str,
        content: str,
        on_notification: Optional[Callable[[Dict[str, Any]], Awaitable[None] | None]] = None,
    ) -> List[Dict[str, Any]]:
        try:
            result = await self.orchestrator.handle_user_message(
                conversation_id,
                content,
                on_notification=on_notification,
            )
            return self.events_from_result(result)
        except Exception as exc:  # noqa: BLE001
            logger.error("handle_user_message failed: %s", exc, exc_info=True)
            return [
                self.notification_event(
                    conversation_id,
                    {"process": "error", "message": str(exc), "level": "error"},
                ),
                self.error_event(str(exc), conversation_id=conversation_id),
            ]

    async def handle_user_upload(
        self,
        conversation_id: str,
        file_bytes: bytes = b"",
        file_name: str = "",
        file_path: str = "",
        file_type: Any = None,
        files: Optional[List[Dict[str, Any]]] = None,
        on_notification: Optional[Callable[[Dict[str, Any]], Awaitable[None] | None]] = None,
    ) -> List[Dict[str, Any]]:
        try:
            result = await self.orchestrator.handle_user_upload(
                conversation_id,
                file_bytes,
                file_name,
                file_path,
                file_type,
                files=files,
                on_notification=on_notification,
            )
            events = self.events_from_result(result)
            if result.event_kind == "analysis.complete":
                events.append(
                    self._map_result(
                        OrchestratorResult(
                            assistant_message=result.assistant_message,
                            conversation_id=result.conversation_id,
                            phase=result.phase,
                            mode=result.mode,
                            selections=result.selections,
                            collected_answers=result.collected_answers,
                            checklist=result.checklist,
                            event_kind="assistant.message",
                            analysis=result.analysis,
                            metadata=result.metadata,
                        )
                    )
                )
            return events
        except Exception as exc:  # noqa: BLE001
            logger.error("handle_user_upload failed: %s", exc, exc_info=True)
            return [
                self.notification_event(
                    conversation_id,
                    {"process": "error", "message": str(exc), "level": "error"},
                ),
                self.error_event(str(exc)),
            ]
