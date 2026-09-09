"""Tests for document_templates.field_mapping LLM mapping."""

import pytest

from app.services.field_mapping_service import (
    FieldMappingService,
    FieldMappingValidationError,
    assert_valid_generate_documents_payload,
    classify_sample_form_value,
    find_missing_mapping_targets,
    format_generate_documents_version,
    is_mapping_question_visible,
    materialize_field_mapping,
    normalize_mapped_fields,
    normalize_to_yes_no,
    parse_county_court_from_selections,
    parse_field_mapping_sources,
    parse_field_mapping_spec,
    parse_field_mapping_targets,
    validate_and_complete_mapping,
    validate_generate_documents_payload,
    validate_semantic_form_data,
)


DIVORCE_SAMPLE_INPUT = """
{
  "id": "divorce_petition",
  "state": "TX",
  "jurisdiction": "harris:dc",
  "version": "1.0",
  "form_data": {
    "_PLAINTIFF_1_FULL_NAME": "John Michael Doe",
    "_DEFENDANT_1_FULL_NAME": "Jane Elizabeth Doe",
    "_CHILDREN": "no",
    "_COUNTY_COURT": "Harris",
    "_DRIVER_LICENSE": "no",
    "_SOCIAL_SECURITY_NUMBER": "no",
    "_MARRIAGE_DATE": "06/15/2018",
    "_LEAVING_TOGETHER_STOP_DATE": "03/01/2026",
    "_DOMICILE": "no",
    "_GROUNDS_TEMPLATE1": "The marriage has become insupportable because of discord or conflict of personalities that destroys the legitimate ends of the marital relationship and prevents any reasonable expectation of reconciliation.",
    "_LEGAL_NOTICE": "yes",
    "_PROTECTIVE_ORDER": "no",
    "_NAME_CHANGE": "no",
    "_PLAINTIFF_1_ADDRESS_LINE_1": "1234 Main Street",
    "_PLAINTIFF_1_CITY": "Houston",
    "_PLAINTIFF_1_STATE": "TX",
    "_PLAINTIFF_1_ZIPCODE": "77002",
    "_PLAINTIFF_1_PHONE_NUMBER": "713-555-0147",
    "$email": "john.doe@example.com",
    "$yesterday": "08/31/2026"
  }
}
"""


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
        "$email",
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


def test_materialize_uses_sample_input_envelope_meta():
    output = materialize_field_mapping(
        ENVELOPE_MAPPING,
        mapped_fields={"_PLAINTIFF_1_FULL_NAME": "Jane Doe"},
        selections={
            "template_code": "TX_DIVORCE_PETITION_WITH_CHILDREN",
            "state_code": "ca",
            "jurisdiction_code": "travis:dc",
            "template_version": "9.9",
        },
        sample_input=DIVORCE_SAMPLE_INPUT,
    )
    assert output["id"] == "divorce_petition"
    assert output["state"] == "TX"
    assert output["jurisdiction"] == "harris:dc"
    assert output["version"] == "1.0"


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


def test_validate_generate_documents_payload_requires_all_form_data_keys():
    payload = materialize_field_mapping(
        ENVELOPE_MAPPING,
        mapped_fields={"_PLAINTIFF_1_FULL_NAME": "Jane Doe"},
        selections={
            "template_code": "TX_DIVORCE_PETITION_WITH_CHILDREN",
            "state_code": "tx",
            "jurisdiction_code": "harris:dc",
            "template_version": "1.0",
        },
    )
    result = validate_generate_documents_payload(ENVELOPE_MAPPING, payload)
    assert result.is_valid is True
    assert result.missing_form_data_keys == []


def test_validate_generate_documents_payload_rejects_missing_keys():
    payload = {
        "id": "TX_DIVORCE_PETITION_WITH_CHILDREN",
        "state": "TX",
        "jurisdiction": "harris:dc",
        "version": "1.0",
        "form_data": {"_PLAINTIFF_1_FULL_NAME": "Jane Doe"},
    }
    result = validate_generate_documents_payload(ENVELOPE_MAPPING, payload)
    assert result.is_valid is False
    assert "_DRIVER_LICENSE" in result.missing_form_data_keys
    with pytest.raises(FieldMappingValidationError, match="field_mapping validation"):
        assert_valid_generate_documents_payload(ENVELOPE_MAPPING, payload)


def test_parse_county_court_from_jurisdiction_name():
    county = parse_county_court_from_selections(
        {"jurisdiction_name": "Harris County - District Clerk"}
    )
    assert county == "Harris"


