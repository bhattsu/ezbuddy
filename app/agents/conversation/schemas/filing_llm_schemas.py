"""Pydantic schemas for structured LLM outputs in the filing assistant."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


NavigationIntent = Literal["generic_legal", "filing_new", "filing_existing", "continue"]

LookupAction = Literal["party_search", "date_search", "case_number", "confirm_case"]

ChecklistStatus = Literal["pending", "answered", "skipped"]


class FilingNavigationOutput(BaseModel):
    """Structured output for FilingAssistantAgent (navigation / intent)."""

    intent: NavigationIntent = "continue"
    assistant_message: str = Field(
        default="I'm here to help with your court filing. How can I assist you today?"
    )
    selections_update: Dict[str, Any] = Field(default_factory=dict)
    lookup_action: Optional[LookupAction] = None
    lookup_params: Dict[str, Any] = Field(default_factory=dict)
    phase_complete: bool = False


class WorkflowMatchOutput(BaseModel):
    """Strict LLM confirmation of an API selection to DB workflow mapping."""

    matched: bool = False
    workflow_id: Optional[str] = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str = ""


class ChecklistUpdateItem(BaseModel):
    """Single checklist row update from WorkflowQuestionsAgent."""

    field_name: str
    status: ChecklistStatus
    value: Optional[Any] = None


class WorkflowQuestionOutput(BaseModel):
    """Structured output for WorkflowQuestionsAgent (workflow Q&A)."""

    assistant_message: str = Field(
        default="Could you provide that information again?"
    )
    answers_update: Dict[str, Any] = Field(default_factory=dict)
    checklist_updates: List[ChecklistUpdateItem] = Field(default_factory=list)
    skipped_fields: List[str] = Field(
        default_factory=list,
        description="field_name values that no longer apply and should not be asked.",
    )
    workflow_complete: bool = False
