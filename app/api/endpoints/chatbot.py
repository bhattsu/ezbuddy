"""
Unified Chatbot Endpoint

- Legal filing assistant (WebSocket /ws only)
- Legacy RAG chatbot session + chat (POST /rag/chat, /session, ...)
"""

import base64
import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect

from app.api.dependencies import get_rag_components
from app.api.schemas.chatbot import (
    ChatRequest,
    ChatResponse,
    CreateSessionRequest,
    ErrorResponse,
    SessionListResponse,
    SessionResponse,
    UpdateSessionRequest,
)
from app.api.schemas.document import FileType
from app.api.schemas.filing_events import parse_client_event
from app.adapters.data_sources.AIM_rds import RDSRepository
from app.services.chatbot_service import ChatbotService
from app.services.conversation_repository import ConversationRepository
from app.services.filing_assistant_service import FilingAssistantService
from app.services.legal_filing_repository import LegalFilingRepository
from app.utils.file_utils import FileValidator, TempFileManager

logger = logging.getLogger(__name__)

router = APIRouter()


def _as_events(payload) -> list:
    if payload is None:
        return []
    if isinstance(payload, list):
        return payload
    return [payload]


def _conversation_id_from_events(events: list, fallback: Optional[str] = None) -> Optional[str]:
    for event in events:
        if not isinstance(event, dict):
            continue
        cid = event.get("conversation_id") or (event.get("payload") or {}).get(
            "conversation_id"
        )
        if cid:
            return str(cid)
    return fallback


async def _send_ws_events(websocket: WebSocket, events: list, emitted: Optional[set] = None) -> set:
    seen = emitted if emitted is not None else set()
    for event in _as_events(events):
        if event.get("type") == "notification":
            process = (event.get("payload") or {}).get("process")
            if process in seen:
                continue
            if process:
                seen.add(process)
        await websocket.send_json(event)
    return seen


def _get_chatbot_service(components: dict = Depends(get_rag_components)) -> ChatbotService:
    logger.debug("Initializing ChatbotService with components")
    return ChatbotService(
        llm_client=components["llm"],
        embedder=components["embedder"],
    )


def _build_filing_service(rds: Optional[RDSRepository]) -> FilingAssistantService:
    filing_repo = LegalFilingRepository(rds=rds)
    conversation_repo = ConversationRepository(rds=rds)
    return FilingAssistantService(
        filing_repo=filing_repo,
        conversation_repo=conversation_repo,
    )


# ============== Legal Filing Chatbot (WebSocket only) ==============


