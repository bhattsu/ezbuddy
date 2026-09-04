"""Tests for matching document_templates to a filing case."""

import pytest

from app.agents.conversation.orchestration.helpers import (
    advance_phase_after_selections,
    handle_lookup,
)
from app.agents.conversation.orchestration.state import FilingSession
from app.agents.utils.db_options_format import filter_selections_update
from app.api.schemas.filing_events import FilingMode, FilingPhase
from app.services.document_template_match import (
    match_document_templates,
    merge_document_and_mapping_questions,
    template_questions_to_workflow,
)
from app.services.field_mapping_service import (
    parse_field_mapping_sources,
    parse_field_mapping_targets,
)


TEMPLATES = [
    {
        "id": "00f53d19-6843-4b69-bece-58ff10a616cb",
        "code": "TX_DIVORCE_WAIVER_OF_SERVICE",
        "name": "Waiver of Service",
        "is_active": True,
        "doc_type": "PETITION_WAIVER_OF_SERVICE",
        "state": "TX",
        "jurisdiction": "",
        "case_category": "Family_Marriage Relationship",
        "case_type": "DIVORCE",
        "case_subtype": "",
        "s3_bucket": "goml-uslegalpro-dev",
        "s3_key": "documents-repo/templates/waiver.pdf",
    },
    {
        "id": "5793c8a8-aa21-46ee-a353-c6c37825811b",
        "code": "TX_DIVORCE_PETITION_NO_CHILDREN",
        "name": "Original Petition for Divorce Without Children",
        "is_active": True,
        "doc_type": "PETITION_NO_CHILDREN",
        "state": "TX",
        "jurisdiction": "",
        "case_category": "Family_Marriage Relationship",
        "case_type": "DIVORCE",
        "case_subtype": "WITHOUT_CHILDREN",
        "s3_bucket": "goml-uslegalpro-dev",
        "s3_key": "documents-repo/templates/no-children.pdf",
    },
    {
        "id": "c8cb71fe-10fe-4ad5-b360-50c64398cb12",
        "code": "TX_DIVORCE_PETITION_WITH_CHILDREN",
        "name": "Original Petition for Divorce With Children",
        "is_active": True,
        "doc_type": "PETITION_WITH_CHILDREN",
        "state": "TX",
        "jurisdiction": "",
        "case_category": "Family_Marriage Relationship",
        "case_type": "DIVORCE",
        "case_subtype": "WITH_CHILDREN",
        "s3_bucket": "goml-uslegalpro-dev",
        "s3_key": "documents-repo/templates/with-children.pdf",
    },
]


def test_match_no_children_includes_waiver_and_petition():
    selections = {
        "state_code": "TX",
        "jurisdiction_code": "refugio:dc",
        "case_category_name": "Family - Marriage Relationship",
        "case_type_name": "Divorce No Children",
        "case_type_display": "Divorce No Children",
    }
    matched = match_document_templates(TEMPLATES, selections)
    doc_types = {row["doc_type"] for row in matched}
    assert doc_types == {"PETITION_WAIVER_OF_SERVICE", "PETITION_NO_CHILDREN"}


def test_match_with_children_excludes_no_children_petition():
    selections = {
        "state_code": "TX",
        "jurisdiction_code": "travis:dc",
        "case_category_display": "Family - Marriage Relationship",
        "case_type_display": "Divorce with Children",
    }
    matched = match_document_templates(TEMPLATES, selections)
    doc_types = {row["doc_type"] for row in matched}
    assert "PETITION_WITH_CHILDREN" in doc_types
    assert "PETITION_NO_CHILDREN" not in doc_types
    assert "PETITION_WAIVER_OF_SERVICE" in doc_types


def test_new_case_advances_to_document_type_after_party():
    session = FilingSession(
        conversation_id="c1",
        user_id="u1",
        mode=FilingMode.FILING_NEW,
        phase=FilingPhase.SELECTING_CASE_PARTIES,
        selections={
            "party_type_code": "PET",
            "case_type_name": "Divorce with Children",
        },
    )
    advance_phase_after_selections(session)
    assert session.phase == FilingPhase.SELECTING_DOCUMENT_TYPE


def test_document_type_advances_only_after_questions_ready():
    session = FilingSession(
        conversation_id="c1",
        user_id="u1",
        mode=FilingMode.FILING_NEW,
        phase=FilingPhase.SELECTING_DOCUMENT_TYPE,
        selections={
            "template_id": "abc",
            "document_type_code": "PETITION_WITH_CHILDREN",
        },
    )
    advance_phase_after_selections(session)
    assert session.phase == FilingPhase.SELECTING_DOCUMENT_TYPE
    session.selections["template_questions_ready"] = True
    advance_phase_after_selections(session)
    assert session.phase == FilingPhase.COLLECTING_WORKFLOW_ANSWERS


@pytest.mark.asyncio
async def test_confirm_case_goes_to_document_type():
    session = FilingSession(
        conversation_id="c1",
        user_id="u1",
        mode=FilingMode.FILING_EXISTING,
        phase=FilingPhase.EXISTING_CASE_CONFIRM,
        selections={"state_code": "TX"},
    )
    await handle_lookup(None, session, "confirm_case", {})  # type: ignore[arg-type]
    assert session.phase == FilingPhase.SELECTING_DOCUMENT_TYPE


