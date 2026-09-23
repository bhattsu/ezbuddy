import pytest

from app.agents.conversation.orchestration.detail_corrections import (
    apply_chat_detail_corrections,
)
from app.agents.conversation.orchestration.state import FilingSession
from app.services.document_template_match import (
    _party_names_from_selections,
    _parse_marriage_case_title,
    apply_known_case_answers,
    is_full_person_name_field,
    is_usable_party_name,
)


def test_parse_marriage_title_splits_parties():
    title = (
        "In The Matter Of The Marriage Of PLAINTIFF PARTY, II , DEFENDANT PARTY"
    )
    p, r = _parse_marriage_case_title(title)
    assert p == "PARTY"
    assert r == "PARTY"


def test_is_usable_party_name_rejects_placeholders():
    assert not is_usable_party_name("PARTY")
    assert not is_usable_party_name("PLAINTIFF PARTY")
    assert is_usable_party_name("Jane Q Public")


def test_party_names_from_existing_case_parties():
    selections = {
        "case_title": "In The Matter Of The Marriage Of PLAINTIFF PARTY, II , DEFENDANT PARTY",
        "existing_case_parties": [
            {"id": "1", "name": "PLAINTIFF  PARTY", "party_type": "Plaintiff"},
            {"id": "2", "name": "DEFENDANT  PARTY", "party_type": "Defendant"},
        ],
        "filing_party_id": "1",
        "jurisdiction_name": "Refugio County - District Clerk",
    }
    petitioner, respondent = _party_names_from_selections(selections)
    assert petitioner == "PARTY"
    assert respondent == "PARTY"


def test_apply_known_case_answers_skips_placeholder_party_names():
    questions = [
        {
            "field_name": "_PLAINTIFF_1_FULL_NAME",
            "field_label": "What is the full legal name of the Plaintiff?",
        },
        {
            "field_name": "_DEFENDANT_1_FULL_NAME",
            "field_label": "What is the full legal name of the Defendant?",
        },
    ]
    selections = {
        "existing_case_parties": [
            {"id": "1", "name": "PLAINTIFF  PARTY", "party_type": "Plaintiff"},
            {"id": "2", "name": "DEFENDANT  PARTY", "party_type": "Defendant"},
        ],
        "jurisdiction_name": "Refugio County - District Clerk",
        "case_type_name": "Divorce No Children",
    }
    answers, _skipped = apply_known_case_answers(questions, selections)
    assert "_PLAINTIFF_1_FULL_NAME" not in answers
    assert "_DEFENDANT_1_FULL_NAME" not in answers


def test_is_full_person_name_field():
    assert is_full_person_name_field(
        "_PLAINTIFF_1_FULL_NAME", "What is the full legal name of the Plaintiff?"
    )
    assert not is_full_person_name_field(
        "_NAME_CHANGE", "Does the Plaintiff want to request a name change?"
    )


@pytest.mark.asyncio
async def test_change_me_name_updates_plaintiff_full_name_only():
    session = FilingSession("c1", "u1")
    session.selections = {
        "filing_party_id": "1",
        "existing_case_parties": [
            {"id": "1", "name": "PLAINTIFF  PARTY", "party_type": "Plaintiff"},
            {"id": "2", "name": "DEFENDANT  PARTY", "party_type": "Defendant"},
        ],
    }
    session.workflow_questions = [
        {
            "field_name": "_PLAINTIFF_1_FULL_NAME",
            "field_label": "What is the full legal name of the Plaintiff?",
        },
        {
            "field_name": "_DEFENDANT_1_FULL_NAME",
            "field_label": "What is the full legal name of the Defendant?",
        },
        {
            "field_name": "_NAME_CHANGE",
            "field_label": "Does the Plaintiff want to request a name change?",
        },
        {
            "field_name": "_PLAINTIFF_1_ZIPCODE",
            "field_label": "What is the Plaintiff's ZIP code?",
        },
    ]
    session.collected_answers = {
        "_PLAINTIFF_1_FULL_NAME": "",
        "_DEFENDANT_1_FULL_NAME": "Other",
        "_NAME_CHANGE": "no",
        "_PLAINTIFF_1_ZIPCODE": "644564",
    }
    changed = await apply_chat_detail_corrections(
        session, "Change me name as Gowdham S", bedrock=None
    )
    assert changed == ["_PLAINTIFF_1_FULL_NAME"]
    assert session.collected_answers["_PLAINTIFF_1_FULL_NAME"] == "Gowdham S"
    assert session.collected_answers["_DEFENDANT_1_FULL_NAME"] == "Other"
    assert session.collected_answers["_NAME_CHANGE"] == "no"
    assert session.collected_answers["_PLAINTIFF_1_ZIPCODE"] == "644564"
