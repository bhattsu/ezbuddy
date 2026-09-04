"""Tests for workflow question deduplication and clustering."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.workflow_question_consolidation_service import (
    ConsolidationCluster,
    ConsolidationLLMOutput,
    WorkflowQuestionConsolidationService,
    _deterministic_consolidate,
    propagate_cluster_answers,
)


class _SessionStub:
    def __init__(self) -> None:
        self.collected_answers: dict = {}
        self.workflow_questions: list = []
        self.metadata: dict = {}


def test_deterministic_consolidate_merges_duplicate_labels():
    db = [
        {
            "field_name": "PETITIONER_NAME",
            "field_label": "Petitioner full legal name",
            "required": True,
            "sort_order": 1,
        }
    ]
    doc = [
        {
            "field_name": "petitioner_full_name",
            "field_label": "Petitioner Full Legal Name",
            "pdf_field": "PetitionerName",
            "required": True,
            "sort_order": 2,
        }
    ]
    out = _deterministic_consolidate(db, doc)
    assert len(out.clusters) == 1
    assert out.removed_duplicates


@pytest.mark.asyncio
async def test_consolidate_skips_llm_for_small_single_source():
    bedrock = MagicMock()
    service = WorkflowQuestionConsolidationService(bedrock)
    result = await service.consolidate(
        db_questions=[
            {"field_name": "A", "field_label": "Question A", "sort_order": 1},
            {"field_name": "B", "field_label": "Question B", "sort_order": 2},
        ],
        document_questions=[],
        selections={},
    )
    assert len(result.questions) == 2
    assert result.original_count == 2
    bedrock.invoke_structured_prompt.assert_not_called()


@pytest.mark.asyncio
async def test_consolidate_calls_llm_when_both_sources_present():
    bedrock = MagicMock()
    bedrock.invoke_structured_prompt = AsyncMock(
        return_value=ConsolidationLLMOutput(
            clusters=[
                ConsolidationCluster(
                    cluster_id="petitioner",
                    question="What is the petitioner's full legal name?",
                    canonical_field_name="petitioner_full_name",
                    cluster_fields=["petitioner_full_name", "PETITIONER_NAME"],
                    duplicate_fields=["PETITIONER_NAME"],
                    required=True,
                    sort_order=1,
                )
            ],
            removed_duplicates=[
                {
                    "removed_field": "PETITIONER_NAME",
                    "kept_field": "petitioner_full_name",
                    "reason": "duplicate",
                }
            ],
            summary="Merged 2 into 1",
        )
    )
    service = WorkflowQuestionConsolidationService(bedrock)
    result = await service.consolidate(
        db_questions=[
            {
                "field_name": "PETITIONER_NAME",
                "field_label": "Petitioner name",
                "sort_order": 1,
            }
        ],
        document_questions=[
            {
                "field_name": "petitioner_full_name",
                "field_label": "Petitioner full name",
                "pdf_field": "PetitionerName",
                "sort_order": 2,
            },
            {
                "field_name": "respondent_name",
                "field_label": "Respondent name",
                "sort_order": 3,
            },
        ],
        selections={"case_type": "divorce"},
    )
    assert len(result.questions) == 1
    assert result.questions[0]["field_name"] == "petitioner_full_name"
    assert result.field_aliases["PETITIONER_NAME"] == "petitioner_full_name"
    assert result.original_count == 3
    bedrock.invoke_structured_prompt.assert_awaited_once()


def test_propagate_cluster_answers_copies_duplicates():
    session = _SessionStub()
    session.workflow_questions = [
        {
            "field_name": "petitioner_full_name",
            "duplicate_fields": ["PETITIONER_NAME"],
            "cluster_fields": ["petitioner_full_name", "PETITIONER_NAME"],
        }
    ]
    session.metadata["workflow_field_aliases"] = {
        "PETITIONER_NAME": "petitioner_full_name"
    }
    session.collected_answers["petitioner_full_name"] = "Jane Doe"
    propagate_cluster_answers(session)
    assert session.collected_answers["PETITIONER_NAME"] == "Jane Doe"
