"""Tests for workflow Q&A prompt and WorkflowQuestionsAgent."""

from __future__ import annotations

from typing import Any, Dict

import pytest

from app.adapters.llm.bedrock import Bedrock
from app.agents.conversation.workflow_questions_agent import WorkflowQuestionsAgent
from app.core.prompts.workflow_questions import (
    WORKFLOW_OUTPUT_KEYS,
    WORKFLOW_QUESTIONS_PROMPT,
    WORKFLOW_INTRO_USER_MESSAGE,
)


class _MockBedrock:
    def __init__(self, response: Any):
        self._response = response
        self.last_prompt: str | None = None
        self.last_body: Dict[str, Any] | None = None

    async def invoke_structured_prompt(self, prompt, schema, timeout=None):
        self.last_prompt = prompt
        if isinstance(self._response, str):
            from app.agents.utils.json_utils import parse_llm_json
            data = parse_llm_json(self._response)
        else:
            data = self._response
        return schema.model_validate(data)

    async def invoke_prompt_with_timeout(self, prompt_body: Dict[str, Any], timeout=None):
        self.last_body = prompt_body
        return self._response

    @staticmethod
    def extract_text_field(llm_output: Dict[str, Any], *keys: str):
        return Bedrock.extract_text_field(llm_output, *keys)


@pytest.fixture
def workflow_questions():
    return [
        {
            "field_name": "PETITIONER_FULL_NAME",
            "field_label": "Petitioner full legal name",
            "question_type": "TEXT",
            "required": True,
            "sort_order": 1,
        },
        {
            "field_name": "HAS_CHILDREN",
            "field_label": "Do you have children together?",
            "question_type": "BOOLEAN",
            "required": True,
            "sort_order": 2,
        },
    ]


@pytest.fixture
def checklist_pending():
    return {
        "total": 2,
        "answered": 0,
        "pending": 2,
        "skipped": 0,
        "items": [
            {
                "field_name": "PETITIONER_FULL_NAME",
                "label": "Petitioner full legal name",
                "status": "pending",
                "value": None,
            },
            {
                "field_name": "HAS_CHILDREN",
                "label": "Do you have children together?",
                "status": "pending",
                "value": None,
            },
        ],
    }


def test_workflow_prompt_has_required_placeholders():
    for key in (
        "selections_json",
        "checklist_json",
        "collected_answers_json",
        "pending_questions_batch_json",
        "next_pending_question_json",
        "current_date",
        "current_date_long",
        "history_text",
        "user_message",
    ):
        assert f"{{{key}}}" in WORKFLOW_QUESTIONS_PROMPT


def test_build_prompt_includes_current_date(workflow_questions, checklist_pending):
    prompt = WorkflowQuestionsAgent.build_prompt(
        selections={},
        workflow_questions=workflow_questions,
        checklist=checklist_pending,
        collected_answers={},
        pending_questions_batch=[workflow_questions[0]],
        next_pending_question=workflow_questions[0],
        history=[],
        user_message=WORKFLOW_INTRO_USER_MESSAGE,
    )
    assert "Today's date" in prompt


def test_build_prompt_includes_checklist(workflow_questions, checklist_pending):
    prompt = WorkflowQuestionsAgent.build_prompt(
        selections={
            "state_code": "TX",
            "county_name": "Harris",
            "jurisdiction_code": "TX-HARRIS-DISTRICT",
            "case_type": "Family Law",
            "sub_case_type": "Divorce with Children",
        },
        workflow_questions=workflow_questions,
        checklist=checklist_pending,
        collected_answers={},
        pending_questions_batch=[workflow_questions[0]],
        next_pending_question=workflow_questions[0],
        history=[],
        user_message=WORKFLOW_INTRO_USER_MESSAGE,
    )
    assert "PETITIONER_FULL_NAME" in prompt
    assert "checklist" in prompt.lower() or "pending" in prompt
    assert "one" in prompt.lower() or "batch" in prompt.lower() or "together" in prompt.lower()


def test_normalize_output_fills_defaults():
    result = WorkflowQuestionsAgent.normalize_output({})
    assert set(result.keys()) >= set(WORKFLOW_OUTPUT_KEYS)
    assert result["workflow_complete"] is False
    assert result["checklist_updates"] == []
    assert result["assistant_message"]


