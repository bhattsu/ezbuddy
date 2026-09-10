"""
WebSocket event schemas for the LLM-driven filing assistant.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field


class FilingMode(str, Enum):
    UNSET = "unset"
    GENERIC = "generic"
    FILING_NEW = "filing_new"
    FILING_EXISTING = "filing_existing"


class FilingPhase(str, Enum):
    GREETING = "greeting"
    INTENT_PENDING = "intent_pending"
    SELECTING_STATE = "selecting_state"
    SELECTING_COUNTY = "selecting_county"
    SELECTING_JURISDICTION = "selecting_jurisdiction"
    SELECTING_CASE_CATEGORY = "selecting_case_category"
    SELECTING_CASE_TYPE = "selecting_case_type"
    SELECTING_CASE_PARTIES = "selecting_case_parties"
    SELECTING_FILER_TYPE = "selecting_filer_type"
    SELECTING_FILING_CODE = "selecting_filing_code"
    # Tyler document_type_codes for the selected filing code (existing case).
    SELECTING_DOC_TYPE_CODE = "selecting_doc_type_code"
    SELECTING_DOCUMENT_TYPE = "selecting_document_type"
    SELECTING_FILING_TYPE = "selecting_filing_type"
    OFFERING_DOCUMENTS = "offering_documents"
    AWAITING_DOCUMENT_UPLOAD = "awaiting_document_upload"
    COLLECTING_WORKFLOW_ANSWERS = "collecting_workflow_answers"
    GENERATING_DOCUMENTS = "generating_documents"
    VERIFYING_PLATFORM_PAYMENT = "verifying_platform_payment"
    VERIFYING_COURT_PAYMENT = "verifying_court_payment"
    CONFIRMING_EFILE = "confirming_efile"
    EXISTING_LOOKUP_METHOD = "existing_lookup_method"
    EXISTING_SELECTING_STATE = "existing_selecting_state"
    EXISTING_SELECTING_JURISDICTION = "existing_selecting_jurisdiction"
    EXISTING_ENTER_CASE_NUMBER = "existing_enter_case_number"
    EXISTING_SEARCH_PARTY = "existing_search_party"
    EXISTING_SEARCH_DATE = "existing_search_date"
    EXISTING_CASE_CONFIRM = "existing_case_confirm"
    EXISTING_UPLOAD_ANALYSIS = "existing_upload_analysis"
    COMPLETE = "complete"


ChecklistStatus = Literal["pending", "answered", "skipped"]


class ChecklistItemModel(BaseModel):
    field_name: str
    label: str
    required: bool = True
    sort_order: int = 0
    status: ChecklistStatus = "pending"
    value: Optional[Any] = None


class ChecklistPayload(BaseModel):
    total: int = 0
    answered: int = 0
    pending: int = 0
    skipped: int = 0
    items: List[ChecklistItemModel] = Field(default_factory=list)


class SelectionOptionModel(BaseModel):
    label: str
    value: str
    code: Optional[str] = None


class SelectionOptionsPayload(BaseModel):
    phase: str
    type: Literal["dropdown", "text"] = "dropdown"
    prompt: str = ""
    total: int = 0
    options: List[SelectionOptionModel] = Field(default_factory=list)


class ChatTurnModel(BaseModel):
    request: str = ""
    response: str = ""


class AssistantMessagePayload(BaseModel):
    message: str
    phase: FilingPhase
    mode: FilingMode = FilingMode.UNSET
    selections: Dict[str, Any] = Field(default_factory=dict)
    collected_data: Dict[str, Any] = Field(default_factory=dict)
    checklist: Optional[ChecklistPayload] = None
    selection_options: Optional[SelectionOptionsPayload] = None
    chat_context: List[ChatTurnModel] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class SessionStartedPayload(BaseModel):
    message: str
    conversation_id: str
    user_id: str
    phase: FilingPhase = FilingPhase.GREETING
    mode: FilingMode = FilingMode.UNSET
    history: List[Dict[str, Any]] = Field(default_factory=list)
    chat_context: List[ChatTurnModel] = Field(default_factory=list)
    selection_options: Optional[SelectionOptionsPayload] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class WorkflowCompletePayload(BaseModel):
    conversation_id: str
    collected_data: Dict[str, Any] = Field(default_factory=dict)
    selections: Dict[str, Any] = Field(default_factory=dict)
    checklist: Optional[ChecklistPayload] = None


class CaseLocatedPayload(BaseModel):
    conversation_id: str
    case_metadata: Dict[str, Any] = Field(default_factory=dict)
    selections: Dict[str, Any] = Field(default_factory=dict)


class AnalysisCompletePayload(BaseModel):
    conversation_id: str
    analysis: Dict[str, Any] = Field(default_factory=dict)
    message: str = ""
    prefilled_fields: Dict[str, Any] = Field(default_factory=dict)
    checklist: Optional[ChecklistPayload] = None
    files: List[Dict[str, Any]] = Field(default_factory=list)


class DocumentsOfferPayload(BaseModel):
    conversation_id: str
    message: str
    phase: FilingPhase
    required_documents: List[Dict[str, Any]] = Field(default_factory=list)
    checklist: Optional[ChecklistPayload] = None


class GeneratedDocumentPayload(BaseModel):
    template_code: str = ""
    template_name: str = ""
    file_name: str = ""
    html_content: Optional[str] = None
    ftl_content: Optional[str] = None
    download_url: Optional[str] = None
    skipped_because_uploaded: bool = False
    error: Optional[str] = None


class DocumentsReadyPayload(BaseModel):
    conversation_id: str
    message: str = ""
    collected_data: Dict[str, Any] = Field(default_factory=dict)
    selections: Dict[str, Any] = Field(default_factory=dict)
    checklist: Optional[ChecklistPayload] = None
    required_documents: List[Dict[str, Any]] = Field(default_factory=list)
    documents: List[GeneratedDocumentPayload] = Field(default_factory=list)


class ErrorPayload(BaseModel):
    code: str = "error"
    message: str
    detail: Optional[str] = None


class NotificationPayload(BaseModel):
    """Top-right toast for a filing process. Stays until the next notification."""

    message: str
    process: str
    level: Literal["info", "success", "error"] = "info"


# Client → server

class SessionInitEvent(BaseModel):
    type: Literal["session.init"] = "session.init"
    user_id: str
    case_id: Optional[str] = None
    conversation_id: Optional[str] = None


class UserMessageEvent(BaseModel):
    type: Literal["user.message"] = "user.message"
    conversation_id: str
    content: str = ""


class UploadFileItem(BaseModel):
    file_name: str
    content_base64: str


class UserUploadEvent(BaseModel):
    type: Literal["user.upload"] = "user.upload"
    conversation_id: str
    file_name: str = ""
    content_base64: str = ""
    files: List[UploadFileItem] = Field(default_factory=list)

    def iter_files(self) -> List[UploadFileItem]:
        if self.files:
            return list(self.files)
        if self.file_name and self.content_base64:
            return [
                UploadFileItem(
                    file_name=self.file_name,
                    content_base64=self.content_base64,
                )
            ]
        return []


ClientEvent = Union[SessionInitEvent, UserMessageEvent, UserUploadEvent]


def parse_client_event(data: Dict[str, Any]) -> ClientEvent:
    event_type = data.get("type")
    if event_type == "session.init":
        return SessionInitEvent.model_validate(data)
    if event_type == "user.message":
        return UserMessageEvent.model_validate(data)
    if event_type == "user.upload":
        return UserUploadEvent.model_validate(data)
    raise ValueError(f"Unknown client event type: {event_type!r}")


def server_event(
    event_type: str,
    conversation_id: Optional[str] = None,
    payload: Optional[BaseModel | Dict[str, Any]] = None,
) -> Dict[str, Any]:
    body: Dict[str, Any] = {"type": event_type}
    if conversation_id:
        body["conversation_id"] = conversation_id
    if payload is not None:
        if isinstance(payload, BaseModel):
            body["payload"] = payload.model_dump(mode="json")
        else:
            body["payload"] = payload
    return body
