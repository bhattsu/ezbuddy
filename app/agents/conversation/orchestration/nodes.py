"""LangGraph nodes for filing orchestration."""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any, Callable, Dict, List, Optional

from app.agents.conversation.orchestration.context import FilingOrchestratorContext
from app.agents.conversation.orchestration.chat_context import (
    append_chat_turn,
    history_for_llm,
)
from app.agents.conversation.orchestration.helpers import (
    advance_mode_from_intent,
    advance_phase_after_selections,
    analyze_uploads_concurrently,
    apply_catalog_court_from_text,
    apply_catalog_shortcuts,
    apply_existing_search_attrs,
    attach_selection_options_to_result,
    build_checklist_from_questions,
    cache_phase_options,
    capture_new_case_topic,
    get_session,
    handle_lookup,
    init_workflow_phase,
    is_affirmative_reply,
    is_new_filing_prefill_phase,
    load_db_options,
    load_history,
    merge_checklist_updates,
    merge_selections,
    next_pending_question,
    options_for_response,
    persist_system_state,
    prefetch_court_catalog,
    restore_from_system_messages,
    result_from_session,
    uploads_from_state,
    workflow_is_complete,
)
from app.agents.conversation.orchestration.session_manager import FilingSessionManager
from app.agents.conversation.orchestration.state import (
    FilingGraphState,
    FilingSession,
    OrchestratorResult,
)
from app.agents.conversation.orchestration.document_nodes import build_document_nodes
from app.agents.conversation.orchestration.payment_nodes import build_payment_nodes
from app.agents.utils.db_options_format import (
    build_phase_selection_message,
    filter_selections_update,
    match_option,
    option_label,
    selection_update_for_option,
)
from app.agents.utils.text_sanitize import sanitize_assistant_text
from app.agents.utils.workflow_batch import (
    batch_pending_questions,
    compact_form_questions,
    format_next_form_question_message,
    list_all_pending_questions,
)
from app.adapters.uslegalpro.tokens import resolve_auth_token
from app.api.schemas.filing_events import FilingMode, FilingPhase
from app.core.prompts.document_offer import DOCUMENT_OFFER_USER_MESSAGE
from app.core.prompts.filing_assistant import (
    GREETING_USER_MESSAGE,
    POST_STATE_HELP_MESSAGE,
    WELCOME_SELECT_STATE_MESSAGE,
)
from app.services.document_template_match import (
    apply_known_case_answers,
    merge_document_and_mapping_questions,
    template_questions_to_workflow,
)
from app.services.process_notifications import loading_process_for_phase

logger = logging.getLogger(__name__)

NodeFn = Callable[[FilingGraphState], Any]
_ENVELOPE_ID_RE = re.compile(r"\b[A-Za-z0-9_-]{3,64}\b")


def _session_snapshot(session: FilingSession) -> Dict[str, str]:
    return {
        "phase": session.phase.value,
        "mode": session.mode.value,
    }


SELECTION_PHASES = (
    FilingPhase.SELECTING_STATE,
    FilingPhase.SELECTING_COUNTY,
    FilingPhase.SELECTING_JURISDICTION,
    FilingPhase.SELECTING_CASE_CATEGORY,
    FilingPhase.SELECTING_CASE_TYPE,
    FilingPhase.SELECTING_CASE_PARTIES,
    FilingPhase.SELECTING_FILER_TYPE,
    FilingPhase.SELECTING_FILING_CODE,
    FilingPhase.SELECTING_DOCUMENT_TYPE,
    FilingPhase.SELECTING_FILING_TYPE,
    FilingPhase.EXISTING_SELECTING_STATE,
    FilingPhase.EXISTING_SELECTING_JURISDICTION,
)


def _format_candidate_message(candidates: List[Dict[str, Any]]) -> str:
    count = len(candidates)
    if count == 1:
        label = option_label(candidates[0])
        return f"Please confirm your selection: {label}"
    return (
        f"{count} options match what you entered. "
        "Please choose one from the dropdown."
    )


def _format_existing_case_confirmation(
    search_item: Dict[str, Any], case_detail: Dict[str, Any]
) -> str:
    """Build a concise confirmation from search and detail API responses."""
    case_number = (
        case_detail.get("case_number")
        or search_item.get("case_number")
        or "unknown"
    )
    title = (
        case_detail.get("case_title")
        or case_detail.get("title")
        or search_item.get("case_title")
        or "Untitled case"
    )
    jurisdiction = (
        case_detail.get("jurisdiction_display")
        or search_item.get("jurisdiction_display")
        or search_item.get("jurisdiction")
        or "unknown jurisdiction"
    )
    case_type = (
        case_detail.get("case_type_display")
        or search_item.get("case_type_display")
        or search_item.get("case_type")
        or "unknown case type"
    )
    parties = case_detail.get("case_parties") or []
    party_names = []
    for party in parties[:6]:
        if not isinstance(party, dict):
            continue
        name = (
            party.get("name")
            or party.get("full_name")
            or " ".join(
                str(party.get(key) or "").strip()
                for key in ("first_name", "middle_name", "last_name")
            ).strip()
        )
        if name:
            party_names.append(str(name))
    party_text = (
        "\nParties: " + ", ".join(party_names) if party_names else ""
    )
    return (
        "I found this case:\n"
        f"- Case number: {case_number}\n"
        f"- Title: {title}\n"
        f"- Jurisdiction: {jurisdiction}\n"
        f"- Case type: {case_type}"
        f"{party_text}\n\nIs this the correct case?"
    )


def _extract_envelope_id(text: str) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    numeric = re.findall(r"\b\d{4,}\b", raw)
    if numeric:
        return numeric[-1]
    tokens = _ENVELOPE_ID_RE.findall(raw)
    return tokens[-1] if tokens else ""


