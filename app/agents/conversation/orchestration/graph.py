"""Build and compile the filing assistant LangGraph."""

from __future__ import annotations

from typing import Any, Optional

from langgraph.graph import END, StateGraph

from app.agents.conversation.orchestration.context import FilingOrchestratorContext
from app.agents.conversation.orchestration.nodes import build_nodes
from app.agents.conversation.orchestration.router import (
    route_after_analyze,
    route_after_connect,
    route_after_init_workflow,
    route_after_message_prepare,
    route_after_navigation,
    route_after_offer,
    route_after_upload,
    route_after_workflow,
    route_entry,
)
from app.agents.conversation.orchestration.state import FilingGraphState

_compiled_graph: Optional[Any] = None
_graph_context: Optional[FilingOrchestratorContext] = None


def build_filing_graph(ctx: FilingOrchestratorContext):
    """Compile the single filing orchestration StateGraph."""
    nodes = build_nodes(ctx)
    graph = StateGraph(FilingGraphState)

    graph.add_node("connect", nodes["connect"])
    graph.add_node("message_prepare", nodes["message_prepare"])
    graph.add_node("upload", nodes["upload"])
    graph.add_node("navigation", nodes["navigation"])
    graph.add_node("init_workflow", nodes["init_workflow"])
    graph.add_node("offer_documents", nodes["offer_documents"])
    graph.add_node("analyze_and_prefill", nodes["analyze_and_prefill"])
    graph.add_node("workflow", nodes["workflow"])
    graph.add_node("generate_documents", nodes["generate_documents"])
    graph.add_node("verify_payment", nodes["verify_payment"])
    graph.add_node("persist", nodes["persist"])

    graph.set_conditional_entry_point(
        route_entry,
        {
            "connect": "connect",
            "message_prepare": "message_prepare",
            "upload": "upload",
        },
    )

    graph.add_conditional_edges(
        "connect",
        route_after_connect,
        {
            "navigation": "navigation",
            "end": END,
        },
    )

    graph.add_conditional_edges(
        "message_prepare",
        route_after_message_prepare,
        {
            "offer_documents": "offer_documents",
            "workflow": "workflow",
            "verify_payment": "verify_payment",
            "navigation": "navigation",
        },
    )

    graph.add_conditional_edges(
        "navigation",
        route_after_navigation,
        {
            "init_workflow": "init_workflow",
            "offer_documents": "offer_documents",
            "workflow": "workflow",
            "case_located": "persist",
            "persist": "persist",
        },
    )

    graph.add_conditional_edges(
        "init_workflow",
        route_after_init_workflow,
        {
            "offer_documents": "offer_documents",
            "persist": "persist",
        },
    )

    graph.add_conditional_edges(
        "offer_documents",
        route_after_offer,
        {
            "workflow": "workflow",
            "generate_documents": "generate_documents",
            "persist": "persist",
        },
    )

    graph.add_conditional_edges(
        "upload",
        route_after_upload,
        {
            "analyze_and_prefill": "analyze_and_prefill",
            "persist": "persist",
        },
    )

    graph.add_conditional_edges(
        "analyze_and_prefill",
        route_after_analyze,
        {
            "generate_documents": "generate_documents",
            "persist": "persist",
        },
    )

    graph.add_conditional_edges(
        "workflow",
        route_after_workflow,
        {
            "generate_documents": "generate_documents",
            "persist": "persist",
        },
    )

    graph.add_edge("generate_documents", "persist")
    graph.add_edge("verify_payment", "persist")
    graph.add_edge("persist", END)

    return graph.compile()


def get_filing_graph(ctx: FilingOrchestratorContext):
    """Return a cached compiled graph for the given context (rebuild if context changes)."""
    global _compiled_graph, _graph_context
    if _compiled_graph is None or _graph_context is not ctx:
        _compiled_graph = build_filing_graph(ctx)
        _graph_context = ctx
    return _compiled_graph
