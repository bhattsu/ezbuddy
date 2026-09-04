"""Shared state types for the filing LangGraph orchestrator."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, TypedDict

from app.api.schemas.filing_events import (
    ChecklistItemModel,
    ChecklistPayload,
    FilingMode,
    FilingPhase,
)

ChecklistStatus = Literal["pending", "answered", "skipped"]
GraphTrigger = Literal["connect", "message", "upload"]


@dataclass
class ChecklistItem:
    field_name: str
    label: str
    required: bool = True
    sort_order: int = 0
    status: ChecklistStatus = "pending"
    value: Any = None
    visibility_condition: Any = None


@dataclass
class WorkflowChecklist:
    workflow_id: Optional[str] = None
    items: List[ChecklistItem] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.items)

    @property
    def answered_count(self) -> int:
        return sum(1 for i in self.items if i.status == "answered")

    @property
    def pending_count(self) -> int:
        return sum(1 for i in self.items if i.status == "pending")

    @property
    def skipped_count(self) -> int:
        return sum(1 for i in self.items if i.status == "skipped")

    def to_payload(self) -> ChecklistPayload:
        return ChecklistPayload(
            total=self.total,
            answered=self.answered_count,
            pending=self.pending_count,
            skipped=self.skipped_count,
            items=[
                ChecklistItemModel(
                    field_name=i.field_name,
                    label=i.label,
                    required=i.required,
                    sort_order=i.sort_order,
                    status=i.status,
                    value=i.value,
                )
                for i in self.items
            ],
        )


@dataclass
class FilingSession:
    conversation_id: str
    user_id: str
    mode: FilingMode = FilingMode.UNSET
    phase: FilingPhase = FilingPhase.GREETING
    selections: Dict[str, Any] = field(default_factory=dict)
    collected_answers: Dict[str, Any] = field(default_factory=dict)
    checklist: WorkflowChecklist = field(default_factory=WorkflowChecklist)
    workflow_questions: List[Dict[str, Any]] = field(default_factory=list)
    required_documents: List[Dict[str, Any]] = field(default_factory=list)
    uploaded_documents: List[Dict[str, Any]] = field(default_factory=list)
    generated_documents: List[Dict[str, Any]] = field(default_factory=list)
    chat_context: List[Dict[str, str]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class OrchestratorResult:
    assistant_message: str
    conversation_id: str
    phase: FilingPhase
    mode: FilingMode = FilingMode.UNSET
    selections: Dict[str, Any] = field(default_factory=dict)
    collected_answers: Dict[str, Any] = field(default_factory=dict)
    checklist: Optional[ChecklistPayload] = None
    event_kind: str = "assistant.message"
    metadata: Dict[str, Any] = field(default_factory=dict)
    analysis: Optional[Dict[str, Any]] = None
    notifications: List[Dict[str, Any]] = field(default_factory=list)


class FilingGraphState(TypedDict, total=False):
    """LangGraph state passed between filing orchestration nodes."""

    trigger: GraphTrigger
    conversation_id: str
    user_id: str
    case_id: Optional[str]
    user_message: str
    skip_user_persist: bool
    history: List[Dict[str, str]]
    # Upload fields
    file_bytes: bytes
    file_name: str
    file_path: str
    file_type: Any
    uploads: List[Dict[str, Any]]
    analysis_results: List[Dict[str, Any]]
    # Routing hints (set by nodes for conditional edges)
    next_node: str
    phase: str
    phase_before: str
    assistant_message: str
    # Output
    result: OrchestratorResult
    error: str
