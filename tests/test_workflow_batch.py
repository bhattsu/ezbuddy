"""Tests for workflow question batching."""

from app.agents.utils.workflow_batch import batch_pending_questions


class _Item:
    def __init__(self, field_name, label, sort_order=0, status="pending", required=True):
        self.field_name = field_name
        self.label = label
        self.sort_order = sort_order
        self.status = status
        self.required = required


def test_batch_consecutive_text_questions():
    questions = [
        {"field_name": "PETITIONER_FULL_NAME", "field_label": "Petitioner name", "question_type": "TEXT", "sort_order": 1},
        {"field_name": "PETITIONER_ADDRESS", "field_label": "Petitioner address", "question_type": "TEXT", "sort_order": 2},
        {"field_name": "HAS_CHILDREN", "field_label": "Has children", "question_type": "BOOLEAN", "sort_order": 3},
    ]
    items = [
        _Item("PETITIONER_FULL_NAME", "Petitioner name", 1),
        _Item("PETITIONER_ADDRESS", "Petitioner address", 2),
        _Item("HAS_CHILDREN", "Has children", 3),
    ]
    batch = batch_pending_questions(
        checklist_items=items,
        workflow_questions=questions,
        collected_answers={},
        max_batch=3,
    )
    assert len(batch) == 2
    assert batch[0]["field_name"] == "PETITIONER_FULL_NAME"
    assert batch[1]["field_name"] == "PETITIONER_ADDRESS"
