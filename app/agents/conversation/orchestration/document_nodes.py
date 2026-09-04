"""Document offer, analysis-prefill, and generation nodes on the filing graph."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from typing import Any, Callable, Dict, List

from app.agents.conversation.orchestration.context import FilingOrchestratorContext
from app.agents.conversation.orchestration.helpers import (
    classify_document_offer_reply,
    format_required_document_list,
    merge_prefilled_answers,
    persist_system_state,
    required_templates,
    result_from_session,
    workflow_is_complete,
)
from app.agents.conversation.orchestration.prefill import map_analyses_to_answers
from app.agents.conversation.orchestration.session_manager import FilingSessionManager
from app.agents.conversation.orchestration.state import (
    FilingGraphState,
    FilingSession,
    OrchestratorResult,
)
from app.api.schemas.filing_events import FilingPhase
from app.core.prompts.document_offer import (
    AWAITING_UPLOAD_MESSAGE,
    DOCUMENT_OFFER_MESSAGE,
    DOCUMENT_OFFER_NO_TEMPLATES,
    DOCUMENT_OFFER_WITH_REQUIRED,
    DOCUMENTS_READY_NO_TEMPLATES,
    DOCUMENTS_READY_WITH_REQUIRED,
    MORE_UPLOADS_MESSAGE,
)
from app.core.prompts.workflow_questions import WORKFLOW_INTRO_USER_MESSAGE

logger = logging.getLogger(__name__)

NodeFn = Callable[[FilingGraphState], Any]


def _resolve_field_mapping(session: FilingSession) -> str:
    """Return field_mapping from session selections or required document config."""
    mapping = str(session.selections.get("field_mapping") or "").strip()
    if mapping:
        return mapping
    for doc in required_templates(session):
        doc_mapping = str(doc.get("field_mapping") or "").strip()
        if doc_mapping:
            return doc_mapping
    return ""


def _required_doc_names(session: FilingSession) -> List[str]:
    names = []
    for doc in required_templates(session):
        label = doc.get("template_name") or doc.get("template_code")
        if label:
            names.append(str(label))
    return names


def _offer_message(session: FilingSession) -> str:
    docs = required_templates(session)
    names = _required_doc_names(session)
    if names:
        return DOCUMENT_OFFER_WITH_REQUIRED.format(doc_list=", ".join(names))
    if not session.required_documents:
        return DOCUMENT_OFFER_NO_TEMPLATES
    return DOCUMENT_OFFER_MESSAGE


def _documents_ready_message(
    session: FilingSession,
    generated: List[Dict[str, Any]],
) -> str:
    docs = required_templates(session)
    doc_list = format_required_document_list(docs)
    filled_count = sum(
        1
        for d in generated
        if d.get("html_content") or d.get("ftl_content") or d.get("fields")
    )
    skipped = sum(1 for d in generated if d.get("skipped_because_uploaded"))
    errors = sum(1 for d in generated if d.get("error"))

    if not docs:
        return DOCUMENTS_READY_NO_TEMPLATES

    summary_parts = [f"Generated {filled_count} document(s)."]
    if skipped:
        summary_parts.append(f"Skipped {skipped} already uploaded.")
    if errors:
        summary_parts.append(f"{errors} could not be generated (missing template file or generation error).")
        failed = [
            d.get("template_name") or d.get("template_code")
            for d in generated
            if d.get("error")
        ]
        if failed:
            summary_parts.append("Failed: " + ", ".join(str(x) for x in failed if x) + ".")

    return DOCUMENTS_READY_WITH_REQUIRED.format(
        doc_list=doc_list,
        summary=" ".join(summary_parts),
    )


def _begin_workflow(session: FilingSession) -> str:
    session.phase = FilingPhase.COLLECTING_WORKFLOW_ANSWERS
    if workflow_is_complete(session) or not session.workflow_questions:
        return "generate_documents"
    return "workflow"


def build_document_nodes(ctx: FilingOrchestratorContext) -> Dict[str, NodeFn]:
    async def offer_documents_node(state: FilingGraphState) -> FilingGraphState:
        cid = state["conversation_id"]
        session = FilingSessionManager.get(cid)
        if not session:
            raise RuntimeError(f"No session for conversation {cid}")

        user_message = state.get("user_message") or ""
        await ctx.notify("offering_documents")
        intent = classify_document_offer_reply(user_message)

        if intent in ("no", "done"):
            next_node = _begin_workflow(session)
            if next_node == "generate_documents":
                session.phase = FilingPhase.GENERATING_DOCUMENTS
                await ctx.notify("generating_documents")
                await persist_system_state(ctx.conversation_repo, session)
                return {
                    **state,
                    "phase": session.phase.value,
                    "user_message": WORKFLOW_INTRO_USER_MESSAGE,
                    "skip_user_persist": True,
                    "next_node": "generate_documents",
                }
            await ctx.notify("collecting_workflow_answers")
            await persist_system_state(ctx.conversation_repo, session)
            return {
                **state,
                "phase": session.phase.value,
                "user_message": WORKFLOW_INTRO_USER_MESSAGE,
                "skip_user_persist": True,
                "next_node": "workflow",
            }

        if intent == "yes":
            session.phase = FilingPhase.AWAITING_DOCUMENT_UPLOAD
            await ctx.notify("awaiting_document_upload")
            result = result_from_session(
                session,
                AWAITING_UPLOAD_MESSAGE,
                event_kind="documents.offer",
                metadata={"required_documents": required_templates(session)},
            )
            await persist_system_state(ctx.conversation_repo, session)
            return {
                **state,
                "phase": session.phase.value,
                "result": result,
                "next_node": "persist",
            }

        if session.phase == FilingPhase.AWAITING_DOCUMENT_UPLOAD:
            result = result_from_session(
                session,
                AWAITING_UPLOAD_MESSAGE,
                event_kind="documents.offer",
                metadata={"required_documents": required_templates(session)},
            )
            return {
                **state,
                "phase": session.phase.value,
                "result": result,
                "next_node": "persist",
            }

        session.phase = FilingPhase.OFFERING_DOCUMENTS
        result = result_from_session(
            session,
            _offer_message(session),
            event_kind="documents.offer",
            metadata={"required_documents": required_templates(session)},
        )
        await persist_system_state(ctx.conversation_repo, session)
        return {
            **state,
            "phase": session.phase.value,
            "result": result,
            "next_node": "persist",
        }

    async def analyze_and_prefill_node(state: FilingGraphState) -> FilingGraphState:
        cid = state["conversation_id"]
        session = FilingSessionManager.get(cid)
        if not session:
            raise RuntimeError(f"No session for conversation {cid}")

        results = list(state.get("analysis_results") or [])
        successful = [r for r in results if r.get("ok") and r.get("analysis")]
        failed = [r for r in results if not r.get("ok")]

        for item in results:
            session.uploaded_documents.append(
                {
                    "file_name": item.get("file_name"),
                    "classification": item.get("classification"),
                    "ok": item.get("ok", False),
                    "error": item.get("error"),
                    "analysis": item.get("analysis"),
                }
            )

        analyses = [r["analysis"] for r in successful]
        if results:
            await ctx.notify("analyzing_upload")
        if analyses:
            await ctx.notify("prefilling_answers")
        mapped = await map_analyses_to_answers(
            ctx.bedrock,
            session.workflow_questions,
            analyses,
            session.collected_answers,
        )
        newly = merge_prefilled_answers(session, mapped)
        session.metadata["last_prefilled_fields"] = newly

        names = [r.get("file_name") for r in successful]
        fail_note = ""
        if failed:
            fail_note = " Some files could not be analyzed: " + ", ".join(
                str(f.get("file_name") or "file") for f in failed
            )

        if workflow_is_complete(session) or not session.workflow_questions:
            session.phase = FilingPhase.GENERATING_DOCUMENTS
            await persist_system_state(ctx.conversation_repo, session)
            return {
                **state,
                "phase": session.phase.value,
                "next_node": "generate_documents",
            }

        session.phase = FilingPhase.AWAITING_DOCUMENT_UPLOAD
        if newly:
            message = MORE_UPLOADS_MESSAGE.format(count=len(newly)) + fail_note
        elif successful:
            message = (
                "I reviewed "
                + ", ".join(str(n) for n in names if n)
                + " but could not pre-fill additional workflow fields."
                + fail_note
                + " You can upload more files, or say that is all to continue."
            )
        else:
            message = (
                "I could not analyze the uploaded file(s)."
                + fail_note
                + " Please try another PDF or Word file, or say that is all to continue with questions."
            )

        combined = successful[0]["analysis"] if successful else {}
        result = result_from_session(
            session,
            message,
            event_kind="analysis.complete",
            metadata={
                "prefilled_fields": newly,
                "files": results,
                "required_documents": required_templates(session),
            },
            analysis=combined,
        )
        await persist_system_state(ctx.conversation_repo, session)
        return {
            **state,
            "phase": session.phase.value,
            "result": result,
            "next_node": "persist",
        }

    async def generate_documents_node(state: FilingGraphState) -> FilingGraphState:
        cid = state["conversation_id"]
        session = FilingSessionManager.get(cid)
        if not session:
            raise RuntimeError(f"No session for conversation {cid}")

        session.phase = FilingPhase.GENERATING_DOCUMENTS
        await ctx.notify("generating_documents")
        from app.services.document_generation_v2_service import (
            DocumentGenerationV2Service,
        )

        extracted_fields = session.selections.get("extracted_form_questions") or [
            {
                "field": q.get("pdf_field") or q.get("field") or q.get("field_label"),
                "question": q.get("field_label") or q.get("question"),
                "page": q.get("page"),
                "answer": session.collected_answers.get(q.get("field_name"), ""),
            }
            for q in (
                session.metadata.get("workflow_questions_full")
                or session.workflow_questions
            )
        ]
        answers = dict(session.collected_answers)
        for question in (
            session.metadata.get("workflow_questions_full")
            or session.workflow_questions
        ):
            value = answers.get(question.get("field_name"))
            if value in (None, ""):
                continue
            pdf_field = question.get("pdf_field") or question.get("field")
            if pdf_field:
                answers.setdefault(str(pdf_field), value)
            label = question.get("field_label") or question.get("question")
            if label:
                answers.setdefault(str(label), value)

        file_name = str(
            session.selections.get("template_code")
            or session.selections.get("document_type_name")
            or "court_form.json"
        )
        generated: List[Dict[str, Any]] = []
        try:
            filled = await DocumentGenerationV2Service().generate(
                extracted_text=str(session.selections.get("extracted_form_text") or ""),
                extracted_fields=extracted_fields,
                answers=answers,
                file_name=file_name,
            )
            field_mapping = _resolve_field_mapping(session)
            if not field_mapping:
                raise RuntimeError(
                    "The selected document template has no field_mapping, "
                    "so JSON cannot be generated."
                )
            from app.services.field_mapping_service import FieldMappingService

            await ctx.notify("mapping_fields")
            mapping_result = await FieldMappingService(ctx.bedrock).map_fields(
                field_mapping=field_mapping,
                workflow_questions=session.workflow_questions,
                collected_answers=session.collected_answers,
                filled_fields=filled.fields,
            )
            mapped_fields = mapping_result.fields
            output_fields = mapped_fields
            field_mapping_validation = {
                "is_complete": mapping_result.is_complete,
                "expected_targets": mapping_result.expected_targets,
                "missing_from_llm": mapping_result.missing_from_llm,
                "backfilled_targets": mapping_result.backfilled_targets,
                "empty_targets": mapping_result.empty_targets,
            }
            generated.append(
                {
                    "template_code": session.selections.get("template_code"),
                    "template_name": session.selections.get("document_type_name"),
                    "file_name": file_name,
                    "document_id": filled.document_id,
                    "fields": output_fields,
                    "pdf_fields": filled.fields,
                    "mapped_fields": mapped_fields or None,
                    "field_mapping_validation": field_mapping_validation,
                    "source": "document_generation_v2",
                    "skipped_because_uploaded": False,
                }
            )
            message = (
                "Filing answers are complete. Here is the filled form JSON.\n\n"
                + json.dumps(output_fields, indent=2, ensure_ascii=False)
            )
            await ctx.notify("documents_ready")
        except Exception as exc:  # noqa: BLE001
            logger.exception("Document generation 2.0 failed in chat")
            generated.append(
                {
                    "template_code": session.selections.get("template_code"),
                    "template_name": session.selections.get("document_type_name"),
                    "file_name": file_name,
                    "skipped_because_uploaded": False,
                    "error": str(exc),
                    "source": "document_generation_v2",
                }
            )
            message = (
                "I collected your answers but could not build the form JSON. "
                f"{exc}"
            )
            await ctx.notify("error", message=message, level="error")

        session.generated_documents = generated
        session.phase = FilingPhase.COMPLETE
        await ctx.conversation_repo.complete_conversation(session.conversation_id)
        await persist_system_state(ctx.conversation_repo, session)

        result = OrchestratorResult(
            assistant_message=message,
            conversation_id=session.conversation_id,
            phase=session.phase,
            mode=session.mode,
            selections=session.selections,
            collected_answers=session.collected_answers,
            checklist=session.checklist.to_payload(),
            event_kind="documents.ready",
            metadata={
                "generated_documents": generated,
                "required_documents": required_templates(session),
            },
        )
        return {
            **state,
            "phase": session.phase.value,
            "result": result,
            "next_node": "persist",
        }

    return {
        "offer_documents": offer_documents_node,
        "analyze_and_prefill": analyze_and_prefill_node,
        "generate_documents": generate_documents_node,
    }


async def _fill_template(
    ctx: FilingOrchestratorContext,
    blob: bytes,
    file_name: str,
    field_data: Dict[str, Any],
    *,
    template_format: str = "pdf",
) -> Dict[str, Any]:
    fmt = (template_format or "pdf").lower()
    if fmt == "ftl":
        text = blob.decode("utf-8", errors="replace")
        response = await ctx.generation_service.generate_filled_ftl(
            text, file_name, field_data
        )
        return {"ftl_content": response.ftl_content, "html_content": None}

    pdf_bytes = blob
    source_type = "pdf"
    if fmt == "docx":
        source_type = "docx"
        suffix = ".docx" if file_name.lower().endswith(".docx") else ".doc"
        tmp_path = ""
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(blob)
                tmp_path = tmp.name
            pdf_bytes = await ctx.generation_service.docx_to_pdf(tmp_path)
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    response = await ctx.generation_service.generate_filled_html(
        pdf_bytes, file_name, field_data, source_file_type=source_type
    )
    return {"html_content": response.html_content, "ftl_content": None}
