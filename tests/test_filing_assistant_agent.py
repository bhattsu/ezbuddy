"""Tests for navigation prompt and FilingAssistantAgent."""

from __future__ import annotations

from typing import Any, Dict

import pytest

from app.adapters.llm.bedrock import Bedrock
from app.agents.conversation.filing_assistant_agent import FilingAssistantAgent
from app.core.prompts.filing_assistant import (
    FILING_ASSISTANT_PROMPT,
    GREETING_USER_MESSAGE,
    NAVIGATION_OUTPUT_KEYS,
)


class _MockBedrock:
    def __init__(self, response: Dict[str, Any]):
        self._response = response
        self.last_prompt: str | None = None
        self.last_body: Dict[str, Any] | None = None

    async def invoke_structured_prompt(self, prompt, schema, timeout=None):
        self.last_prompt = prompt
        return schema.model_validate(self._response)

    async def invoke_prompt_with_timeout(self, prompt_body: Dict[str, Any], timeout=None):
        self.last_body = prompt_body
        return self._response

    @staticmethod
    def extract_text_field(llm_output: Dict[str, Any], *keys: str):
        return Bedrock.extract_text_field(llm_output, *keys)


@pytest.fixture
def sample_states():
    return [
        {"state_code": "TX", "state_name": "Texas"},
        {"state_code": "CA", "state_name": "California"},
    ]


def test_navigation_prompt_has_required_placeholders():
    for key in (
        "mode",
        "phase",
        "selections_json",
        "db_options_json",
        "options_summary",
        "history_text",
        "user_message",
    ):
        assert f"{{{key}}}" in FILING_ASSISTANT_PROMPT


def test_build_prompt_injects_context(sample_states):
    prompt = FilingAssistantAgent.build_prompt(
        mode="filing_new",
        phase="selecting_state",
        selections={"state_code": "TX"},
        db_options=sample_states,
        history=[{"role": "user", "content": "I need to file for divorce"}],
        user_message="Texas",
    )
    assert "selecting_state" in prompt
    assert "Texas" in prompt
    assert "TX" in prompt
    assert "state_code" in prompt
    assert "never invent" in prompt.lower() or "ONLY" in prompt
    assert "Texas" in prompt or "CA" in prompt


def test_build_prompt_greeting_phase():
    prompt = FilingAssistantAgent.build_prompt(
        mode="unset",
        phase="greeting",
        selections={},
        db_options=[],
        history=[],
        user_message=GREETING_USER_MESSAGE,
    )
    assert GREETING_USER_MESSAGE in prompt
    assert "intent_pending" in prompt or "greeting" in prompt


def test_normalize_output_fills_defaults():
    result = FilingAssistantAgent.normalize_output({})
    assert set(result.keys()) >= set(NAVIGATION_OUTPUT_KEYS)
    assert result["intent"] == "continue"
    assert result["phase_complete"] is False
    assert result["assistant_message"]


@pytest.mark.asyncio
async def test_run_parses_json_response(sample_states):
    llm_response = {
        "intent": "filing_new",
        "assistant_message": "Which state are you filing in?",
        "selections_update": {},
        "lookup_action": None,
        "lookup_params": {},
        "phase_complete": False,
    }
    bedrock = _MockBedrock(llm_response)
    agent = FilingAssistantAgent(bedrock=bedrock)

    result = await agent.run(
        mode="unset",
        phase="intent_pending",
        selections={},
        db_options=sample_states,
        history=[],
        user_message="I need to file for divorce",
    )

    assert result["intent"] == "filing_new"
    assert "state" in result["assistant_message"].lower()
    assert bedrock.last_prompt is not None
    assert "I need to file for divorce" in bedrock.last_prompt


@pytest.mark.asyncio
async def test_run_falls_back_to_prompt_body_on_structured_failure(sample_states):
    llm_response = {
        "intent": "filing_new",
        "assistant_message": "Fallback reply",
        "selections_update": {"state_code": "TX"},
        "phase_complete": False,
    }
    bedrock = _MockBedrock(llm_response)

    async def fail_structured(*args, **kwargs):
        raise RuntimeError("structured unavailable")

    bedrock.invoke_structured_prompt = fail_structured
    agent = FilingAssistantAgent(bedrock=bedrock)

    result = await agent.run(
        mode="filing_new",
        phase="selecting_state",
        selections={},
        db_options=sample_states,
        history=[],
        user_message="Texas",
    )

    assert result["assistant_message"] == "Fallback reply"
    assert bedrock.last_body is not None


@pytest.mark.asyncio
async def test_run_existing_case_lookup(sample_states):
    llm_response = {
        "intent": "filing_existing",
        "assistant_message": "Do you know your case number?",
        "selections_update": {},
        "lookup_action": "party_search",
        "lookup_params": {"party_names": "Smith"},
        "phase_complete": False,
    }
    bedrock = _MockBedrock(llm_response)
    agent = FilingAssistantAgent(bedrock=bedrock)

    result = await agent.run(
        mode="filing_existing",
        phase="existing_lookup_method",
        selections={},
        db_options=[],
        history=[],
        user_message="Search by party names: Smith",
    )

    assert result["intent"] == "filing_existing"
    assert result["lookup_action"] == "party_search"
    assert result["lookup_params"]["party_names"] == "Smith"


@pytest.mark.asyncio
async def test_run_llm_failure_returns_fallback(sample_states):
    bedrock = _MockBedrock({})

    async def fail_structured(*args, **kwargs):
        raise RuntimeError("timeout")

    async def fail_body(*args, **kwargs):
        raise RuntimeError("timeout")

    bedrock.invoke_structured_prompt = fail_structured
    bedrock.invoke_prompt_with_timeout = fail_body
    agent = FilingAssistantAgent(bedrock=bedrock)

    result = await agent.run(
        mode="unset",
        phase="greeting",
        selections={},
        db_options=sample_states,
        history=[],
        user_message="Hello",
    )

    assert result["intent"] == "continue"
    assert "technical issue" in result["assistant_message"].lower()
