"""Conditional routing for the filing LangGraph."""

from __future__ import annotations

from typing import Literal

from app.agents.conversation.orchestration.state import FilingGraphState
from app.api.schemas.filing_events import FilingPhase

_OFFER_PHASES = {
    FilingPhase.OFFERING_DOCUMENTS.value,
    FilingPhase.AWAITING_DOCUMENT_UPLOAD.value,
}


def route_entry(state: FilingGraphState) -> Literal["connect", "message_prepare", "upload"]:
    trigger = state.get("trigger") or "message"
    if trigger == "connect":
        return "connect"
    if trigger == "upload":
        return "upload"
    return "message_prepare"


def route_after_message_prepare(
    state: FilingGraphState,
) -> Literal["offer_documents", "workflow", "verify_payment", "navigation"]:
    phase = state.get("phase") or ""
    if phase in _OFFER_PHASES:
        return "offer_documents"
    if phase == FilingPhase.COLLECTING_WORKFLOW_ANSWERS.value:
        return "workflow"
    if phase in {
        FilingPhase.VERIFYING_PLATFORM_PAYMENT.value,
        FilingPhase.VERIFYING_COURT_PAYMENT.value,
    }:
        return "verify_payment"
    return "navigation"


def route_after_navigation(
    state: FilingGraphState,
) -> Literal["init_workflow", "workflow", "case_located", "persist"]:
    next_node = state.get("next_node") or "persist"
    if next_node == "init_workflow":
        return "init_workflow"
    if next_node == "workflow":
        return "workflow"
    if next_node == "case_located":
        return "case_located"
    return "persist"


def route_after_init_workflow(
    state: FilingGraphState,
) -> Literal["offer_documents", "persist"]:
    if state.get("next_node") == "offer_documents":
        return "offer_documents"
    return "persist"


def route_after_connect(
    state: FilingGraphState,
) -> Literal["navigation", "end"]:
    if state.get("next_node") == "done":
        return "end"
    return "navigation"


def route_after_offer(
    state: FilingGraphState,
) -> Literal["workflow", "generate_documents", "persist"]:
    next_node = state.get("next_node") or "persist"
    if next_node == "workflow":
        return "workflow"
    if next_node == "generate_documents":
        return "generate_documents"
    return "persist"


def route_after_upload(
    state: FilingGraphState,
) -> Literal["analyze_and_prefill", "persist"]:
    if state.get("next_node") == "analyze_and_prefill":
        return "analyze_and_prefill"
    return "persist"


def route_after_analyze(
    state: FilingGraphState,
) -> Literal["generate_documents", "persist"]:
    if state.get("next_node") == "generate_documents":
        return "generate_documents"
    return "persist"


def route_after_workflow(
    state: FilingGraphState,
) -> Literal["generate_documents", "persist"]:
    if state.get("next_node") == "generate_documents":
        return "generate_documents"
    return "persist"
