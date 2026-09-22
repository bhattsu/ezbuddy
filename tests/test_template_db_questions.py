from app.services.document_template_match import (
    parse_template_questions_text,
    workflow_questions_from_db_column,
)


def test_parse_template_questions_text_skips_blank_lines():
    text = "Line one?\n\nLine two?\n  \nLine three?"
    assert parse_template_questions_text(text) == [
        "Line one?",
        "Line two?",
        "Line three?",
    ]


def test_workflow_questions_from_db_column_aligns_to_mapping():
    mapping = (
        '{"form_data":{"_PLAINTIFF_1_FULL_NAME":"","_DEFENDANT_1_FULL_NAME":""}}'
    )
    questions = "Plaintiff name?\nDefendant name?"
    rows = workflow_questions_from_db_column(questions, mapping)
    assert len(rows) == 2
    assert rows[0]["field_name"] == "_PLAINTIFF_1_FULL_NAME"
    assert rows[0]["field_label"] == "Plaintiff name?"
    assert rows[1]["field_name"] == "_DEFENDANT_1_FULL_NAME"
    assert rows[1]["field_label"] == "Defendant name?"
