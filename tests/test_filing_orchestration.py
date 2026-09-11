"""Tests for unified filing orchestration helpers, router, and prefill mapping."""

from __future__ import annotations

import asyncio
from typing import Any, List

import pytest

from app.agents.conversation.orchestration.helpers import (
    analyze_uploads_concurrently,
    classify_document_offer_reply,
    merge_prefilled_answers,
    sync_checklist_from_answers,
    uploaded_covers_template,
    workflow_is_complete,
)
from app.agents.conversation.orchestration.prefill import heuristic_map_analysis_to_answers
from app.agents.conversation.orchestration.router import (
    route_after_analyze,
    route_after_message_prepare,
    route_after_offer,
    route_after_upload,
    route_after_workflow,
    route_entry,
)
from app.agents.conversation.orchestration.state import (
    ChecklistItem,
    FilingSession,
    WorkflowChecklist,
)
from app.api.schemas.filing_events import FilingPhase, UserUploadEvent
from app.api.schemas.legal_filing import DocumentAnalysisResponse


def test_route_entry_triggers():
    assert route_entry({"trigger": "connect"}) == "connect"
    assert route_entry({"trigger": "upload"}) == "upload"
    assert route_entry({"trigger": "message"}) == "message_prepare"


def test_route_after_message_prepare_offer_and_workflow():
    assert (
        route_after_message_prepare({"phase": FilingPhase.OFFERING_DOCUMENTS.value})
        == "offer_documents"
    )
    assert (
        route_after_message_prepare(
            {"phase": FilingPhase.AWAITING_DOCUMENT_UPLOAD.value}
        )
        == "offer_documents"
    )
    assert (
        route_after_message_prepare(
            {"phase": FilingPhase.COLLECTING_WORKFLOW_ANSWERS.value}
        )
        == "workflow"
    )
    assert (
        route_after_message_prepare(
            {"phase": FilingPhase.VERIFYING_PLATFORM_PAYMENT.value}
        )
        == "verify_payment"
    )
    assert (
        route_after_message_prepare(
            {"phase": FilingPhase.VERIFYING_COURT_PAYMENT.value}
        )
        == "verify_payment"
    )
    assert (
        route_after_message_prepare({"phase": FilingPhase.SELECTING_STATE.value})
        == "navigation"
    )
    assert (
        route_after_message_prepare(
            {
                "phase": FilingPhase.SELECTING_STATE.value,
                "next_node": "persist",
            }
        )
        == "persist"
    )
    assert (
        route_after_message_prepare(
            {
                "phase": FilingPhase.OFFERING_DOCUMENTS.value,
                "user_message": "change name to John",
            }
        )
        == "workflow"
    )


def test_route_offer_upload_analyze_workflow():
    assert route_after_offer({"next_node": "workflow"}) == "workflow"
    assert route_after_offer({"next_node": "generate_documents"}) == "generate_documents"
    assert route_after_offer({}) == "persist"
    assert route_after_upload({"next_node": "analyze_and_prefill"}) == "analyze_and_prefill"
    assert route_after_upload({}) == "persist"
    assert route_after_analyze({"next_node": "generate_documents"}) == "generate_documents"
    assert route_after_analyze({}) == "persist"
    assert route_after_workflow({"next_node": "generate_documents"}) == "generate_documents"
    assert route_after_workflow({}) == "persist"


def test_classify_document_offer_reply():
    assert classify_document_offer_reply("yes I have documents") == "yes"
    assert classify_document_offer_reply("No, I don't have any") == "no"
    assert classify_document_offer_reply("that's all") == "done"
    assert classify_document_offer_reply("[document_offer]") == "unknown"


def test_user_upload_event_iter_files_single_and_batch():
    single = UserUploadEvent(
        conversation_id="c1",
        file_name="a.pdf",
        content_base64="YQ==",
    )
    assert len(single.iter_files()) == 1
    batch = UserUploadEvent(
        conversation_id="c1",
        files=[
            {"file_name": "a.pdf", "content_base64": "YQ=="},
            {"file_name": "b.pdf", "content_base64": "Yg=="},
        ],
    )
    assert [f.file_name for f in batch.iter_files()] == ["a.pdf", "b.pdf"]


def test_heuristic_map_and_unmatched_stay_pending():
    questions = [
        {"field_name": "PETITIONER_FULL_NAME", "field_label": "Petitioner"},
        {"field_name": "HAS_CHILDREN", "field_label": "Children"},
    ]
    analyses = [
        {
            "extracted_fields": {"PETITIONER_FULL_NAME": "Jane Doe"},
            "user_details": {},
        }
    ]
    mapped = heuristic_map_analysis_to_answers(questions, analyses, {})
    assert mapped["PETITIONER_FULL_NAME"] == "Jane Doe"
    assert "HAS_CHILDREN" not in mapped