@pytest.mark.asyncio
async def test_run_first_workflow_question(workflow_questions, checklist_pending):
    llm_response = {
        "assistant_message": "Let's start with the Petitioner. What is your full legal name?",
        "answers_update": {},
        "checklist_updates": [],
        "workflow_complete": False,
    }
    bedrock = _MockBedrock(llm_response)
    agent = WorkflowQuestionsAgent(bedrock=bedrock)

    result = await agent.run(
        selections={"state_code": "TX", "case_type": "Family Law"},
        workflow_questions=workflow_questions,
        checklist=checklist_pending,
        collected_answers={},
        next_pending_question=workflow_questions[0],
        history=[],
        user_message=WORKFLOW_INTRO_USER_MESSAGE,
    )

    assert "petitioner" in result["assistant_message"].lower() or "legal name" in result["assistant_message"].lower()
    assert result["workflow_complete"] is False
    assert bedrock.last_prompt is not None
    assert "PETITIONER_FULL_NAME" in bedrock.last_prompt


@pytest.mark.asyncio
async def test_run_records_answer_and_checklist_update(workflow_questions, checklist_pending):
    llm_response = {
        "assistant_message": "Thank you, John. Do you have children together?",
        "answers_update": {"PETITIONER_FULL_NAME": "John Smith"},
        "checklist_updates": [
            {
                "field_name": "PETITIONER_FULL_NAME",
                "status": "answered",
                "value": "John Smith",
            }
        ],
        "workflow_complete": False,
    }
    bedrock = _MockBedrock(llm_response)
    agent = WorkflowQuestionsAgent(bedrock=bedrock)

    result = await agent.run(
        selections={"state_code": "TX"},
        workflow_questions=workflow_questions,
        checklist=checklist_pending,
        collected_answers={},
        next_pending_question=workflow_questions[0],
        history=[{"role": "assistant", "content": "What is your full legal name?"}],
        user_message="John Smith",
    )

    assert result["answers_update"]["PETITIONER_FULL_NAME"] == "John Smith"
    assert result["checklist_updates"][0]["status"] == "answered"
    assert result["checklist_updates"][0]["value"] == "John Smith"


@pytest.mark.asyncio
async def test_run_workflow_complete(workflow_questions, checklist_pending):
    llm_response = {
        "assistant_message": "Thank you! I have all the information I need.",
        "answers_update": {"HAS_CHILDREN": "yes"},
        "checklist_updates": [
            {"field_name": "HAS_CHILDREN", "status": "answered", "value": "yes"}
        ],
        "workflow_complete": True,
    }
    bedrock = _MockBedrock(llm_response)
    agent = WorkflowQuestionsAgent(bedrock=bedrock)

    result = await agent.run(
        selections={"state_code": "TX"},
        workflow_questions=workflow_questions,
        checklist={
            **checklist_pending,
            "answered": 1,
            "pending": 1,
        },
        collected_answers={"PETITIONER_FULL_NAME": "John Smith"},
        next_pending_question=workflow_questions[1],
        history=[],
        user_message="Yes, two children",
    )

    assert result["workflow_complete"] is True
    assert result["answers_update"]["HAS_CHILDREN"] == "yes"


@pytest.mark.asyncio
async def test_run_llm_failure_returns_fallback(workflow_questions, checklist_pending):
    bedrock = _MockBedrock({})

    async def fail_structured(*args, **kwargs):
        raise RuntimeError("bedrock down")

    async def fail_body(*args, **kwargs):
        raise RuntimeError("bedrock down")

    bedrock.invoke_structured_prompt = fail_structured
    bedrock.invoke_prompt_with_timeout = fail_body
    agent = WorkflowQuestionsAgent(bedrock=bedrock)

    result = await agent.run(
        selections={},
        workflow_questions=workflow_questions,
        checklist=checklist_pending,
        collected_answers={},
        next_pending_question=workflow_questions[0],
        history=[],
        user_message="John Smith",
    )

    assert result["workflow_complete"] is False
    assert "technical issue" in result["assistant_message"].lower()
