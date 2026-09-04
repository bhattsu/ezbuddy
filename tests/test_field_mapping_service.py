"""Tests for document_templates.field_mapping LLM mapping."""

import pytest

from app.services.field_mapping_service import (
    FieldMappingService,
    find_missing_mapping_targets,
    parse_field_mapping_targets,
    validate_and_complete_mapping,
)


def test_parse_field_mapping_targets():
    mapping = (
        '_FULL_NAME=join(" ", A, B)|_SPOUSE=join(" ", X, Y)|_COUNTY=JURISDICTION'
    )
    assert parse_field_mapping_targets(mapping) == [
        "_FULL_NAME",
        "_SPOUSE",
        "_COUNTY",
    ]


def test_find_missing_mapping_targets():
    assert find_missing_mapping_targets(
        {"_FULL_NAME": "Jane"}, ["_FULL_NAME", "_COUNTY"]
    ) == ["_COUNTY"]


def test_validate_and_complete_mapping_backfills_missing_keys():
    validated = validate_and_complete_mapping(
        fields={"_FULL_NAME": "Jane Doe"},
        targets=["_FULL_NAME", "_COUNTY"],
        fallback={"_COUNTY": "Travis"},
    )
    assert validated.is_complete is True
    assert validated.fields == {"_FULL_NAME": "Jane Doe", "_COUNTY": "Travis"}
    assert validated.missing_from_llm == ["_COUNTY"]
    assert validated.backfilled_targets == ["_COUNTY"]


def test_validate_and_complete_mapping_backfills_empty_llm_values():
    validated = validate_and_complete_mapping(
        fields={"_FULL_NAME": "Jane Doe", "_COUNTY": ""},
        targets=["_FULL_NAME", "_COUNTY"],
        fallback={"_COUNTY": "Travis"},
    )
    assert validated.fields["_COUNTY"] == "Travis"
    assert validated.missing_from_llm == ["_COUNTY"]
    assert validated.backfilled_targets == ["_COUNTY"]


def test_validate_and_complete_mapping_adds_empty_for_unresolved_targets():
    validated = validate_and_complete_mapping(
        fields={"_FULL_NAME": "Jane Doe"},
        targets=["_FULL_NAME", "_COUNTY"],
        fallback={},
    )
    assert validated.is_complete is True
    assert validated.fields["_COUNTY"] == ""
    assert validated.missing_from_llm == ["_COUNTY"]
    assert validated.backfilled_targets == []


def test_validate_and_complete_mapping_drops_extra_keys():
    validated = validate_and_complete_mapping(
        fields={"_FULL_NAME": "Jane", "_EXTRA": "nope"},
        targets=["_FULL_NAME"],
        fallback={},
    )
    assert validated.fields == {"_FULL_NAME": "Jane"}


def test_deterministic_fallback_direct_and_join():
    service = FieldMappingService(bedrock=None)
    mapping = (
        '_FULL_NAME=join(" ", PLAINTIFF_1_FIRST_NAME, PLAINTIFF_1_LAST_NAME)'
        "|_COUNTY=JURISDICTION"
    )
    result = service._deterministic_fallback(
        mapping,
        {
            "PLAINTIFF_1_FIRST_NAME": "Jane",
            "PLAINTIFF_1_LAST_NAME": "Doe",
            "JURISDICTION": "Travis",
        },
        {},
    )
    assert result["_FULL_NAME"] == "Jane Doe"
    assert result["_COUNTY"] == "Travis"


@pytest.mark.asyncio
async def test_map_fields_uses_llm_output():
    mapping = "_FULL_NAME=PLAINTIFF_1_FIRST_NAME|_COUNTY=JURISDICTION"

    class _Bedrock:
        async def invoke_structured_prompt(self, prompt, schema):
            return schema(
                fields={
                    "_FULL_NAME": "Jane Doe",
                    "_COUNTY": "Travis",
                    "_EXTRA": "ignore me",
                }
            )

        async def invoke_prompt_with_timeout(self, body):
            return {}

    service = FieldMappingService(bedrock=_Bedrock())
    result = await service.map_fields(
        field_mapping=mapping,
        workflow_questions=[{"field_name": "PLAINTIFF_1_FIRST_NAME"}],
        collected_answers={"PLAINTIFF_1_FIRST_NAME": "Jane", "JURISDICTION": "Travis"},
        filled_fields={"Petitioner Name": "Jane Doe"},
    )
    assert result.is_complete is True
    assert result.fields["_FULL_NAME"] == "Jane Doe"
    assert result.fields["_COUNTY"] == "Travis"
    assert "_EXTRA" not in result.fields
    assert set(result.fields) == {"_FULL_NAME", "_COUNTY"}


@pytest.mark.asyncio
async def test_map_fields_backfills_missing_llm_targets():
    mapping = "_FULL_NAME=PLAINTIFF_1_FIRST_NAME|_COUNTY=JURISDICTION"

    class _Bedrock:
        async def invoke_structured_prompt(self, prompt, schema):
            return schema(fields={"_FULL_NAME": "Jane Doe"})

        async def invoke_prompt_with_timeout(self, body):
            return {}

    service = FieldMappingService(bedrock=_Bedrock())
    result = await service.map_fields(
        field_mapping=mapping,
        workflow_questions=[],
        collected_answers={"JURISDICTION": "Travis"},
        filled_fields={},
    )
    assert result.is_complete is True
    assert result.fields["_COUNTY"] == "Travis"
    assert result.missing_from_llm == ["_COUNTY"]
    assert result.backfilled_targets == ["_COUNTY"]


@pytest.mark.asyncio
async def test_map_fields_falls_back_when_llm_fails():
    mapping = (
        '_FULL_NAME=join(" ", PLAINTIFF_1_FIRST_NAME, PLAINTIFF_1_LAST_NAME)'
        "|_COUNTY=JURISDICTION"
    )

    class _Bedrock:
        async def invoke_structured_prompt(self, prompt, schema):
            raise RuntimeError("bedrock down")

        async def invoke_prompt_with_timeout(self, body):
            raise RuntimeError("bedrock down")

    service = FieldMappingService(bedrock=_Bedrock())
    result = await service.map_fields(
        field_mapping=mapping,
        workflow_questions=[],
        collected_answers={
            "PLAINTIFF_1_FIRST_NAME": "Jane",
            "PLAINTIFF_1_LAST_NAME": "Doe",
            "JURISDICTION": "Travis",
        },
        filled_fields={},
    )
    assert result.is_complete is True
    assert result.fields["_FULL_NAME"] == "Jane Doe"
    assert result.fields["_COUNTY"] == "Travis"
    assert set(result.fields.keys()) == {"_FULL_NAME", "_COUNTY"}
