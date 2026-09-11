"""Mid-session chat corrections for collected details."""

from app.agents.conversation.orchestration.detail_corrections import (
    apply_chat_detail_corrections,
    looks_like_detail_correction,
)
from app.agents.conversation.orchestration.helpers import sync_checklist_from_answers
from app.agents.conversation.orchestration.state import (
    ChecklistItem,
    FilingSession,
    WorkflowChecklist,
)


def _session() -> FilingSession:
    session = FilingSession(conversation_id="c1", user_id="u1")
    session.workflow_questions = [
        {"field_name": "PETITIONER_FULL_NAME", "field_label": "Petitioner name"},
        {"field_name": "CHILDREN", "field_label": "Has children"},
        {
            "field_name": "CHILD_NAME",
            "field_label": "Child name",
            "visibility_condition": {"CHILDREN": "yes"},
        },
    ]
    session.checklist = WorkflowChecklist(
        items=[
            ChecklistItem(
                field_name="PETITIONER_FULL_NAME",
                label="Petitioner name",
                status="answered",
                value="Jane Doe",
            ),
            ChecklistItem(field_name="CHILDREN", label="Has children", status="answered", value="yes"),
            ChecklistItem(field_name="CHILD_NAME", label="Child name", status="pending"),
        ]
    )
    session.collected_answers = {
        "PETITIONER_FULL_NAME": "Jane Doe",
        "CHILDREN": "yes",
    }
    session.selections = {"case_type_name": "Divorce with children"}
    return session


def test_looks_like_detail_correction():
    assert looks_like_detail_correction("change name to John")
    assert looks_like_detail_correction(
        "change case subtype from divorce with children to divorce without children"
    )
    assert not looks_like_detail_correction("yes I have the documents")


def test_change_name_updates_collected_answer_and_checklist():
    session = _session()
    changed = apply_chat_detail_corrections(session, "change name to John")
    sync_checklist_from_answers(session)
    assert "PETITIONER_FULL_NAME" in changed
    assert session.collected_answers["PETITIONER_FULL_NAME"] == "John Doe"
    item = next(
        row for row in session.checklist.items if row.field_name == "PETITIONER_FULL_NAME"
    )
    assert item.status == "answered"
    assert item.value == "John Doe"


def test_change_to_without_children_updates_subtype():
    session = _session()
    changed = apply_chat_detail_corrections(
        session,
        "change case subtype from divorce with children to divorce without children",
    )
    sync_checklist_from_answers(session)
    assert "CHILDREN" in changed
    assert session.collected_answers["CHILDREN"] == "no"
    assert "without children" in str(session.selections.get("case_type_name") or "").lower()
    child = next(row for row in session.checklist.items if row.field_name == "CHILD_NAME")
    assert child.status == "skipped"