def _envelope_status_message(item: Dict[str, Any], envelope_id: str) -> str:
    status = str(item.get("status") or item.get("envelope_status") or "unknown").strip()
    submitted_on = str(item.get("submitted_on") or "").strip()
    case_number = str(item.get("case_number") or "").strip()
    reason = str(item.get("status_reason") or item.get("reviewer_comment") or "").strip()
    parts = [f"The filing envelope {envelope_id} is currently {status}."]
    if submitted_on:
        parts.append(f"It was submitted on {submitted_on}.")
    if case_number:
        parts.append(f"Case number: {case_number}.")
    filings = item.get("filings")
    if isinstance(filings, list) and filings:
        filing_states = [
            str(row.get("status") or "").strip()
            for row in filings
            if isinstance(row, dict) and str(row.get("status") or "").strip()
        ]
        if filing_states:
            parts.append("Document statuses: " + ", ".join(filing_states) + ".")
    if reason:
        parts.append(f"Court note: {reason}.")
    return " ".join(parts)


async def _handle_status_check(
    ctx: FilingOrchestratorContext,
    session: FilingSession,
    user_message: str,
    lookup_params: Dict[str, Any],
) -> tuple[str, Dict[str, Any]]:
    envelope_id = str(
        lookup_params.get("envelope_id")
        or session.selections.get("envelope_id")
        or _extract_envelope_id(user_message)
    ).strip()
    if not envelope_id:
        return "Please share the envelope ID so I can check your filing status.", {}
    state_code = str(session.selections.get("state_code") or "").strip().lower()
    if not state_code:
        return (
            "I need the filing state to check envelope status. "
            "Please share the state code as well.",
            {},
        )

    await ctx.notify("checking_envelope_status")
    fields = (
        "submitter(full_name,submitter_uslp_id),submitted_on,envelope_fees,status,"
        "client_matter_number,case_number,case_tracking_id,"
        "filings(file_name,original_document,stamped_document,reviewer_comment,status_reason,status)"
    )
    response = await ctx.efile_service.envelope_status(
        user_id=session.user_id,
        state_code=state_code,
        envelope_id=envelope_id,
        fields=fields,
    )
    item = response.get("item")
    if not isinstance(item, dict):
        items = response.get("items")
        if isinstance(items, list) and items and isinstance(items[0], dict):
            item = dict(items[0])
        else:
            item = {}
    message = _envelope_status_message(item, envelope_id)
    status = str(item.get("status") or item.get("envelope_status") or "PENDING")
    session.selections["envelope_id"] = envelope_id
    session.selections["envelope_status"] = status
    if item.get("case_tracking_id"):
        session.selections["case_tracking_id"] = item.get("case_tracking_id")

    conv = await ctx.conversation_repo.get_conversation(session.conversation_id)
    session_id = str((conv or {}).get("session_id") or "").strip()
    submission = None
    if session_id:
        submission = await ctx.submission_repo.latest_for_session(session_id)
    if submission is None and session.selections.get("reference_id"):
        submission = await ctx.submission_repo.by_reference(
            str(session.selections.get("reference_id"))
        )
    if submission and submission.get("submission_id"):
        await ctx.submission_repo.update_submission_status(
            submission_id=str(submission.get("submission_id")),
            submission_status=status,
            response_message={"envelope_id": envelope_id, "status_response": response},
        )
    return message, {"envelope_status": status, "envelope_id": envelope_id}


def _append_party_to_session(session: FilingSession, validated: Dict[str, Any]) -> None:
    """
    If the LLM returned a ``party`` dict in the validated update, append it to
    ``session.selections["case_parties"]`` and remove it from ``validated`` so
    that ``merge_selections`` does not overwrite the list.
    """
    party = validated.pop("party", None)
    if party and isinstance(party, dict) and party.get("role"):
        parties: List[Dict[str, Any]] = list(
            session.selections.get("case_parties") or []
        )
        role = str(party.get("role") or "").strip().lower()
        existing_roles = {str(p.get("role") or "").strip().lower() for p in parties}
        if role not in existing_roles:
            parties.append(party)
        else:
            # Update existing party entry for this role
            parties = [
                party if str(p.get("role") or "").strip().lower() == role else p
                for p in parties
            ]
        session.selections["case_parties"] = parties