def test_parse_county_court_from_jurisdiction_code():
    county = parse_county_court_from_selections({"jurisdiction_code": "harris:dc"})
    assert county == "Harris"


def test_normalize_mapped_fields_fixes_county_and_booleans():
    selections = {
        "jurisdiction_name": "Harris County - District Clerk",
        "email": "john.doe@example.com",
    }
    normalized = normalize_mapped_fields(
        {
            "_COUNTY_COURT": "No",
            "_DRIVER_LICENSE": "I do not have a driver's license number.",
            "_LEGAL_NOTICE": "My spouse will sign a Waiver of Service.",
            "_DOMICILE": "My spouse lives in Texas.",
            "$email": "",
            "$yesterday": "",
        },
        sample_input=DIVORCE_SAMPLE_INPUT,
        selections=selections,
    )
    assert normalized["_COUNTY_COURT"] == "Harris"
    assert normalized["_DRIVER_LICENSE"] == "no"
    assert normalized["_LEGAL_NOTICE"] == "yes"
    assert normalized["_DOMICILE"] == "no"
    assert normalized["$email"] == "john.doe@example.com"
    assert "$yesterday" in normalized
    assert normalized["$yesterday"]


def test_classify_sample_form_value():
    assert classify_sample_form_value("yes") == "boolean"
    assert classify_sample_form_value("1") == "numeric_code"
    assert classify_sample_form_value("08/31/2026") == "date"
    assert classify_sample_form_value("john.doe@example.com") == "email"


def test_normalize_to_yes_no_from_prose():
    assert normalize_to_yes_no("I do not have a driver's license number.") == "no"
    assert normalize_to_yes_no("My spouse will sign a Waiver of Service.") == "yes"
    assert (
        normalize_to_yes_no(
            "I ask the clerk to issue a Citation of Service for my spouse."
        )
        == "no"
    )
    assert normalize_to_yes_no("My spouse lives in Texas.") == "no"


def test_validate_semantic_form_data_rejects_invalid_county():
    errors = validate_semantic_form_data(
        {"_COUNTY_COURT": "No", "_DRIVER_LICENSE": "no"},
        sample_input=DIVORCE_SAMPLE_INPUT,
        selections={"jurisdiction_name": "Harris County - District Clerk"},
    )
    assert any("_COUNTY_COURT" in err for err in errors)


def test_validate_generate_documents_payload_semantic_errors():
    mapping = """
    {
      "id": "",
      "state": "",
      "jurisdiction": "",
      "version": "",
      "form_data": {
        "_COUNTY_COURT": "",
        "_DRIVER_LICENSE": "",
        "$email": ""
      }
    }
    """
    payload = {
        "id": "divorce_petition",
        "state": "TX",
        "jurisdiction": "harris:dc",
        "version": "1.0",
        "form_data": {
            "_COUNTY_COURT": "No",
            "_DRIVER_LICENSE": "125, TX",
            "$email": "john.doe@example.com",
        },
    }
    result = validate_generate_documents_payload(
        mapping,
        payload,
        sample_input=DIVORCE_SAMPLE_INPUT,
        selections={"jurisdiction_name": "Harris County - District Clerk"},
    )
    assert result.is_valid is True
    assert any("_DRIVER_LICENSE" in err for err in result.semantic_errors)


@pytest.mark.asyncio
async def test_map_fields_normalizes_using_sample_input():
    mapping = "_COUNTY_COURT=JURISDICTION|_DRIVER_LICENSE=DRIVER_LICENSE"

    class _Bedrock:
        async def invoke_structured_prompt(self, prompt, schema):
            assert "sample generate_documents payload" in prompt.lower()
            assert "_DRIVER_LICENSE\": \"no\"" in prompt
            return schema(
                fields={
                    "_COUNTY_COURT": "No",
                    "_DRIVER_LICENSE": "I do not have a driver's license number.",
                }
            )

        async def invoke_prompt_with_timeout(self, body):
            return {}

    result = await FieldMappingService(bedrock=_Bedrock()).map_fields(
        field_mapping=mapping,
        workflow_questions=[],
        collected_answers={"JURISDICTION": "No", "DRIVER_LICENSE": "no"},
        filled_fields={},
        selections={"jurisdiction_name": "Harris County - District Clerk"},
        sample_input=DIVORCE_SAMPLE_INPUT,
    )
    assert result.fields["_COUNTY_COURT"] == "Harris"
    assert result.fields["_DRIVER_LICENSE"] == "no"
