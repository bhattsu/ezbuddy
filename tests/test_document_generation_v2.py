"""Tests for document generation 2.0 JSON filling."""

import pytest

from app.services.document_generation_v2_service import (
    DocumentGenerationV2Service,
    map_answers_to_pdf_fields,
    match_answer_for_field,
)
from app.services.document_template_match import template_questions_to_workflow


def test_map_answers_uses_exact_pdf_field_keys():
    fields = [
        {"field": "Cause Number", "question": "What is the cause number?"},
        {"field": "Petitioner Name", "question": "What is the Petitioner's full name?"},
        {"field": "Court Number", "question": "What is the court number?"},
    ]
    answers = {
        "cause_number": "20250622001",
        "What is the Petitioner's full name?": "PLAINTIFF PARTY, II",
        "court_number": "5465",
    }
    filled = map_answers_to_pdf_fields(fields, answers)
    assert filled == {
        "Cause Number": "20250622001",
        "Petitioner Name": "PLAINTIFF PARTY, II",
        "Court Number": "5465",
    }


def test_match_answer_does_not_invent_values():
    row = {"field": "County", "question": "What is the county name?"}
    assert match_answer_for_field(row, {"cause_number": "1"}) == ""


def test_template_questions_keep_pdf_field():
    questions, _answers = template_questions_to_workflow(
        [
            {
                "question": "What is the cause number?",
                "answer": "",
                "field": "Cause Number",
            }
        ]
    )
    assert questions[0]["pdf_field"] == "Cause Number"


@pytest.mark.asyncio
async def test_generate_v2_prefers_llm_values_on_exact_keys():
    class _Bedrock:
        async def invoke_structured_prompt(self, prompt, schema):
            return schema(
                fields={
                    "Cause Number": "20250622001",
                    "Invented": "nope",
                }
            )

        async def invoke_prompt_with_timeout(self, body):
            return {}

    service = DocumentGenerationV2Service(bedrock=_Bedrock())
    result = await service.generate(
        extracted_text="Cause Number _____ Petitioner Name _____",
        extracted_fields=[
            {"field": "Cause Number", "question": "What is the cause number?"},
            {"field": "Petitioner Name", "question": "What is the petitioner name?"},
        ],
        answers={"cause_number": "20250622001", "petitioner_name": "Jane"},
        file_name="petition.pdf",
    )
    assert result.fields["Cause Number"] == "20250622001"
    assert result.fields["Petitioner Name"] == "Jane"
    assert "Invented" not in result.fields


@pytest.mark.asyncio
async def test_generate_v2_falls_back_when_llm_fails():
    class _Bedrock:
        async def invoke_structured_prompt(self, prompt, schema):
            raise RuntimeError("bedrock down")

        async def invoke_prompt_with_timeout(self, body):
            raise RuntimeError("bedrock down")

    service = DocumentGenerationV2Service(bedrock=_Bedrock())
    result = await service.generate(
        extracted_fields=[
            {"field": "Cause Number", "question": "What is the cause number?"}
        ],
        answers={"cause_number": "20250622001"},
    )
    assert result.fields == {"Cause Number": "20250622001"}
