"""Pydantic schemas for structured LLM outputs in the filing assistant."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


NavigationIntent = Literal["generic_legal", "filing_new", "filing_existing", "continue"]

NavigationIntentRoute = Literal[
    "filing_new",
    "filing_existing",
    "generic_legal",
    "check_status",
    "unclear",
]

LookupAction = Literal[
    "party_search",
    "date_search",
    "case_number",
    "confirm_case",
    "check_status",
]

ChecklistStatus = Literal["pending", "answered", "skipped"]


class NavigationIntentClassificationOutput(BaseModel):
    """Short LLM routing at intent_pending (free text, no regex)."""

    intent: NavigationIntentRoute = Field(
        default="unclear",
        description=(
            "filing_new: start a new court case; filing_existing: look up an "
            "existing case to file into; check_status: track a prior e-filing "
            "submission; generic_legal: substantive legal question not asking "
            "to file; unclear: wizard help or ambiguous."
        ),
    )


class FilingNavigationOutput(BaseModel):
    """Structured output for FilingAssistantAgent (navigation / intent)."""

    intent: NavigationIntent = "continue"
    assistant_message: str = Field(
        default="I'm here to help with your court filing. How can I assist you today?"
    )
    selections_update: Dict[str, Any] = Field(default_factory=dict)
    lookup_action: Optional[LookupAction] = Field(
        default=None,
        description=(
            "Set to check_status when the user asks about the outcome of a prior "
            "e-filing submission (envelope status). Do not use filing_existing for "
            "status inquiries."
        ),
    )
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
