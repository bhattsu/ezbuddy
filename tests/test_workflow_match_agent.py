"""Tests for strict workflow-name matching."""

from __future__ import annotations

import pytest

from app.agents.conversation.workflow_match_agent import WorkflowMatchAgent


class _MockBedrock:
    def __init__(self, response):
        self.response = response
        self.last_prompt = None

    async def invoke_structured_prompt(self, prompt, schema, timeout=None):
        self.last_prompt = prompt
        return schema.model_validate(self.response)


WORKFLOWS = [
    {
        "workflow_id": "wf-child",
        "workflow_code": "TX_DIV_CHILD",
        "workflow_name": "Texas Divorce With Children",
        "case_type": "Family Law",
        "sub_case_type": "Divorce With Children",
    },
    {
        "workflow_id": "wf-no-child",
        "workflow_code": "TX_DIV_NO_CHILD",
        "workflow_name": "Texas Divorce Without Children",
        "case_type": "Family Law",
        "sub_case_type": "Divorce Without Children",
    },
]


def test_strict_candidate_matches_state_and_api_case_type():
    selections = {
        "state_name": "Texas",
        "case_category_name": "Family",
        "case_type_name": "Divorce with Children",
        "party_type_name": "Petitioner",
    }

    candidates = WorkflowMatchAgent.strict_candidates(selections, WORKFLOWS)

    assert [row["workflow_id"] for row in candidates] == ["wf-child"]


def test_strict_candidate_does_not_use_fuzzy_related_name():
    selections = {
        "state_name": "Texas",
        "case_type_name": "Divorce",
    }

    assert WorkflowMatchAgent.strict_candidates(selections, WORKFLOWS) == []


@pytest.mark.asyncio
async def test_llm_confirms_strict_workflow():
    bedrock = _MockBedrock(
        {
            "matched": True,
            "workflow_id": "wf-child",
            "confidence": 0.98,
            "reason": "Exact workflow name and consistent filing selections.",
        }
    )
    agent = WorkflowMatchAgent(bedrock=bedrock)

    result = await agent.match(
        {
            "state_name": "Texas",
            "case_category_name": "Family",
            "case_type_name": "Divorce with Children",
            "party_type_name": "Petitioner",
        },
        WORKFLOWS,
    )

    assert result["matched"] is True
    assert result["workflow_id"] == "wf-child"
    assert result["matched_workflow"]["workflow_name"] == "Texas Divorce With Children"
    assert "Texas Divorce With Children" in bedrock.last_prompt


@pytest.mark.asyncio
async def test_no_exact_workflow_skips_llm():
    bedrock = _MockBedrock({})
    agent = WorkflowMatchAgent(bedrock=bedrock)

    result = await agent.match(
        {"state_name": "Texas", "case_type_name": "Adoption"},
        WORKFLOWS,
    )

    assert result["matched"] is False
    assert result["workflow_id"] is None
    assert bedrock.last_prompt is None
