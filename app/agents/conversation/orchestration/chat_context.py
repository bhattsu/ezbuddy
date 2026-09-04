"""Per-session request/response cache for filing chat context."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.agents.conversation.orchestration.state import FilingSession

CHAT_CONTEXT_LIMIT = 40
MAX_TURN_CHARS = 4000
_INTERNAL_REQUESTS = frozenset(
    {
        "[session_start]",
        "[workflow_start]",
        "[document_offer]",
    }
)


def _clip(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) <= MAX_TURN_CHARS:
        return text
    return text[: MAX_TURN_CHARS - 3] + "..."


def append_chat_turn(
    session: FilingSession,
    request: str = "",
    response: str = "",
) -> None:
    """Cache one user request and assistant response on the session."""
    req = _clip(request)
    res = _clip(response)
    if req in _INTERNAL_REQUESTS:
        req = ""
    if not req and not res:
        return
    turns = list(session.chat_context or [])
    if (
        turns
        and turns[-1].get("request") == req
        and turns[-1].get("response") == res
    ):
        return
    if turns and req and not turns[-1].get("response") and turns[-1].get("request") == req:
        turns[-1]["response"] = res
    else:
        turns.append({"request": req, "response": res})
    session.chat_context = turns[-CHAT_CONTEXT_LIMIT:]


def messages_to_turns(
    messages: List[Dict[str, str]],
    *,
    drop_trailing_user: bool = True,
) -> List[Dict[str, str]]:
    turns: List[Dict[str, str]] = []
    pending_request = ""
    for message in messages or []:
        role = str(message.get("role") or "").strip().lower()
        content = _clip(message.get("content"))
        if not content:
            continue
        if content in _INTERNAL_REQUESTS:
            continue
        if role == "user":
            if pending_request:
                turns.append({"request": pending_request, "response": ""})
            pending_request = content
            continue
        if role == "assistant":
            turns.append({"request": pending_request, "response": content})
            pending_request = ""
    if pending_request and not drop_trailing_user:
        turns.append({"request": pending_request, "response": ""})
    return turns[-CHAT_CONTEXT_LIMIT:]


def hydrate_chat_context(
    session: FilingSession, messages: List[Dict[str, str]]
) -> None:
    if session.chat_context:
        return
    session.chat_context = messages_to_turns(messages, drop_trailing_user=True)


def chat_context_as_messages(session: FilingSession) -> List[Dict[str, str]]:
    messages: List[Dict[str, str]] = []
    for turn in session.chat_context or []:
        request = str(turn.get("request") or "").strip()
        response = str(turn.get("response") or "").strip()
        if request:
            messages.append({"role": "user", "content": request})
        if response:
            messages.append({"role": "assistant", "content": response})
    return messages


def history_for_llm(
    session: Optional[FilingSession],
    fallback: Optional[List[Dict[str, str]]] = None,
) -> List[Dict[str, str]]:
    """Prefer cached request/response turns, else RDS/graph history."""
    if session and session.chat_context:
        return chat_context_as_messages(session)
    return list(fallback or [])


def format_request_response_history(history: List[Dict[str, str]]) -> str:
    """Render chat history as REQUEST/RESPONSE turns for the LLM."""
    turns = messages_to_turns(history, drop_trailing_user=True)
    if not turns:
        return "(no prior messages)"
    blocks: List[str] = []
    for index, turn in enumerate(turns, start=1):
        request = str(turn.get("request") or "").strip()
        response = str(turn.get("response") or "").strip()
        lines = [f"Turn {index}"]
        if request:
            lines.append(f"REQUEST: {request}")
        if response:
            lines.append(f"RESPONSE: {response}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)
