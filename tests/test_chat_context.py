"""Tests for session request/response chat context cache."""

from app.agents.conversation.orchestration.chat_context import (
    append_chat_turn,
    format_request_response_history,
    history_for_llm,
    hydrate_chat_context,
    messages_to_turns,
)
from app.agents.conversation.orchestration.helpers import restore_from_system_messages
from app.agents.conversation.orchestration.state import FilingSession
from app.api.schemas.filing_events import FilingPhase
import json


def test_append_chat_turn_caches_request_and_response():
    session = FilingSession(conversation_id="c1", user_id="u1")
    append_chat_turn(session, "Texas", "You selected Texas.")
    append_chat_turn(session, "new case", "Please choose a court.")
    assert session.chat_context == [
        {"request": "Texas", "response": "You selected Texas."},
        {"request": "new case", "response": "Please choose a court."},
    ]


def test_internal_requests_are_not_cached():
    session = FilingSession(conversation_id="c1", user_id="u1")
    append_chat_turn(session, "[session_start]", "Welcome.")
    assert session.chat_context == [{"request": "", "response": "Welcome."}]


def test_history_for_llm_prefers_cached_turns():
    session = FilingSession(conversation_id="c1", user_id="u1")
    append_chat_turn(session, "Texas", "You selected Texas.")
    history = history_for_llm(
        session, [{"role": "user", "content": "ignored older db row"}]
    )
    assert history == [
        {"role": "user", "content": "Texas"},
        {"role": "assistant", "content": "You selected Texas."},
    ]


def test_hydrate_from_db_messages():
    session = FilingSession(conversation_id="c1", user_id="u1")
    hydrate_chat_context(
        session,
        [
            {"role": "user", "content": "Texas"},
            {"role": "assistant", "content": "You selected Texas."},
            {"role": "user", "content": "current unanswered"},
        ],
    )
    assert session.chat_context == [
        {"request": "Texas", "response": "You selected Texas."}
    ]


def test_format_request_response_history():
    text = format_request_response_history(
        [
            {"role": "user", "content": "Texas"},
            {"role": "assistant", "content": "You selected Texas."},
        ]
    )
    assert "REQUEST: Texas" in text
    assert "RESPONSE: You selected Texas." in text


def test_restore_chat_context_from_system_snapshot():
    session = FilingSession(conversation_id="c1", user_id="u1")
    snapshot = {
        "filing_state": {
            "phase": FilingPhase.SELECTING_JURISDICTION.value,
            "mode": "filing_new",
            "selections": {"state_code": "TX"},
            "chat_context": [
                {"request": "Texas", "response": "You selected Texas."}
            ],
        }
    }
    restore_from_system_messages(
        [{"sender": "SYSTEM", "message": json.dumps(snapshot)}],
        session,
    )
    assert session.chat_context[0]["request"] == "Texas"
    assert session.phase == FilingPhase.SELECTING_JURISDICTION


def test_messages_to_turns_pairs_user_and_assistant():
    turns = messages_to_turns(
        [
            {"role": "assistant", "content": "Welcome"},
            {"role": "user", "content": "Texas"},
            {"role": "assistant", "content": "Got it"},
        ]
    )
    assert turns[0]["request"] == ""
    assert turns[0]["response"] == "Welcome"
    assert turns[1] == {"request": "Texas", "response": "Got it"}