@router.websocket("/ws")
async def filing_chat_ws(websocket: WebSocket):
    """
    Real-time legal filing chat over WebSocket with typed events.

    Client must send ``session.init`` first::

        {"type": "session.init", "user_id": "<uuid>", "conversation_id": null}

    Then::

        {"type": "user.message", "conversation_id": "<uuid>", "content": "..."}
        {"type": "user.upload", "conversation_id": "<uuid>", "file_name": "...", "content_base64": "..."}
        {"type": "user.upload", "conversation_id": "<uuid>", "files": [{"file_name": "...", "content_base64": "..."}]}
    """
    await websocket.accept()
    logger.info("Filing chat WebSocket accepted from %s", websocket.client)

    from app.services.aim_factory import get_or_create_rds_repo

    try:
        rds = await get_or_create_rds_repo(websocket.app)
        service = _build_filing_service(rds)
    except Exception as exc:  # noqa: BLE001
        logger.error("Filing chat WS startup failed: %s", exc, exc_info=True)
        try:
            await websocket.send_json(
                FilingAssistantService.error_event(f"Failed to initialize: {exc}")
            )
            await websocket.close(code=1011)
        except Exception:  # noqa: BLE001
            pass
        return

    conversation_id: Optional[str] = None

    try:
        while True:
            message_event = await websocket.receive()
            if message_event.get("type") == "websocket.disconnect":
                break
            raw = message_event.get("text")
            if raw is None and message_event.get("bytes") is not None:
                raw = message_event["bytes"].decode("utf-8", errors="replace")
            if not raw:
                continue

            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json(
                    FilingAssistantService.error_event("Invalid JSON payload")
                )
                continue

            try:
                event = parse_client_event(data)
            except Exception as exc:  # noqa: BLE001
                await websocket.send_json(
                    FilingAssistantService.error_event(str(exc))
                )
                continue

            if event.type == "session.init":
                emitted: set = set()

                async def on_notification(item: dict) -> None:
                    process = item.get("process")
                    if process in emitted:
                        return
                    if process:
                        emitted.add(process)
                    await websocket.send_json(
                        FilingAssistantService.notification_event(
                            event.conversation_id or conversation_id, item
                        )
                    )

                events = await service.on_connect(
                    user_id=event.user_id,
                    conversation_id=event.conversation_id,
                    case_id=event.case_id,
                    on_notification=on_notification,
                )
                conversation_id = _conversation_id_from_events(events, conversation_id)
                await _send_ws_events(websocket, events, emitted)
                continue

            if not conversation_id and hasattr(event, "conversation_id"):
                conversation_id = event.conversation_id

            if event.type == "user.message":
                emitted = set()

                async def on_notification(item: dict) -> None:
                    process = item.get("process")
                    if process in emitted:
                        return
                    if process:
                        emitted.add(process)
                    await websocket.send_json(
                        FilingAssistantService.notification_event(
                            event.conversation_id or conversation_id, item
                        )
                    )

                events = await service.handle_user_message(
                    event.conversation_id, event.content, on_notification=on_notification
                )
                conversation_id = _conversation_id_from_events(
                    events, event.conversation_id or conversation_id
                )
                await _send_ws_events(websocket, events, emitted)
                continue

            if event.type == "user.upload":
                temp_paths: list[str] = []
                try:
                    items = event.iter_files()
                    if not items:
                        await websocket.send_json(
                            FilingAssistantService.error_event("No files were uploaded")
                        )
                        continue
                    uploads: list[dict] = []
                    for item in items:
                        file_bytes = base64.b64decode(item.content_base64)
                        if not file_bytes:
                            await websocket.send_json(
                                FilingAssistantService.error_event(
                                    f"Uploaded file is empty: {item.file_name}"
                                )
                            )
                            continue
                        file_type = FileValidator.detect_file_type_from_bytes(
                            file_bytes, item.file_name
                        )
                        if file_type not in (FileType.PDF, FileType.DOCX, FileType.WORD):
                            await websocket.send_json(
                                FilingAssistantService.error_event(
                                    f"Only PDF/DOCX uploads are supported (got {file_type})"
                                )
                            )
                            continue
                        temp_file_path = await TempFileManager.save_bytes_to_temp(
                            file_bytes, item.file_name, file_type
                        )
                        temp_paths.append(temp_file_path)
                        uploads.append(
                            {
                                "file_bytes": file_bytes,
                                "file_name": item.file_name,
                                "file_path": temp_file_path,
                                "file_type": file_type,
                            }
                        )
                    if not uploads:
                        continue
                    emitted = set()

                    async def on_notification(item: dict) -> None:
                        process = item.get("process")
                        if process in emitted:
                            return
                        if process:
                            emitted.add(process)
                        await websocket.send_json(
                            FilingAssistantService.notification_event(
                                event.conversation_id or conversation_id, item
                            )
                        )

                    first = uploads[0]
                    events = await service.handle_user_upload(
                        event.conversation_id,
                        first["file_bytes"],
                        first["file_name"],
                        first["file_path"],
                        first["file_type"],
                        files=uploads,
                        on_notification=on_notification,
                    )
                    await _send_ws_events(websocket, events, emitted)
                finally:
                    for path in temp_paths:
                        await TempFileManager.cleanup_temp_file(path)

    except WebSocketDisconnect:
        logger.info("Filing chat WebSocket disconnected (conversation=%s)", conversation_id)
    except Exception as exc:  # noqa: BLE001
        logger.error("Filing chat WebSocket error: %s", exc, exc_info=True)
        try:
            await websocket.send_json(
                FilingAssistantService.error_event(str(exc), conversation_id=conversation_id)
            )
            await websocket.close(code=1011)
        except Exception:  # noqa: BLE001
            pass


# ============== RAG Session Management Endpoints ==============


@router.post(
    "/session",
    response_model=SessionResponse,
    responses={500: {"model": ErrorResponse, "description": "Internal server error"}},
    summary="Create RAG Chat Session",
)
async def create_session(
    request: CreateSessionRequest,
    service: ChatbotService = Depends(_get_chatbot_service),
):
    try:
        return service.create_session(request)
    except Exception as e:
        logger.error("Failed to create session: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/session/{session_id}", response_model=SessionResponse, summary="Get RAG Session")
async def get_session(
    session_id: str,
    service: ChatbotService = Depends(_get_chatbot_service),
):
    session = service.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")
    return session


@router.patch("/session/{session_id}", response_model=SessionResponse, summary="Update RAG Session")
async def update_session(
    session_id: str,
    request: UpdateSessionRequest,
    service: ChatbotService = Depends(_get_chatbot_service),
):
    try:
        session = service.update_session(session_id, request)
        if not session:
            raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")
        return session
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to update session: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.delete("/session/{session_id}", summary="Delete RAG Session")
async def delete_session(
    session_id: str,
    service: ChatbotService = Depends(_get_chatbot_service),
):
    deleted = service.delete_session(session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")
    return {"message": f"Session {session_id} deleted successfully"}


@router.get("/sessions", response_model=SessionListResponse, summary="List RAG Sessions")
async def list_sessions(service: ChatbotService = Depends(_get_chatbot_service)):
    try:
        sessions = service.list_sessions()
        return SessionListResponse(sessions=sessions, total=len(sessions))
    except Exception as e:
        logger.error("Failed to list sessions: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/rag/chat", response_model=ChatResponse, summary="RAG Chat")
async def rag_chat(
    request: ChatRequest,
    service: ChatbotService = Depends(_get_chatbot_service),
):
    if not request.message and not request.query_image_base64:
        raise HTTPException(
            status_code=400,
            detail="Must provide either message or query_image_base64",
        )
    try:
        return await service.chat(request)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        logger.error("RAG chat failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e
