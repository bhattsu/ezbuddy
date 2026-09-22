"""HTTP schemas for the deterministic filing dropdown flow (no LLM until questions)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.api.schemas.filing_events import (
    ChecklistPayload,
    FilingMode,
    FilingPhase,
    SelectionOptionsPayload,
)


class CreateFilingFlowSessionRequest(BaseModel):
    user_id: str = Field(..., description="Platform user UUID")
    case_id: Optional[str] = None


class FilingFlowSelectRequest(BaseModel):
    code: str = Field(..., description="Option code from the current step dropdown")


class FilingFlowStepResponse(BaseModel):
    conversation_id: str
    phase: FilingPhase
    mode: FilingMode = FilingMode.UNSET
    selections: Dict[str, Any] = Field(default_factory=dict)
    selection_options: Optional[SelectionOptionsPayload] = None
    template_questions_ready: bool = False
    workflow_questions_count: int = 0
    checklist: Optional[ChecklistPayload] = None
    message: str = ""


class FilingFlowSessionCreatedResponse(FilingFlowStepResponse):
    pass