async def _hydrate_template_questions(
    ctx: FilingOrchestratorContext, session: FilingSession
) -> str:
    """Download the selected template PDF and extract form questions."""
    if session.phase != FilingPhase.SELECTING_DOCUMENT_TYPE:
        return ""
    if session.selections.get("template_questions_ready"):
        return ""
    template_id = str(session.selections.get("template_id") or "").strip()
    doc_type = str(
        session.selections.get("doc_type")
        or session.selections.get("document_type_code")
        or ""
    ).strip()
    if not template_id and not doc_type:
        return ""

    await ctx.notify("extracting_questions")
    templates = await ctx.filing_repo.list_active_document_templates()
    from app.services.document_template_match import match_document_templates

    matched = match_document_templates(templates, session.selections)
    chosen = next(
        (
            row
            for row in matched
            if template_id
            and str(row.get("template_id") or row.get("id") or "") == template_id
        ),
        None,
    )
    if chosen is None and doc_type:
        chosen = next(
            (
                row
                for row in matched
                if str(row.get("doc_type") or row.get("code") or "") == doc_type
            ),
            None,
        )
    if chosen:
        template_id = str(chosen.get("template_id") or chosen.get("id") or template_id)
        session.selections["template_id"] = template_id
        session.selections["field_mapping"] = chosen.get("field_mapping") or ""
        session.selections["cached_field_mapping"] = session.selections["field_mapping"]
        if chosen.get("s3_bucket"):
            session.selections["s3_bucket"] = chosen["s3_bucket"]
        if chosen.get("s3_key"):
            session.selections["s3_key"] = chosen["s3_key"]
        if chosen.get("name"):
            session.selections["document_type_name"] = chosen["name"]
        if chosen.get("code"):
            session.selections["template_code"] = chosen["code"]

    if not template_id:
        return (
            "I could not match that document type to a template. "
            "Please choose one of the listed document types."
        )
    if not str(session.selections.get("field_mapping") or "").strip():
        return (
            "The selected document template has no field_mapping, "
            "so I cannot ask only the related questions. "
            "Please choose a different document type."
        )

    version = await ctx.filing_repo.get_latest_template_version(str(template_id))
    s3_bucket = str(
        (version or {}).get("s3_bucket") or session.selections.get("s3_bucket") or ""
    )
    s3_key = str(
        (version or {}).get("s3_key") or session.selections.get("s3_key") or ""
    )
    if not s3_key:
        return (
            "That document type is configured, but no template file is available yet."
        )
    if ctx.s3_manager is None:
        return "Document storage is not configured, so I cannot load that template."

    session.selections["s3_bucket"] = s3_bucket
    session.selections["s3_key"] = s3_key
    if version and version.get("template_version_id"):
        session.selections["template_version_id"] = str(version["template_version_id"])
    stored_version = None
    if version is not None and version.get("version") not in (None, ""):
        stored_version = version.get("version")
    elif chosen and chosen.get("version") not in (None, ""):
        stored_version = chosen.get("version")
    if stored_version not in (None, ""):
        session.selections["template_version"] = stored_version

    from app.api.schemas.document import FileType
    from app.services.court_form_question_service import CourtFormQuestionService

    try:
        file_bytes = await ctx.s3_manager.download_bytes(s3_key, bucket=s3_bucket or None)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to download template %s from S3", s3_key)
        return f"I could not download the selected template. {exc}"

    file_name = s3_key.rsplit("/", 1)[-1] or "template.pdf"
    service = CourtFormQuestionService(s3_manager=ctx.s3_manager)
    try:
        extracted = await service.extract_questions(
            file_bytes=file_bytes,
            file_name=file_name,
            file_type=FileType.PDF,
            file_path="",
            source="s3",
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Template question extraction failed for %s", s3_key)
        return f"I loaded the template but could not extract its questions. {exc}"

    await ctx.notify("loading_questions")
    template_questions, prefilled = template_questions_to_workflow(extracted.questions)
    mapping_text = str(
        session.selections.get("cached_field_mapping")
        or session.selections.get("field_mapping")
        or ""
    )
    session.selections["field_mapping"] = mapping_text
    session.selections["cached_field_mapping"] = mapping_text
    questions = merge_document_and_mapping_questions(
        template_questions,
        mapping_text,
    )
    if not questions:
        return (
            "I opened the selected template but did not find fields to ask about. "
            "Please choose a different document type."
        )

    session.workflow_questions = questions
    session.checklist = build_checklist_from_questions(None, questions)
    remapped_prefill = {}
    for question in questions:
        name = str(question.get("field_name") or "")
        if not name:
            continue
        if name in prefilled:
            remapped_prefill[name] = prefilled[name]
            continue
        slug = name.lower().replace("-", "_")
        if slug in prefilled:
            remapped_prefill[name] = prefilled[slug]
    session.collected_answers.update(remapped_prefill)
    known_answers, skipped_fields = apply_known_case_answers(
        questions, session.selections
    )
    session.collected_answers.update(known_answers)
    for item in session.checklist.items:
        if item.field_name in session.collected_answers:
            item.status = "answered"
            item.value = session.collected_answers[item.field_name]
        elif item.field_name in skipped_fields:
            item.status = "skipped"
    session.selections["extracted_form_questions"] = [
        item.model_dump() if hasattr(item, "model_dump") else dict(item)
        for item in extracted.questions
    ]
    session.selections["extracted_form_text"] = "\n".join(
        f"[page {row.get('page') or '?'}] {row.get('field') or ''}: {row.get('question') or ''}"
        for row in session.selections["extracted_form_questions"]
    )
    session.required_documents = [
        {
            "template_id": template_id,
            "template_code": session.selections.get("template_code"),
            "template_name": session.selections.get("document_type_name"),
            "doc_type": session.selections.get("doc_type") or doc_type,
            "field_mapping": session.selections.get("field_mapping"),
            "s3_bucket": s3_bucket,
            "s3_key": s3_key,
            "is_required": True,
        }
    ]
    session.selections["template_questions_ready"] = True
    await ctx.notify("questions_ready")
    doc_name = (
        session.selections.get("document_type_name")
        or session.selections.get("doc_type")
        or "the selected document"
    )
    blank = sum(
        1
        for item in session.checklist.items
        if item.status == "pending"
        and item.field_name not in session.collected_answers
    )
    skipped = sum(1 for item in session.checklist.items if item.status == "skipped")
    known = len(session.collected_answers)
    return (
        f"You selected {doc_name}. I will fill this form with you, one question at a time, "
        "and skip anything that does not apply."
        + (f" I already have {known} answer(s) from the case details." if known else "")
        + (f" {skipped} field(s) do not apply." if skipped else "")
        + (f" {blank} still needed." if blank else "")
    )


def build_nodes(ctx: FilingOrchestratorContext) -> Dict[str, NodeFn]:
    """Factory: LangGraph node callables bound to orchestrator dependencies."""

    async def connect_node(state: FilingGraphState) -> FilingGraphState:
        await ctx.require_conversation_repo()
        user_id = state["user_id"]
        conversation_id = state.get("conversation_id")
        case_id = state.get("case_id")

        if conversation_id:
            conv = await ctx.conversation_repo.get_conversation(conversation_id)
            if not conv:
                raise ValueError(f"Conversation not found: {conversation_id}")
            cid = str(conv.get("conversation_id") or conversation_id)
            session = FilingSessionManager.get_or_create(cid, user_id)
            rows = await ctx.conversation_repo.get_history(cid, limit=500)
            restore_from_system_messages(rows, session)
            history = await load_history(ctx.conversation_repo, cid, session=session)
            await ctx.notify("session_started")
            loading = loading_process_for_phase(session.phase)
            if loading:
                await ctx.notify(loading)
            if history:
                last = history[-1].get("content", "")
                result = result_from_session(session, last, event_kind="session.started")
                response_options = await options_for_response(
                    ctx.filing_repo,
                    session,
                    codes_service=ctx.codes_service,
                    bedrock=ctx.bedrock,
                )
                result = attach_selection_options_to_result(
                    result, session.phase, response_options
                )
                return {
                    **state,
                    "conversation_id": cid,
                    "phase": session.phase.value,
                    "history": history,
                    "result": result,
                    "next_node": "done",
                }

        row = await ctx.conversation_repo.create_conversation(user_id, case_id, None)
        if not row:
            raise RuntimeError("Failed to create conversation in RDS")
        cid = str(row.get("conversation_id") or uuid.uuid4())
        session = FilingSessionManager.create(cid, user_id)
        session.phase = FilingPhase.SELECTING_STATE
        await ctx.notify("session_started")
        await ctx.notify("loading_states")

        return {
            **state,
            "conversation_id": cid,
            "phase": session.phase.value,
            "user_message": GREETING_USER_MESSAGE,
            "history": [],
            "skip_user_persist": True,
            "next_node": "navigation",
        }

    async def message_prepare_node(state: FilingGraphState) -> FilingGraphState:
        await ctx.require_conversation_repo()
        cid = state["conversation_id"]
        conv = await ctx.conversation_repo.get_conversation(cid)
        if not conv:
            raise ValueError(f"Conversation not found: {cid}")

        user_id = str(conv.get("user_id") or state.get("user_id") or "")
        session = get_session(cid, user_id)
        rows = await ctx.conversation_repo.get_history(cid, limit=500)
        if not session.chat_context or session.phase == FilingPhase.GREETING:
            restore_from_system_messages(rows, session)

        content = state.get("user_message") or ""
        await ctx.conversation_repo.insert_user_message(cid, content)
        history = await load_history(ctx.conversation_repo, cid, session=session)

        return {
            **state,
            "phase": session.phase.value,
            "history": history,
            "user_id": user_id,
        }

    async def upload_node(state: FilingGraphState) -> FilingGraphState:
        await ctx.require_conversation_repo()
        cid = state["conversation_id"]
        conv = await ctx.conversation_repo.get_conversation(cid)
        if not conv:
            raise ValueError(f"Conversation not found: {cid}")

        user_id = str(conv.get("user_id") or "")
        session = get_session(cid, user_id)
        uploads = uploads_from_state(state)
        if not uploads:
            raise ValueError("No files were uploaded")

        labels = ", ".join(str(u.get("file_name") or "document") for u in uploads)
        await ctx.conversation_repo.insert_user_message(cid, f"[upload] {labels}")
        await ctx.notify("analyzing_upload")

        analysis_results = await analyze_uploads_concurrently(
            ctx.analysis_service, uploads
        )

        if is_new_filing_prefill_phase(session):
            return {
                **state,
                "phase": session.phase.value,
                "analysis_results": analysis_results,
                "next_node": "analyze_and_prefill",
            }

        session.mode = FilingMode.FILING_EXISTING
        session.phase = FilingPhase.EXISTING_UPLOAD_ANALYSIS
        await ctx.notify("existing_upload_analysis")

        successful = [r for r in analysis_results if r.get("ok") and r.get("analysis")]
        analysis_dict = successful[0]["analysis"] if successful else {}
        session.selections["upload_analysis"] = analysis_dict
        session.metadata["last_analysis"] = analysis_dict
        session.metadata["upload_analyses"] = analysis_results

        for item in successful:
            user_details = (item.get("analysis") or {}).get("user_details") or {}
            cause = user_details.get("cause_number") or user_details.get("case_number")
            if cause:
                case = await ctx.filing_repo.get_user_details_by_case_number(str(cause))
                if case:
                    session.selections["case_metadata"] = case
                    session.selections["case_number"] = case.get("case_number")
                    session.phase = FilingPhase.EXISTING_CASE_CONFIRM
                    break

        history = await load_history(ctx.conversation_repo, cid, session=session)
        if session.selections.get("case_metadata"):
            db_options = [session.selections["case_metadata"]]
        elif analysis_dict:
            db_options = [analysis_dict]
        else:
            db_options = []

        llm_out = await ctx.nav_agent.run(
            mode=session.mode.value,
            phase=session.phase.value,
            selections=session.selections,
            db_options=[o for o in db_options if o],
            history=history,
            user_message=f"I uploaded {labels}.",
        )

        message = llm_out.get("assistant_message") or (
            analysis_dict.get("message") if analysis_dict else "Document analyzed."
        )
        event_kind = "analysis.complete"
        if session.phase == FilingPhase.EXISTING_CASE_CONFIRM:
            event_kind = "assistant.message"

        result = OrchestratorResult(
            assistant_message=message,
            conversation_id=cid,
            phase=session.phase,
            mode=session.mode,
            selections=session.selections,
            collected_answers=session.collected_answers,
            event_kind=event_kind,
            analysis=analysis_dict,
            metadata={"files": analysis_results},
        )
        await persist_system_state(ctx.conversation_repo, session)
        return {
            **state,
            "phase": session.phase.value,
            "history": history,
            "result": result,
            "next_node": "persist",
        }

    async def navigation_node(state: FilingGraphState) -> FilingGraphState:
        cid = state["conversation_id"]
        session = FilingSessionManager.get(cid)
        if not session:
            raise RuntimeError(f"No session for conversation {cid}")

        history = history_for_llm(session, state.get("history") or [])
        user_message = state.get("user_message") or ""
        skip_user_persist = bool(state.get("skip_user_persist"))
        phase_at_turn_start = session.phase

        loading = loading_process_for_phase(session.phase)
        if loading:
            await ctx.notify(loading)

        await capture_new_case_topic(
            session,
            user_message,
            bedrock=ctx.bedrock,
            history=history,
        )
        await apply_catalog_court_from_text(
            ctx.filing_repo, session, user_message, bedrock=ctx.bedrock
        )

        db_options = await load_db_options(
            ctx.filing_repo,
            session.phase,
            session.selections,
            codes_service=ctx.codes_service,
            mode=session.mode,
            bedrock=ctx.bedrock,
        )
        cache_phase_options(session, session.phase, db_options)

        # Resolve the choice locally first. Option lists can hold hundreds of
        # rows (914 Texas courts), which is too many to send to the LLM, so an
        # exact or uniquely-narrowing match short-circuits the model call.
        intent = "continue"
        validated: Dict[str, Any] = {}
        llm_out: Dict[str, Any] = {}
        raw_update: Dict[str, Any] = {}
        candidate_message = ""
        matched_locally = False
        candidates: List[Dict[str, Any]] = []
        response_options_override: Optional[List[Dict[str, Any]]] = None
        is_session_start = user_message == GREETING_USER_MESSAGE

        if is_session_start:
            matched_locally = True

        if (
            session.mode == FilingMode.FILING_EXISTING
            and user_message.strip()
            and user_message != GREETING_USER_MESSAGE
        ):
            typed = user_message.strip()
            if session.phase == FilingPhase.EXISTING_ENTER_CASE_NUMBER:
                validated = {"case_number": typed}
                matched_locally = True
            elif session.phase == FilingPhase.EXISTING_CASE_CONFIRM and is_affirmative_reply(
                typed
            ):
                matched_locally = True
                llm_out = {"lookup_action": "confirm_case"}

        can_match_locally = (
            (
                session.mode in (FilingMode.FILING_NEW, FilingMode.FILING_EXISTING)
                or session.phase == FilingPhase.SELECTING_STATE
            )
            and session.phase in SELECTION_PHASES
            and bool(db_options)
            and bool(user_message.strip())
            and user_message != GREETING_USER_MESSAGE
        )
        if can_match_locally:
            match, candidates = match_option(db_options, user_message)
            if match:
                validated = filter_selections_update(
                    session.phase.value,
                    selection_update_for_option(session.phase.value, match),
                    db_options,
                )
                matched_locally = bool(validated)
                if matched_locally:
                    logger.info(
                        "Matched option locally phase=%s value=%s",
                        session.phase.value,
                        validated,
                    )
            elif candidates:
                candidate_message = _format_candidate_message(candidates)
                response_options_override = candidates

        if not matched_locally and not candidate_message:
            await ctx.notify("processing_request")
            llm_out = await ctx.nav_agent.run(
                mode=session.mode.value,
                phase=session.phase.value,
                selections=session.selections,
                db_options=db_options,
                history=history,
                user_message=user_message,
            )

            intent = str(llm_out.get("intent") or "continue")
            if session.mode == FilingMode.UNSET or intent in (
                "filing_new",
                "filing_existing",
                "generic_legal",
            ):
                advance_mode_from_intent(session, intent)
            await capture_new_case_topic(
                session,
                user_message,
                bedrock=ctx.bedrock,
                history=history,
            )

            raw_update = llm_out.get("selections_update") or {}
            if raw_update.get("case_topic"):
                session.selections["case_topic"] = raw_update["case_topic"]
            validated = filter_selections_update(
                session.phase.value, raw_update, db_options
            )
            if raw_update and not validated and not raw_update.get("case_topic"):
                logger.warning(
                    "Dropped selections_update not in db_options: %s phase=%s",
                    raw_update,
                    session.phase.value,
                )

        # Party append: accumulate parties list from validated "party" key
        _append_party_to_session(session, validated)

        merge_selections(session, validated)
        existing_api_message = ""
        existing_api_handled = False
        if (
            session.mode == FilingMode.FILING_EXISTING
            and session.phase == FilingPhase.EXISTING_ENTER_CASE_NUMBER
            and session.selections.get("case_number")
        ):
            existing_api_handled = True
            await ctx.notify("searching_existing_case")
            state_code = str(session.selections.get("state_code") or "")
            jurisdiction_code = str(
                session.selections.get("jurisdiction_code") or ""
            )
            case_number = str(session.selections.get("case_number") or "")
            try:
                user_row = (
                    await ctx.user_repo.get_by_user_id(session.user_id)
                    if ctx.user_repo
                    else None
                )
                auth_token = resolve_auth_token(user_row)
                logger.info(
                    "Existing-case auth token source=operational.users platform_user=%s",
                    auth_token.split("/")[0],
                )

                search_response = await ctx.existing_case_service.search_case(
                    state_code=state_code,
                    jurisdiction_code=jurisdiction_code,
                    case_number=case_number,
                    auth_token=auth_token,
                )
                search_items = ctx.existing_case_service.search_items(
                    search_response
                )
                if not search_items:
                    api_message = str(search_response.get("message") or "").strip()
                    existing_api_message = api_message or (
                        "No case was found for that case number and jurisdiction. "
                        "Please verify both values and try again."
                    )
                else:
                    search_item = search_items[0]
                    tracking_id = str(
                        search_item.get("case_tracking_id") or ""
                    ).strip()
                    if not tracking_id:
                        raise RuntimeError(
                            "The case search response did not include a case tracking ID."
                        )
                    detail_link = ctx.existing_case_service.case_detail_link(
                        search_item
                    )
                    # Case detail is restricted for some account types, so the
                    # search result alone is enough to confirm the case.
                    case_detail: Dict[str, Any] = {}
                    detail_error = ""
                    try:
                        await ctx.notify("loading_case_details")
                        detail_response = (
                            await ctx.existing_case_service.get_case_details(
                                state_code=state_code,
                                case_tracking_id=tracking_id,
                                auth_token=auth_token,
                                case_detail_link=detail_link,
                                case_detail_url=str(
                                    detail_link.get("link") or ""
                                ),
                            )
                        )
                        case_detail = ctx.existing_case_service.detail_item(
                            detail_response
                        )
                    except Exception as detail_exc:  # noqa: BLE001
                        detail_error = str(detail_exc)
                        logger.warning(
                            "Case detail unavailable for %s: %s",
                            tracking_id,
                            detail_error,
                        )

                    parties = case_detail.get("case_parties") or []
                    filing_party_id = (
                        parties[0].get("id")
                        if parties and isinstance(parties[0], dict)
                        else None
                    )
                    session.selections.update(
                        {
                            "case_tracking_id": tracking_id,
                            "case_search_result": search_item,
                            "case_metadata": case_detail or search_item,
                            "case_details": case_detail,
                            "filing_party_id": filing_party_id,
                        }
                    )
                    apply_existing_search_attrs(session, search_item, case_detail)
                    session.phase = FilingPhase.EXISTING_CASE_CONFIRM
                    await ctx.notify("existing_case_confirm")
                    existing_api_message = _format_existing_case_confirmation(
                        search_item, case_detail
                    )
                    if detail_error:
                        existing_api_message += (
                            "\n\nFull case details could not be loaded "
                            f"({detail_error})"
                        )
            except Exception as exc:  # noqa: BLE001
                logger.exception("Existing-case API lookup failed")
                existing_api_message = (
                    "I could not retrieve that case from US Legal Pro. "
                    f"{exc}"
                )

        phase_before = session.phase
        lookup_action = None if existing_api_handled else llm_out.get("lookup_action")
        if lookup_action == "check_status":
            try:
                status_message, status_meta = await _handle_status_check(
                    ctx,
                    session,
                    user_message,
                    llm_out.get("lookup_params") or {},
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("Envelope status check failed")
                status_message = (
                    "I could not retrieve the envelope status right now. "
                    f"Please try again in a moment. {exc}"
                )
                status_meta = {}
            await persist_system_state(ctx.conversation_repo, session)
            result = result_from_session(
                session,
                status_message,
                event_kind="assistant.message",
                metadata=status_meta,
            )
            return {
                **state,
                "phase": session.phase.value,
                "result": result,
                "next_node": "persist",
            }
        if lookup_action == "party_search":
            await ctx.notify("existing_search_party")
        elif lookup_action == "date_search":
            await ctx.notify("existing_search_date")
        elif lookup_action == "case_number":
            await ctx.notify("searching_existing_case")
        elif lookup_action == "confirm_case":
            await ctx.notify("loading_document_types")
        await handle_lookup(
            ctx.filing_repo,
            session,
            lookup_action,
            llm_out.get("lookup_params") or {},
        )

        template_message = await _hydrate_template_questions(ctx, session)

        advance_phase_after_selections(session)
        await apply_catalog_shortcuts(ctx.filing_repo, session, bedrock=ctx.bedrock)

        assistant_message = (
            existing_api_message
            or template_message
            or candidate_message
            or str(llm_out.get("assistant_message") or "")
        )
        used_court_rules_rag = False

        # Generic legal Q&A: retrieve court-rules chunks from OpenSearch.
        should_use_court_rules = (
            not matched_locally
            and not candidate_message
            and (intent == "generic_legal" or session.mode == FilingMode.GENERIC)
            and bool(user_message.strip())
            and user_message != GREETING_USER_MESSAGE
        )
        if should_use_court_rules:
            try:
                await ctx.notify("answering_legal_question")
                state_code = session.selections.get("state_code")
                case_type = session.selections.get("case_type")
                rag_out = await ctx.court_rules_service.answer_question(
                    user_message,
                    state_code=state_code,
                    case_type=case_type,
                )
                rag_answer = str(rag_out.get("answer") or "").strip()
                if rag_answer:
                    assistant_message = sanitize_assistant_text(rag_answer)
                    used_court_rules_rag = True
                    logger.info(
                        "[generic_legal] answered via court_rules RAG sources=%s",
                        len(rag_out.get("sources") or []),
                    )
            except Exception:
                logger.exception(
                    "[generic_legal] court_rules RAG failed; keeping nav agent reply"
                )

        if session.phase != phase_at_turn_start and not used_court_rules_rag:
            if session.phase == FilingPhase.INTENT_PENDING:
                await ctx.notify("loading_courts")
                await prefetch_court_catalog(ctx.filing_repo, session)
                assistant_message = POST_STATE_HELP_MESSAGE
            elif session.phase == FilingPhase.EXISTING_ENTER_CASE_NUMBER:
                court = (
                    session.selections.get("jurisdiction_name")
                    or session.selections.get("jurisdiction_code")
                    or "the selected jurisdiction"
                )
                assistant_message = (
                    f"You selected {court}. Please enter the existing case number."
                )
            elif session.phase != FilingPhase.COLLECTING_WORKFLOW_ANSWERS:
                next_options = await load_db_options(
                    ctx.filing_repo,
                    session.phase,
                    session.selections,
                    codes_service=ctx.codes_service,
                    mode=session.mode,
                    bedrock=ctx.bedrock,
                )
                built = build_phase_selection_message(
                    session.phase.value, session.selections, next_options
                )
                if built:
                    assistant_message = sanitize_assistant_text(built)
                elif not next_options:
                    logger.warning(
                        "No db_options after phase advance %s -> %s selections=%s",
                        phase_before.value,
                        session.phase.value,
                        session.selections,
                    )

        # A locally matched selection produces no LLM text, so re-show the
        # current phase options if nothing else filled the reply.
        if not assistant_message.strip():
            built = build_phase_selection_message(
                session.phase.value, session.selections, db_options
            )
            if is_session_start and session.phase == FilingPhase.SELECTING_STATE:
                assistant_message = sanitize_assistant_text(
                    WELCOME_SELECT_STATE_MESSAGE
                    + (" " + built if built else "")
                )
            else:
                assistant_message = sanitize_assistant_text(
                    built or "Please choose one of the options listed above."
                )

        next_node = "persist"
        if (
            phase_before == FilingPhase.SELECTING_CASE_PARTIES
            and session.phase == FilingPhase.COLLECTING_WORKFLOW_ANSWERS
            and session.selections.get("party_type_code")
        ):
            next_node = "init_workflow"
        elif (
            phase_before == FilingPhase.SELECTING_DOCUMENT_TYPE
            and session.phase == FilingPhase.OFFERING_DOCUMENTS
        ):
            next_node = "offer_documents"
        elif (
            phase_before == FilingPhase.SELECTING_DOCUMENT_TYPE
            and session.phase == FilingPhase.COLLECTING_WORKFLOW_ANSWERS
        ):
            assistant_message = format_next_form_question_message(
                assistant_message,
                next_pending_question(session),
            )
        elif (
            # Legacy DB path
            phase_before == FilingPhase.SELECTING_CASE_TYPE
            and session.phase == FilingPhase.COLLECTING_WORKFLOW_ANSWERS
            and not session.selections.get("case_type_code")
        ):
            next_node = "init_workflow"
        elif session.phase == FilingPhase.COMPLETE and session.mode == FilingMode.FILING_EXISTING:
            next_node = "case_located"

        if next_node == "init_workflow":
            return {
                **state,
                "phase": session.phase.value,
                "phase_before": phase_before.value,
                "assistant_message": assistant_message,
                "skip_user_persist": skip_user_persist,
                "next_node": "init_workflow",
            }

        if next_node == "offer_documents":
            await persist_system_state(ctx.conversation_repo, session)
            return {
                **state,
                "phase": session.phase.value,
                "phase_before": phase_before.value,
                "assistant_message": assistant_message,
                "skip_user_persist": skip_user_persist,
                "user_message": "[document_offer]",
                "next_node": "offer_documents",
            }

        if next_node == "case_located":
            await ctx.conversation_repo.complete_conversation(session.conversation_id)
            result = OrchestratorResult(
                assistant_message=assistant_message,
                conversation_id=session.conversation_id,
                phase=session.phase,
                mode=session.mode,
                selections=session.selections,
                collected_answers=session.collected_answers,
                event_kind="case.located",
                metadata={"case_metadata": session.selections.get("case_metadata")},
            )
            return {
                **state,
                "phase": session.phase.value,
                "result": result,
                "next_node": "persist",
            }

        await persist_system_state(ctx.conversation_repo, session)
        response_options = await options_for_response(
            ctx.filing_repo,
            session,
            codes_service=ctx.codes_service,
            bedrock=ctx.bedrock,
            override=response_options_override,
        )
        result = result_from_session(
            session, sanitize_assistant_text(assistant_message)
        )
        result = attach_selection_options_to_result(
            result, session.phase, response_options
        )
        return {
            **state,
            "phase": session.phase.value,
            "result": result,
            "next_node": "persist",
        }

    async def init_workflow_node(state: FilingGraphState) -> FilingGraphState:
        cid = state["conversation_id"]
        session = FilingSessionManager.get(cid)
        if not session:
            raise RuntimeError(f"No session for conversation {cid}")

        await ctx.notify("matching_workflow")
        candidates = await ctx.filing_repo.get_active_workflows()
        match = await ctx.workflow_match_agent.match(
            session.selections, candidates
        )
        session.metadata["workflow_match"] = match
        session.selections["workflow_match"] = match

        if not match.get("matched"):
            session.phase = FilingPhase.COMPLETE
            await persist_system_state(ctx.conversation_repo, session)
            reason = str(match.get("reason") or "").strip()
            message = (
                "The selected filing details do not match a supported case "
                "workflow in our system, so I cannot start the document "
                "questions for this filing."
            )
            if reason:
                message = f"{message} {reason}"
            result = OrchestratorResult(
                assistant_message=sanitize_assistant_text(message),
                conversation_id=session.conversation_id,
                phase=session.phase,
                mode=session.mode,
                selections=session.selections,
                collected_answers=session.collected_answers,
                metadata={"workflow_match": match},
            )
            return {
                **state,
                "phase": session.phase.value,
                "result": result,
                "next_node": "persist",
            }

        matched = dict(match.get("matched_workflow") or {})
        selections = session.selections
        selections["api_case_type_code"] = selections.get("case_type_code")
        selections["api_case_type_name"] = selections.get("case_type_name")
        selections["workflow_id"] = str(match["workflow_id"])
        selections["case_type"] = matched.get("case_type") or ""
        selections["sub_case_type"] = matched.get("sub_case_type") or ""
        selections["db_jurisdiction_code"] = matched.get("jurisdiction_code")
        selections["db_jurisdiction_name"] = matched.get("jurisdiction_name")
        selections["matched_workflow"] = matched

        await ctx.notify("loading_questions")
        await init_workflow_phase(ctx.filing_repo, session, bedrock=ctx.bedrock)
        if not session.workflow_questions:
            match = {
                **match,
                "matched": False,
                "reason": (
                    f"Workflow {matched.get('workflow_name') or match['workflow_id']} "
                    "matched strictly, but it has no active workflow questions."
                ),
            }
            session.metadata["workflow_match"] = match
            session.selections["workflow_match"] = match
            session.phase = FilingPhase.COMPLETE
            await persist_system_state(ctx.conversation_repo, session)
            result = OrchestratorResult(
                assistant_message=sanitize_assistant_text(
                    "The selected filing matches "
                    f"{matched.get('workflow_name') or 'a configured workflow'}, "
                    "but no active questions are configured for that workflow."
                ),
                conversation_id=session.conversation_id,
                phase=session.phase,
                mode=session.mode,
                selections=session.selections,
                collected_answers=session.collected_answers,
                metadata={"workflow_match": match},
            )
            return {
                **state,
                "phase": session.phase.value,
                "result": result,
                "next_node": "persist",
            }

        history = state.get("history") or []
        user_message = state.get("user_message") or ""
        if not state.get("skip_user_persist"):
            history = history + [{"role": "user", "content": user_message}]

        return {
            **state,
            "phase": session.phase.value,
            "history": history,
            "user_message": DOCUMENT_OFFER_USER_MESSAGE,
            "skip_user_persist": True,
            "next_node": "offer_documents",
        }

    async def workflow_node(state: FilingGraphState) -> FilingGraphState:
        cid = state["conversation_id"]
        session = FilingSessionManager.get(cid)
        if not session:
            raise RuntimeError(f"No session for conversation {cid}")

        await ctx.notify("collecting_workflow_answers")
        history = history_for_llm(session, state.get("history") or [])
        user_message = state.get("user_message") or ""

        next_q = next_pending_question(session)
        from_template = bool(session.selections.get("template_questions_ready"))
        if from_template:
            pending_batch = compact_form_questions(
                list_all_pending_questions(
                    checklist_items=session.checklist.items,
                    workflow_questions=session.workflow_questions,
                    collected_answers=session.collected_answers,
                )
            )
        else:
            pending_batch = batch_pending_questions(
                checklist_items=session.checklist.items,
                workflow_questions=session.workflow_questions,
                collected_answers=session.collected_answers,
            )
        llm_out = await ctx.workflow_agent.run(
            selections=session.selections,
            workflow_questions=session.workflow_questions,
            checklist=session.checklist.to_payload().model_dump(),
            collected_answers=session.collected_answers,
            pending_questions_batch=pending_batch,
            next_pending_question=next_q,
            history=history,
            user_message=user_message,
        )

        known_fields: set[str] = set()
        for q in session.workflow_questions:
            name = str(q.get("field_name") or "").strip()
            if name:
                known_fields.add(name)
            for field_name in q.get("cluster_fields") or []:
                label = str(field_name).strip()
                if label:
                    known_fields.add(label)
        alias_fields = set(
            (session.metadata.get("workflow_field_aliases") or {}).keys()
        )
        known_fields.update(alias_fields)
        recorded = {
            key: val
            for key, val in (llm_out.get("answers_update") or {}).items()
            if key in known_fields
        }
        for key, val in recorded.items():
            session.collected_answers[key] = val
        from app.services.workflow_question_consolidation_service import (
            propagate_cluster_answers,
        )

        propagate_cluster_answers(session)
        merge_checklist_updates(session, llm_out.get("checklist_updates") or [])
        skipped_fields = [
            name
            for name in (llm_out.get("skipped_fields") or [])
            if name in known_fields
        ]
        if skipped_fields:
            merge_checklist_updates(
                session,
                [
                    {"field_name": name, "status": "skipped"}
                    for name in skipped_fields
                    if name not in session.collected_answers
                ],
            )

        for key, val in session.collected_answers.items():
            for item in session.checklist.items:
                if item.field_name == key and item.status != "skipped":
                    item.status = "answered"
                    item.value = val

        leftover = next_pending_question(session)
        if from_template:
            complete = leftover is None
        else:
            complete = bool(llm_out.get("workflow_complete")) or workflow_is_complete(
                session
            )
        if complete:
            session.phase = FilingPhase.GENERATING_DOCUMENTS
            await persist_system_state(ctx.conversation_repo, session)
            return {
                **state,
                "phase": session.phase.value,
                "next_node": "generate_documents",
            }

        assistant_message = str(llm_out.get("assistant_message") or "").strip()
        if not assistant_message and leftover:
            assistant_message = str(
                leftover.get("field_label")
                or leftover.get("field_name")
                or "Please provide the next detail for the form."
            )

        await persist_system_state(ctx.conversation_repo, session)
        result = OrchestratorResult(
            assistant_message=assistant_message,
            conversation_id=session.conversation_id,
            phase=session.phase,
            mode=session.mode,
            selections=session.selections,
            collected_answers=session.collected_answers,
            checklist=session.checklist.to_payload(),
        )

        return {
            **state,
            "phase": session.phase.value,
            "result": result,
            "next_node": "persist",
        }

    async def persist_node(state: FilingGraphState) -> FilingGraphState:
        result = state.get("result")
        if not result:
            return state

        cid = result.conversation_id
        session = FilingSessionManager.get(cid)
        if state.get("trigger") == "connect" and result.event_kind == "session.started":
            if session:
                result.metadata = dict(result.metadata or {})
                result.metadata["chat_context"] = list(session.chat_context)
            return {**state, "result": result}

        if session:
            append_chat_turn(
                session,
                state.get("user_message") or "",
                result.assistant_message,
            )
            result.metadata = dict(result.metadata or {})
            result.metadata["chat_context"] = list(session.chat_context)

        await ctx.conversation_repo.insert_ai_message(cid, result.assistant_message)
        if session:
            await persist_system_state(ctx.conversation_repo, session)
        if state.get("trigger") == "connect":
            result.event_kind = "session.started"
        return {**state, "result": result}

    return {
        "connect": connect_node,
        "message_prepare": message_prepare_node,
        "upload": upload_node,
        "navigation": navigation_node,
        "init_workflow": init_workflow_node,
        "workflow": workflow_node,
        "persist": persist_node,
        **build_document_nodes(ctx),
        **build_payment_nodes(ctx),
    }