def test_template_questions_to_workflow_prefills_answers():
    questions, answers = template_questions_to_workflow(
        [
            {
                "question": "What is the cause number?",
                "answer": "2025-1",
                "field": "Cause Number",
            },
            {
                "question": "What is the petitioner name?",
                "answer": "",
                "field": "Petitioner Name",
            },
        ]
    )
    assert questions[0]["field_name"] == "cause_number"
    assert answers["cause_number"] == "2025-1"
    assert "petitioner_name" not in answers


def test_filter_selections_update_carries_template_id():
    options = match_document_templates(
        TEMPLATES,
        {
            "state_code": "TX",
            "case_category_name": "Family - Marriage Relationship",
            "case_type_name": "Divorce No Children",
        },
    )
    chosen = next(
        row for row in options if row["doc_type"] == "PETITION_NO_CHILDREN"
    )
    result = filter_selections_update(
        "selecting_document_type",
        {"document_type_code": chosen["code"]},
        options,
    )
    assert result["template_id"] == "5793c8a8-aa21-46ee-a353-c6c37825811b"
    assert result["doc_type"] == "PETITION_NO_CHILDREN"
    assert result["s3_key"] == "documents-repo/templates/no-children.pdf"
    assert result["template_code"] == "TX_DIVORCE_PETITION_NO_CHILDREN"


def test_known_case_answers_prefill_and_skip_children():
    from app.services.document_template_match import apply_known_case_answers

    questions = [
        {
            "field_name": "cause_number",
            "field_label": "What is the cause number?",
            "pdf_field": "Cause Number",
        },
        {
            "field_name": "county_name",
            "field_label": "What is the county name?",
            "pdf_field": "County",
        },
        {
            "field_name": "child_support",
            "field_label": "What is the child support amount?",
            "pdf_field": "Child Support",
        },
        {
            "field_name": "court_type",
            "field_label": "Is this a District Court or County Court at Law?",
            "pdf_field": "Court Type",
        },
    ]
    answers, skipped = apply_known_case_answers(
        questions,
        {
            "case_number": "20250622001",
            "jurisdiction_display": "Refugio County - District Clerk",
            "case_type_display": "Divorce No Children",
            "doc_type": "PETITION_NO_CHILDREN",
        },
    )
    assert answers["cause_number"] == "20250622001"
    assert answers["county_name"] == "Refugio"
    assert answers["court_type"] == "District Court"
    assert "child_support" in skipped
    assert "child_support" not in answers


def test_skipped_fields_do_not_block_completion():
    from app.agents.conversation.orchestration.helpers import workflow_is_complete
    from app.agents.conversation.orchestration.state import ChecklistItem, WorkflowChecklist

    session = FilingSession(
        conversation_id="c1",
        user_id="u1",
        workflow_questions=[
            {"field_name": "cause_number", "required": True},
            {"field_name": "child_support", "required": True},
        ],
        checklist=WorkflowChecklist(
            items=[
                ChecklistItem(
                    field_name="cause_number",
                    label="Cause",
                    required=True,
                    status="answered",
                    value="1",
                ),
                ChecklistItem(
                    field_name="child_support",
                    label="Support",
                    required=True,
                    status="skipped",
                ),
            ]
        ),
        collected_answers={"cause_number": "1"},
    )
    assert workflow_is_complete(session)


def test_next_form_question_is_asked_alone():
    from app.agents.utils.workflow_batch import format_next_form_question_message

    message = format_next_form_question_message(
        "I will fill this form with you, one question at a time.",
        {"field_label": "What is the Petitioner's mailing address?"},
    )
    assert "What is the Petitioner's mailing address?" in message
    assert "1." not in message
    assert "2." not in message


def test_field_mapping_sources_and_targets():
    mapping = (
        '_FULL_NAME=join(" ", PLAINTIFF_1_FIRST_NAME, PLAINTIFF_1_LAST_NAME)|'
        "_CAUSE_NUMBER=CAUSE_NUMBER"
    )
    assert parse_field_mapping_targets(mapping) == ["_FULL_NAME", "_CAUSE_NUMBER"]
    assert parse_field_mapping_sources(mapping) == [
        "PLAINTIFF_1_FIRST_NAME",
        "PLAINTIFF_1_LAST_NAME",
        "CAUSE_NUMBER",
    ]


def test_merge_questions_uses_mapping_sources_not_extra_pdf_fields():
    document_questions = [
        {
            "field_name": "cause_number",
            "field_label": "What is the cause number?",
            "pdf_field": "CAUSE_NUMBER",
        },
        {
            "field_name": "random_pdf_box",
            "field_label": "Some unused PDF box",
            "pdf_field": "UNUSED_BOX",
        },
    ]
    mapping = (
        '_FULL_NAME=join(" ", PLAINTIFF_1_FIRST_NAME, PLAINTIFF_1_LAST_NAME)|'
        "_CAUSE_NUMBER=CAUSE_NUMBER"
    )
    merged = merge_document_and_mapping_questions(document_questions, mapping)
    names = [row["field_name"] for row in merged]
    labels = [row["field_label"] for row in merged]
    assert "random_pdf_box" not in names
    assert "What is the cause number?" in labels
    assert "plaintiff_1_first_name" in names
    assert "plaintiff_1_last_name" in names