def test_merge_prefill_does_not_overwrite_and_completes_workflow():
    session = FilingSession(conversation_id="c1", user_id="u1")
    session.workflow_questions = [
        {"field_name": "PETITIONER_FULL_NAME", "required": True},
        {"field_name": "COUNTY", "required": True},
    ]
    session.checklist = WorkflowChecklist(
        items=[
            ChecklistItem(field_name="PETITIONER_FULL_NAME", label="Name", required=True),
            ChecklistItem(field_name="COUNTY", label="County", required=True),
        ]
    )
    session.collected_answers = {"PETITIONER_FULL_NAME": "Jane Doe"}
    session.checklist.items[0].status = "answered"
    session.checklist.items[0].value = "Jane Doe"

    newly = merge_prefilled_answers(
        session,
        {"PETITIONER_FULL_NAME": "Other", "COUNTY": "Travis"},
    )
    assert session.collected_answers["PETITIONER_FULL_NAME"] == "Jane Doe"
    assert newly == {"COUNTY": "Travis"}
    assert workflow_is_complete(session)


def test_uploaded_covers_template_by_classification():
    uploaded = [{"classification": "petition", "file_name": "scan.pdf", "ok": True}]
    template = {"template_code": "PETITION", "template_name": "Original Petition"}
    assert uploaded_covers_template(uploaded, template)
    assert not uploaded_covers_template(
        uploaded, {"template_code": "SUMMONS", "template_name": "Citation"}
    )


@pytest.mark.asyncio
async def test_analyze_uploads_concurrent_one_failure_does_not_drop_rest():
    class FakeAnalysis:
        def __init__(self) -> None:
            self.calls: List[str] = []
            self.in_flight = 0
            self.max_in_flight = 0

        async def analyze_document(self, **kwargs: Any) -> DocumentAnalysisResponse:
            name = kwargs["file_name"]
            self.calls.append(name)
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
            await asyncio.sleep(0.05)
            self.in_flight -= 1
            if name == "bad.pdf":
                raise RuntimeError("boom")
            return DocumentAnalysisResponse(
                document_id=name,
                file_name=name,
                document_classification="petition",
            )

    fake = FakeAnalysis()
    uploads = [
        {"file_name": "a.pdf", "file_bytes": b"a", "file_path": "", "file_type": None},
        {"file_name": "bad.pdf", "file_bytes": b"b", "file_path": "", "file_type": None},
        {"file_name": "c.pdf", "file_bytes": b"c", "file_path": "", "file_type": None},
    ]
    results = await analyze_uploads_concurrently(fake, uploads, concurrency=3)
    assert len(results) == 3
    assert fake.max_in_flight >= 2
    oks = {r["file_name"]: r["ok"] for r in results}
    assert oks["a.pdf"] is True
    assert oks["c.pdf"] is True
    assert oks["bad.pdf"] is False


def test_template_format_from_s3_key_ftl():
    from app.services.legal_filing_repository import (
        _normalize_template_rows,
        template_format_from_path,
    )

    assert template_format_from_path("PETITION.pdf", "documents-repo/petition.ftl") == "ftl"
    assert template_format_from_path("form.pdf", "documents-repo/form.pdf") == "pdf"
    assert template_format_from_path("form.docx", None) == "docx"

    rows = _normalize_template_rows([
        {
            "template_code": "PETITION",
            "template_name": "Original Petition for Divorce",
            "is_required": True,
            "s3_bucket": "goml-uslegalpro-dev",
            "s3_key": "documents-repo/petition.ftl",
            "template_version": 1,
        }
    ])
    assert rows[0]["file_name"] == "petition.ftl"
    assert rows[0]["template_format"] == "ftl"


def test_sync_checklist_updates_skipped_when_answer_changes():
    session = FilingSession(conversation_id="c1", user_id="u1")
    session.workflow_questions = [
        {"field_name": "PETITIONER_FULL_NAME", "field_label": "Name", "required": True},
        {"field_name": "CHILDREN", "field_label": "Has children", "required": True},
    ]
    session.checklist = WorkflowChecklist(
        items=[
            ChecklistItem(field_name="PETITIONER_FULL_NAME", label="Name", status="skipped"),
            ChecklistItem(field_name="CHILDREN", label="Has children", status="pending"),
        ]
    )
    session.collected_answers = {
        "PETITIONER_FULL_NAME": "John Doe",
        "CHILDREN": "no",
    }
    sync_checklist_from_answers(session)
    by_name = {item.field_name: item for item in session.checklist.items}
    assert by_name["PETITIONER_FULL_NAME"].status == "answered"
    assert by_name["PETITIONER_FULL_NAME"].value == "John Doe"
    assert by_name["CHILDREN"].status == "answered"
    payload = session.checklist.to_payload()
    assert payload.answered == 2
    assert payload.total == 2


def test_build_filing_graph_has_unified_nodes():
    from app.agents.conversation.orchestration.context import FilingOrchestratorContext
    from app.agents.conversation.orchestration.graph import build_filing_graph

    ctx = FilingOrchestratorContext.create()
    graph = build_filing_graph(ctx)
    names = set(graph.get_graph().nodes.keys())
    for node in (
        "connect",
        "offer_documents",
        "analyze_and_prefill",
        "generate_documents",
        "verify_payment",
        "workflow",
        "persist",
    ):
        assert node in names
