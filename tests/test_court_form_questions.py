"""Tests for court-form Textract question conversion."""

from types import SimpleNamespace

import pytest

from app.services.court_form_question_service import (
    CourtFormQuestionService,
    _is_textract_unavailable,
    field_label_to_question,
    parse_s3_location,
)


def test_field_label_to_question():
    assert field_label_to_question("Cause Number:") == "What is the Cause Number?"
    assert field_label_to_question("Attorney") == "What is an Attorney?"
    assert field_label_to_question("Is this an emergency?") == "Is this an emergency?"
    assert field_label_to_question("Are you the petitioner") == "Are you the petitioner?"


def test_parse_s3_location():
    assert parse_s3_location("s3://my-bucket/forms/divorce.pdf") == (
        "my-bucket",
        "forms/divorce.pdf",
    )
    assert parse_s3_location(
        "https://my-bucket.s3.us-east-2.amazonaws.com/forms/divorce.pdf"
    ) == ("my-bucket", "forms/divorce.pdf")
    assert parse_s3_location(
        "https://s3.us-east-2.amazonaws.com/my-bucket/forms/divorce.pdf"
    ) == ("my-bucket", "forms/divorce.pdf")
    assert parse_s3_location("https://example.com/file.pdf") == (None, None)


def test_questions_from_forms_are_simple_strings():
    extraction = SimpleNamespace(
        pages_data=[
            SimpleNamespace(
                page_number=1,
                forms=[
                    SimpleNamespace(key="Petitioner Name", value="Jane Doe"),
                    SimpleNamespace(key="Cause Number", value=""),
                    SimpleNamespace(key="Petitioner Name", value="duplicate"),
                ],
            )
        ]
    )
    questions = CourtFormQuestionService.questions_from_forms(extraction)
    assert [q.field for q in questions] == ["Petitioner Name", "Cause Number"]
    assert questions[0].question == "What is the Petitioner Name?"
    assert questions[0].answer == "Jane Doe"
    assert questions[1].answer == ""


@pytest.mark.asyncio
async def test_extract_questions_uses_local_textract_not_s3():
    from app.api.schemas.document import FileType

    class _Extractor:
        def __init__(self):
            self.called = False

        async def extract_from_bytes(self, file_bytes, file_name, file_type, document_id):
            self.called = True
            assert file_bytes == b"%PDF-fake"
            return SimpleNamespace(
                document_id=document_id,
                pages_data=[
                    SimpleNamespace(
                        page_number=1,
                        text_content="Cause Number 1",
                        forms=[SimpleNamespace(key="Cause Number", value="1")],
                    )
                ],
            )

    class _LLM:
        async def _invoke_with_timeout(self, body):
            raise RuntimeError("skip llm")

    extractor = _Extractor()
    service = CourtFormQuestionService(
        extractor=extractor,
        llm_service=_LLM(),
        s3_manager=SimpleNamespace(),
    )
    result = await service.extract_questions(
        file_bytes=b"%PDF-fake",
        file_name="form.pdf",
        file_type=FileType.PDF,
        file_path="/tmp/unused.pdf",
        source="upload",
    )
    assert extractor.called
    assert result.questions[0].answer == "1"
    assert result.source == "upload"


def test_questions_from_llm_output_string():
    payload = {
        "output": '{"questions":[{"question":"What is the cause number?","answer":"1","field":"Cause Number","page":1}]}',
        "error": "Failed to parse JSON response",
    }
    questions = CourtFormQuestionService._questions_from_llm_payload(payload)
    assert questions[0].answer == "1"
    assert questions[0].field == "Cause Number"
    err = RuntimeError(
        "Image processing failed: An error occurred (SubscriptionRequiredException) "
        "when calling the AnalyzeDocument operation: The AWS Access Key Id needs a "
        "subscription for the service"
    )
    assert _is_textract_unavailable(err)


@pytest.mark.asyncio
async def test_extract_questions_falls_back_when_textract_unsubscribed(monkeypatch):
    from app.api.schemas.document import FileType

    class _Extractor:
        async def extract_from_bytes(self, **kwargs):
            raise RuntimeError(
                "Image processing failed: An error occurred (SubscriptionRequiredException) "
                "when calling the AnalyzeDocument operation: The AWS Access Key Id needs a "
                "subscription for the service"
            )

    class _LLM:
        async def _invoke_with_timeout(self, body):
            return {
                "questions": [
                    {
                        "question": "What is the cause number?",
                        "answer": "2025-1",
                        "field": "Cause Number",
                        "page": 1,
                    }
                ]
            }

    monkeypatch.setattr(
        CourtFormQuestionService,
        "_local_pdf_extraction",
        staticmethod(
            lambda file_bytes, file_name, document_id: SimpleNamespace(
                document_id=document_id,
                pages_data=[
                    SimpleNamespace(page_number=1, text_content="Cause Number: 2025-1")
                ],
            )
        ),
    )
    service = CourtFormQuestionService(
        extractor=_Extractor(),
        llm_service=_LLM(),
        s3_manager=SimpleNamespace(),
    )
    result = await service.extract_questions(
        file_bytes=b"%PDF-fake",
        file_name="form.pdf",
        file_type=FileType.PDF,
        file_path="/tmp/unused.pdf",
        source="upload",
    )
    assert "Textract is not subscribed" in result.message
    assert result.questions[0].answer == "2025-1"
