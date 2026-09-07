"""Tests for document_templates.field_mapping LLM mapping."""

import pytest

from app.services.field_mapping_service import (
    FieldMappingService,
    find_missing_mapping_targets,
    format_generate_documents_version,
    is_mapping_question_visible,
    materialize_field_mapping,
    parse_field_mapping_sources,
    parse_field_mapping_spec,
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


def test_parse_field_mapping_supports_legacy_comma_separators():
    mapping = (
        "_COUNTY_COURT=JURISDICTION, _DRIVER_LICENSE=DRIVER_LICENSE, "
        '_FULL_NAME=join(" ", FIRST_NAME, MIDDLE_NAME, LAST_NAME)|'
        "_PHONE=PHONE_NUMBER"
    )
    assert parse_field_mapping_targets(mapping) == [
        "_COUNTY_COURT",
        "_DRIVER_LICENSE",
        "_FULL_NAME",
        "_PHONE",
    ]
    assert parse_field_mapping_sources(mapping) == [
        "JURISDICTION",
        "DRIVER_LICENSE",
        "FIRST_NAME",
        "MIDDLE_NAME",
        "LAST_NAME",
        "PHONE_NUMBER",
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
    # Explicit SOURCE mappings are authoritative over conflicting LLM output.
    assert result.fields["_FULL_NAME"] == "Jane"
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


@pytest.mark.asyncio
async def test_map_fields_uses_question_mapping_source_alias():
    class _Bedrock:
        async def invoke_structured_prompt(self, prompt, schema):
            raise RuntimeError("bedrock down")

        async def invoke_prompt_with_timeout(self, body):
            raise RuntimeError("bedrock down")

    result = await FieldMappingService(bedrock=_Bedrock()).map_fields(
        field_mapping="_COUNTY=JURISDICTION",
        workflow_questions=[
            {
                "field_name": "county_question",
                "mapping_source": "JURISDICTION",
            }
        ],
        collected_answers={"county_question": "Travis"},
        filled_fields={},
    )
    assert result.fields == {"_COUNTY": "Travis"}


ENVELOPE_MAPPING = """
{
  "id": "TX_DIVORCE_PETITION_WITH_CHILDREN",
  "state": "TX",
  "jurisdiction": "harris:dc",
  "version": "1.0",
  "form_data": {
    "_PLAINTIFF_1_FULL_NAME": "",
    "_DRIVER_LICENSE": "",
    "_LICENSE_NUMBER": "",
    "$email": "",
    "$yesterday": ""
  }
}
"""


def test_parse_json_envelope_mapping():
    spec = parse_field_mapping_spec(ENVELOPE_MAPPING)
    assert spec.kind == "envelope"
    assert parse_field_mapping_targets(ENVELOPE_MAPPING) == [
        "_PLAINTIFF_1_FULL_NAME",
        "_DRIVER_LICENSE",
        "_LICENSE_NUMBER",
        "$email",
        "$yesterday",
    ]
    assert parse_field_mapping_sources(ENVELOPE_MAPPING) == [
        "_PLAINTIFF_1_FULL_NAME",
        "_DRIVER_LICENSE",
        "_LICENSE_NUMBER",
    ]


def test_materialize_envelope_keeps_exact_shape():
    output = materialize_field_mapping(
        ENVELOPE_MAPPING,
        mapped_fields={
            "_PLAINTIFF_1_FULL_NAME": "John Michael Doe",
            "_DRIVER_LICENSE": "no",
        },
        selections={
            "template_code": "divorce_petition",
            "state_code": "ca",
            "jurisdiction_code": "other:dc",
            "template_version": "9.9",
            "email": "john.doe@example.com",
        },
        collected_answers={},
    )
    assert set(output.keys()) == {"id", "state", "jurisdiction", "version", "form_data"}
    assert output["id"] == "TX_DIVORCE_PETITION_WITH_CHILDREN"
    assert output["state"] == "TX"
    assert output["jurisdiction"] == "harris:dc"
    assert output["version"] == "1.0"
    assert output["form_data"]["_PLAINTIFF_1_FULL_NAME"] == "John Michael Doe"
    assert output["form_data"]["_DRIVER_LICENSE"] == "no"
    assert output["form_data"]["_LICENSE_NUMBER"] == ""
    assert output["form_data"]["$email"] == "john.doe@example.com"
    assert output["form_data"]["$yesterday"]


def test_materialize_empty_envelope_uses_session_not_hardcoded_values():
    empty_mapping = """
    {
      "id": "",
      "state": "",
      "jurisdiction": "",
      "version": "",
      "form_data": {"_COUNTY_COURT": ""}
    }
    """
    output = materialize_field_mapping(
        empty_mapping,
        mapped_fields={"_COUNTY_COURT": "Harris"},
        selections={
            "template_code": "TX_DIVORCE_PETITION_WITH_CHILDREN",
            "state_code": "tx",
            "jurisdiction_code": "harris:dc",
            "template_version": "2.0",
        },
    )
    assert output["id"] == "TX_DIVORCE_PETITION_WITH_CHILDREN"
    assert output["state"] == "TX"
    assert output["jurisdiction"] == "harris:dc"
    assert output["version"] == "2.0"
    assert output["form_data"] == {"_COUNTY_COURT": "Harris"}


def test_materialize_empty_version_uses_stored_template_version():
    empty_mapping = """
    {
      "id": "TX_DIVORCE_PETITION_WITH_CHILDREN",
      "state": "TX",
      "jurisdiction": "harris:dc",
      "version": "",
      "form_data": {"_COUNTY_COURT": ""}
    }
    """
    output = materialize_field_mapping(
        empty_mapping,
        mapped_fields={"_COUNTY_COURT": "Harris"},
        selections={
            "template_version": 1,
            "s3_key": (
                "documents-repo/templates/TX/TX-TRAVIS-DISTRICT/"
                "TX_DIVORCE_PETITION_WITH_CHILDREN/v1/petition.pdf"
            ),
        },
    )
    assert output["version"] == "1.0"


def test_format_generate_documents_version_from_sources():
    assert format_generate_documents_version("1.0") == "1.0"
    assert format_generate_documents_version(1) == "1.0"
    assert format_generate_documents_version("v1") == "1.0"
    assert format_generate_documents_version(
        "",
        s3_key="templates/TX/TX_DIVORCE_PETITION_WITH_CHILDREN/v1/file.pdf",
    ) == "1.0"
    assert format_generate_documents_version("", s3_key="") == ""


def test_follow_up_visibility_uses_yes_no_answers():
    question = {
        "field_name": "_LICENSE_NUMBER",
        "visibility_condition": {"_DRIVER_LICENSE": "yes"},
    }
    assert is_mapping_question_visible(question, {"_DRIVER_LICENSE": "no"}) is False
    assert is_mapping_question_visible(question, {"_DRIVER_LICENSE": "Yes"}) is True
    assert is_mapping_question_visible(question, {}) is False
