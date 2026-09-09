"""Pure helpers for filing phase transitions, checklist, and persistence snapshots."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Dict, List, Literal, Optional

from app.agents.conversation.orchestration.session_manager import FilingSessionManager
from app.agents.conversation.orchestration.state import (
    ChecklistItem,
    FilingGraphState,
    FilingSession,
    OrchestratorResult,
    WorkflowChecklist,
)
from app.agents.utils.db_options_format import build_selection_options_payload
from app.api.schemas.filing_events import FilingMode, FilingPhase
from app.services.conversation_repository import ConversationRepository
from app.services.court_catalog import (
    category_options,
    case_type_options,
    filter_court,
    filter_court_category,
    get_court_catalog,
    jurisdiction_options,
    match_jurisdiction_from_text,
    search_catalog_rows,
)
from app.adapters.llm.bedrock import Bedrock
from app.services.document_template_match import match_document_templates
from app.services.legal_filing_repository import LegalFilingRepository
from app.services.uslegalpro_codes_service import USLegalProCodesService

logger = logging.getLogger(__name__)

HISTORY_LIMIT = 50
ANALYSIS_CONCURRENCY = 4

_YES_RE = re.compile(
    r"\b(yes|yeah|yep|yup|sure|ok|okay|i have|i do|upload)\b", re.I
)
_DONE_RE = re.compile(
    r"\b(that'?s all|thats all|no more|done|continue|skip|finished|i'?m done)\b",
    re.I,
)
_NO_RE = re.compile(
    r"\b(no|nope|nah|do not have|don't have|dont have|none|nothing)\b", re.I
)


async def load_history(
    conversation_repo: ConversationRepository,
    conversation_id: str,
    session: Optional[FilingSession] = None,
) -> List[Dict[str, str]]:
    from app.agents.conversation.orchestration.chat_context import (
        hydrate_chat_context,
        history_for_llm,
    )

    rows = await conversation_repo.get_history(conversation_id, limit=500)
    messages = ConversationRepository.history_to_llm_messages(rows, limit=HISTORY_LIMIT)
    if session is not None:
        hydrate_chat_context(session, messages)
        return history_for_llm(session, messages)
    return messages


async def persist_system_state(
    conversation_repo: ConversationRepository, session: FilingSession
) -> None:
    snapshot = {
        "phase": session.phase.value,
        "mode": session.mode.value,
        "selections": session.selections,
        "collected_answers": session.collected_answers,
        "checklist": [
            {
                "field_name": i.field_name,
                "status": i.status,
                "value": i.value,
            }
            for i in session.checklist.items
        ],
        "workflow_id": session.checklist.workflow_id,
        "workflow_questions": session.workflow_questions,
        "required_documents": session.required_documents,
        "uploaded_documents": [
            {
                "file_name": d.get("file_name"),
                "classification": d.get("classification"),
                "ok": d.get("ok", True),
                "error": d.get("error"),
            }
            for d in session.uploaded_documents
        ],
        "generated_documents": [
            {
                "template_code": d.get("template_code"),
                "template_name": d.get("template_name"),
                "file_name": d.get("file_name"),
                "skipped_because_uploaded": d.get("skipped_because_uploaded", False),
                "error": d.get("error"),
            }
            for d in session.generated_documents
        ],
        "chat_context": list(session.chat_context or [])[-40:],
    }
    await conversation_repo.insert_system_message(
        session.conversation_id,
        json.dumps({"filing_state": snapshot}, default=str),
    )


def restore_from_system_messages(
    rows: List[Dict[str, Any]], session: FilingSession
) -> None:
    for row in reversed(rows):
        if str(row.get("sender")).upper() != "SYSTEM":
            continue
        try:
            payload = json.loads(str(row.get("message") or "{}"))
        except json.JSONDecodeError:
            continue
        state = payload.get("filing_state")
        if not isinstance(state, dict):
            continue
        try:
            session.phase = FilingPhase(state.get("phase", session.phase.value))
            session.mode = FilingMode(state.get("mode", session.mode.value))
        except ValueError:
            pass
        session.selections = dict(state.get("selections") or session.selections)
        session.collected_answers = dict(
            state.get("collected_answers") or session.collected_answers
        )
        wf_id = state.get("workflow_id")
        if wf_id:
            session.checklist.workflow_id = str(wf_id)
        if state.get("workflow_questions"):
            session.workflow_questions = list(state["workflow_questions"])
        if state.get("required_documents"):
            session.required_documents = list(state["required_documents"])
        if state.get("uploaded_documents"):
            session.uploaded_documents = list(state["uploaded_documents"])
        if state.get("generated_documents"):
            session.generated_documents = list(state["generated_documents"])
        if state.get("chat_context"):
            session.chat_context = [
                {
                    "request": str(item.get("request") or ""),
                    "response": str(item.get("response") or ""),
                }
                for item in state["chat_context"]
                if isinstance(item, dict)
            ]
        saved_items = state.get("checklist") or []
        if saved_items and session.checklist.items:
            by_name = {i.field_name: i for i in session.checklist.items}
            for saved in saved_items:
                item = by_name.get(saved.get("field_name"))
                if not item:
                    continue
                if saved.get("status") in ("pending", "answered", "skipped"):
                    item.status = saved["status"]
                if "value" in saved:
                    item.value = saved["value"]
        elif saved_items and not session.checklist.items:
            session.checklist = WorkflowChecklist(
                workflow_id=str(wf_id) if wf_id else None,
                items=[
                    ChecklistItem(
                        field_name=s["field_name"],
                        label=s.get("label") or s["field_name"],
                        status=s.get("status") or "pending",
                        value=s.get("value"),
                    )
                    for s in saved_items
                    if s.get("field_name")
                ],
            )
        break


async def _catalog_rows_for_session(
    filing_repo: LegalFilingRepository,
    selections: Dict[str, Any],
) -> List[Dict[str, str]]:
    return await get_court_catalog().rows_for_state(
        selections.get("state_code"),
        filing_repo,
    )


def _catalog_rows_for_selected_court(
    rows: List[Dict[str, str]],
    selections: Dict[str, Any],
) -> List[Dict[str, str]]:
    """Filter cached catalog rows to the chosen court/category — no LLM."""
    j_code = str(selections.get("jurisdiction_code") or "").strip()
    if not j_code:
        return rows
    court_rows = filter_court(rows, j_code)
    c_code = str(selections.get("case_category_code") or "").strip()
    if c_code:
        court_rows = filter_court_category(court_rows, j_code, c_code)
    topic = str(selections.get("case_topic") or "").strip()
    if topic:
        court_rows = search_catalog_rows(court_rows, topic)
    return court_rows


async def _scoped_catalog_rows(
    rows: List[Dict[str, str]],
    selections: Dict[str, Any],
    bedrock: Optional[Bedrock] = None,
    *,
    phase: Optional[FilingPhase] = None,
) -> List[Dict[str, str]]:
    """LLM court matching only for initial jurisdiction selection."""
    if selections.get("jurisdiction_code"):
        return _catalog_rows_for_selected_court(rows, selections)

    if phase is not None and phase != FilingPhase.SELECTING_JURISDICTION:
        return rows

    topic = str(selections.get("case_topic") or "").strip()
    if not topic:
        return rows

    from app.services.court_catalog_filter_service import CourtCatalogFilterService

    service = CourtCatalogFilterService(bedrock)
    return await service.filter_catalog_rows(rows, selections)


async def capture_new_case_topic(
    session: FilingSession,
    user_message: str,
    *,
    bedrock: Optional[Bedrock] = None,
    history: Optional[List[Dict[str, str]]] = None,
) -> None:
    message = str(user_message or "").strip()
    if not message:
        return
    should_capture = session.phase in (
        FilingPhase.INTENT_PENDING,
        FilingPhase.GREETING,
    ) or (
        session.mode == FilingMode.FILING_NEW
        and session.phase == FilingPhase.SELECTING_JURISDICTION
        and not session.selections.get("jurisdiction_code")
    )
    if not should_capture:
        return

    from app.services.court_catalog_filter_service import CourtCatalogFilterService

    service = CourtCatalogFilterService(bedrock)
    intent = await service.extract_case_intent(
        message, session.selections, history or []
    )
    if not intent.case_topic:
        return
    prior_topic = str(session.selections.get("case_topic") or "")
    if intent.case_topic != prior_topic:
        session.selections.pop("matched_court_codes", None)
        session.selections.pop("matched_case_type_codes", None)
    session.selections["case_topic"] = intent.case_topic
    session.selections["case_intent"] = intent.model_dump()


async def apply_catalog_court_from_text(
    filing_repo: LegalFilingRepository,
    session: FilingSession,
    user_message: str,
    *,
    bedrock: Optional[Bedrock] = None,
) -> None:
    """If the user named a unique court in natural language, store its code."""
    if session.mode != FilingMode.FILING_NEW:
        return
    if session.phase not in (
        FilingPhase.SELECTING_JURISDICTION,
        FilingPhase.SELECTING_CASE_CATEGORY,
        FilingPhase.SELECTING_CASE_TYPE,
        FilingPhase.SELECTING_CASE_PARTIES,
    ):
        return
    if session.selections.get("jurisdiction_code"):
        return
    rows = await _catalog_rows_for_session(filing_repo, session.selections)
    if not rows:
        return
    scoped = await _scoped_catalog_rows(
        rows, session.selections, bedrock, phase=session.phase
    )
    court = match_jurisdiction_from_text(
        scoped or [],
        user_message,
    )
    if court:
        session.selections["jurisdiction_code"] = court["code"]
        session.selections["jurisdiction_name"] = court["name"]


async def apply_catalog_shortcuts(
    filing_repo: LegalFilingRepository,
    session: FilingSession,
    *,
    bedrock: Optional[Bedrock] = None,
) -> None:
    """Skip unique category/type after a court is chosen from the catalog."""
    if session.mode != FilingMode.FILING_NEW:
        return
    rows = await _catalog_rows_for_session(filing_repo, session.selections)
    if not rows:
        return
    scoped = (
        _catalog_rows_for_selected_court(rows, session.selections)
        if session.selections.get("jurisdiction_code")
        else await _scoped_catalog_rows(
            rows, session.selections, bedrock, phase=session.phase
        )
    )
    for _ in range(4):
        j_code = str(session.selections.get("jurisdiction_code") or "")
        if session.phase == FilingPhase.SELECTING_JURISDICTION:
            courts = jurisdiction_options(scoped)
            if len(courts) != 1:
                return
            session.selections["jurisdiction_code"] = courts[0]["code"]
            session.selections["jurisdiction_name"] = courts[0]["name"]
            advance_phase_after_selections(session)
            continue
        if not j_code:
            return
        if session.phase == FilingPhase.SELECTING_CASE_CATEGORY:
            cats = category_options(filter_court(scoped, j_code))
            if len(cats) != 1:
                return
            session.selections["case_category_code"] = cats[0]["code"]
            session.selections["case_category_name"] = cats[0]["name"]
            advance_phase_after_selections(session)
            continue
        if session.phase == FilingPhase.SELECTING_CASE_TYPE:
            types = case_type_options(
                filter_court_category(
                    scoped,
                    j_code,
                    str(session.selections.get("case_category_code") or ""),
                )
            )
            if len(types) != 1:
                return
            chosen = types[0]
            session.selections["case_type_code"] = chosen["code"]
            session.selections["case_type_name"] = chosen["name"]
            if chosen.get("party_type_codes_url"):
                session.selections["party_type_codes_url"] = chosen[
                    "party_type_codes_url"
                ]
            if chosen.get("case_subtype_codes_url"):
                session.selections["case_subtype_codes_url"] = chosen[
                    "case_subtype_codes_url"
                ]
            if chosen.get("filing_codes_url"):
                session.selections["filing_codes_url"] = chosen["filing_codes_url"]
            if chosen.get("filer_type_codes_url"):
                session.selections["filer_type_codes_url"] = chosen[
                    "filer_type_codes_url"
                ]
            if chosen.get("filing_type_url"):
                session.selections["filing_type_url"] = chosen["filing_type_url"]
            advance_phase_after_selections(session)
            continue
        return


async def prefetch_court_catalog(
    filing_repo: LegalFilingRepository,
    session: FilingSession,
) -> None:
    """Load and cache flattened court rows after the shared state step."""
    state_code = str(session.selections.get("state_code") or "")
    if not state_code:
        return
    await get_court_catalog().rows_for_state(state_code, filing_repo)


async def load_db_options(
    filing_repo: LegalFilingRepository,
    phase: FilingPhase,
    selections: Dict[str, Any],
    codes_service: Optional[USLegalProCodesService] = None,
    mode: Optional[FilingMode] = None,
    bedrock: Optional[Bedrock] = None,
) -> List[Dict[str, Any]]:
    if phase in (FilingPhase.GREETING, FilingPhase.INTENT_PENDING):
        if selections.get("state_code"):
            return []
        return await filing_repo.get_states()
    if phase in (
        FilingPhase.SELECTING_STATE,
        FilingPhase.EXISTING_SELECTING_STATE,
    ):
        return await filing_repo.get_states()
    if phase == FilingPhase.SELECTING_COUNTY:
        return await filing_repo.get_counties(selections.get("state_code"))
    if phase == FilingPhase.SELECTING_JURISDICTION:
        if mode == FilingMode.FILING_NEW:
            rows = await _catalog_rows_for_session(filing_repo, selections)
            if rows:
                scoped = await _scoped_catalog_rows(
                    rows, selections, bedrock, phase=phase
                )
                return jurisdiction_options(scoped)
        if codes_service and selections.get("state_code"):
            return await codes_service.get_jurisdictions(selections["state_code"])
        return await filing_repo.get_jurisdictions_for_county(
            selections.get("state_code"),
            county_name=selections.get("county_name"),
            county_id=selections.get("county_id"),
        )
    if phase == FilingPhase.EXISTING_SELECTING_JURISDICTION:
        if codes_service and selections.get("state_code"):
            return await codes_service.get_jurisdictions(selections["state_code"])
        return []
    if phase == FilingPhase.SELECTING_CASE_CATEGORY:
        if mode == FilingMode.FILING_NEW:
            rows = await _catalog_rows_for_session(filing_repo, selections)
            if rows:
                return category_options(_catalog_rows_for_selected_court(rows, selections))
        url = selections.get("case_category_codes_url")
        if codes_service and url:
            return await codes_service.get_case_categories_from_jurisdiction_link(url)
        return []
    if phase == FilingPhase.SELECTING_CASE_TYPE:
        if mode == FilingMode.FILING_NEW:
            rows = await _catalog_rows_for_session(filing_repo, selections)
            if rows:
                return case_type_options(_catalog_rows_for_selected_court(rows, selections))
        url = selections.get("case_type_codes_url")
        if codes_service and url:
            return await codes_service.get_case_types_from_category_link(url)
        code = selections.get("jurisdiction_code")
        if code:
            return await filing_repo.get_workflows_for_jurisdiction(code)
        return []
    if phase == FilingPhase.SELECTING_CASE_PARTIES:
        if codes_service:
            return await codes_service.get_party_types_for_case_type(
                selections.get("state_code"),
                selections.get("jurisdiction_code"),
                selections.get("case_category_code"),
                selections.get("case_type_code"),
            )
        return []
    if phase == FilingPhase.SELECTING_FILER_TYPE:
        url = selections.get("filer_type_codes_url")
        if codes_service and url:
            return await codes_service.fetch_by_url(str(url))
        return []
    if phase == FilingPhase.SELECTING_FILING_CODE:
        url = selections.get("filing_codes_url")
        if codes_service and url:
            return await codes_service.fetch_by_url(str(url))
        return []
    if phase == FilingPhase.SELECTING_DOCUMENT_TYPE:
        templates = await filing_repo.list_active_document_templates()
        return match_document_templates(templates, selections)
    if phase == FilingPhase.SELECTING_FILING_TYPE:
        url = selections.get("filing_type_url")
        if codes_service and url:
            # filing_type endpoint returns ``{"item": {"EFile": "EFile"}}``,
            # which the standard normalize path cannot read.
            return await codes_service.fetch_filing_type_by_url(str(url))
        return []
    if phase == FilingPhase.EXISTING_CASE_CONFIRM:
        case = selections.get("case_metadata")
        return [case] if case else []
    if phase == FilingPhase.VERIFYING_COURT_PAYMENT:
        return list(selections.get("court_payment_accounts") or [])
    if phase == FilingPhase.CONFIRMING_EFILE:
        return []
    if phase in (FilingPhase.EXISTING_SEARCH_PARTY, FilingPhase.EXISTING_SEARCH_DATE):
        return list(selections.get("search_results") or [])
    return []


_PHASE_CACHE_KEYS = {
    FilingPhase.SELECTING_JURISDICTION: "cached_jurisdictions",
    FilingPhase.EXISTING_SELECTING_JURISDICTION: "cached_jurisdictions",
    FilingPhase.SELECTING_CASE_CATEGORY: "cached_case_categories",
    FilingPhase.SELECTING_CASE_TYPE: "cached_case_types",
    FilingPhase.SELECTING_CASE_PARTIES: "cached_party_types",
    FilingPhase.SELECTING_FILER_TYPE: "cached_filer_types",
    FilingPhase.SELECTING_FILING_CODE: "cached_filing_codes",
    FilingPhase.SELECTING_DOCUMENT_TYPE: "cached_document_types",
    FilingPhase.SELECTING_FILING_TYPE: "cached_filing_types",
}


def compact_cached_options(options: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for row in options or []:
        if not isinstance(row, dict):
            continue
        code = str(row.get("code") or row.get("doc_type") or "").strip()
        name = str(
            row.get("name")
            or row.get("label")
            or row.get("template_name")
            or ""
        ).strip()
        item: Dict[str, Any] = {"code": code, "name": name}
        if row.get("doc_type"):
            item["doc_type"] = str(row.get("doc_type")).strip()
        if row.get("filing_codes_url"):
            item["filing_codes_url"] = str(row.get("filing_codes_url")).strip()
        if row.get("document_type_codes_url"):
            item["document_type_codes_url"] = str(
                row.get("document_type_codes_url")
            ).strip()
        if row.get("filer_type_codes_url"):
            item["filer_type_codes_url"] = str(
                row.get("filer_type_codes_url")
            ).strip()
        if row.get("filing_type_url"):
            item["filing_type_url"] = str(row.get("filing_type_url")).strip()
        if code or name:
            rows.append(item)
    return rows


def cache_phase_options(
    session: FilingSession, phase: FilingPhase, options: List[Dict[str, Any]]
) -> None:
    """Keep catalog lists the user already saw so e-file can reuse them."""
    key = _PHASE_CACHE_KEYS.get(phase)
    if not key:
        return
    rows = compact_cached_options(options)
    if rows:
        session.selections[key] = rows


def cached_catalog_rows(selections: Dict[str, Any], key: str) -> List[Dict[str, Any]]:
    rows = selections.get(key)
    if not isinstance(rows, list):
        return []
    return [dict(row) for row in rows if isinstance(row, dict)]


def attach_selection_options_to_result(
    result: OrchestratorResult,
    phase: FilingPhase,
    options: List[Dict[str, Any]],
) -> OrchestratorResult:
    """Add structured dropdown options to the WebSocket payload metadata."""
    payload = build_selection_options_payload(phase.value, options)
    if payload:
        meta = dict(result.metadata or {})
        meta["selection_options"] = payload
        result.metadata = meta
    return result


async def options_for_response(
    filing_repo: LegalFilingRepository,
    session: FilingSession,
    *,
    codes_service: Optional[USLegalProCodesService] = None,
    bedrock: Optional[Bedrock] = None,
    override: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    if override is not None:
        return override
    # Auto-skip 1-option phases (filer_type, filing_type). Loop a few times
    # in case a chain of single-option phases collapses (e.g. filer_type has
    # a single Attorney entry AND filing_type is just EFile).
    for _ in range(4):
        phase = session.phase
        options = await load_db_options(
            filing_repo,
            phase,
            session.selections,
            codes_service=codes_service,
            mode=session.mode,
            bedrock=bedrock,
        )
        if phase in (
            FilingPhase.SELECTING_FILER_TYPE,
            FilingPhase.SELECTING_FILING_TYPE,
        ) and auto_pick_single_option(session, phase, options):
            continue
        return options
    return []


def merge_selections(session: FilingSession, update: Dict[str, Any]) -> None:
    for key, value in (update or {}).items():
        if value is not None and value != "":
            session.selections[key] = value


def apply_existing_search_attrs(
    session: FilingSession,
    search_item: Dict[str, Any],
    case_detail: Optional[Dict[str, Any]] = None,
) -> None:
    """Copy case search/detail fields into selections for template matching."""
    source = dict(search_item or {})
    if isinstance(case_detail, dict):
        source.update({k: v for k, v in case_detail.items() if v not in (None, "")})
    sel = session.selections
    if source.get("case_category_display"):
        sel["case_category_name"] = source["case_category_display"]
    if source.get("case_category"):
        sel.setdefault("case_category_code", source["case_category"])
    if source.get("case_type_display"):
        sel["case_type_name"] = source["case_type_display"]
        sel["case_type"] = source["case_type_display"]
    if source.get("case_type"):
        sel.setdefault("case_type_code", source["case_type"])
    if source.get("jurisdiction_display"):
        sel.setdefault("jurisdiction_name", source["jurisdiction_display"])


_AFFIRM_REPLIES = frozenset(
    {"yes", "y", "ok", "okay", "confirm", "confirmed", "correct", "that's right", "thats right"}
)


def is_affirmative_reply(text: str) -> bool:
    return str(text or "").strip().lower() in _AFFIRM_REPLIES


def advance_mode_from_intent(session: FilingSession, intent: str) -> None:
    """Apply new/existing/generic intent after the shared state step."""
    if session.phase == FilingPhase.SELECTING_STATE:
        return
    if intent == "generic_legal":
        session.mode = FilingMode.GENERIC
    elif intent == "filing_new":
        session.mode = FilingMode.FILING_NEW
        if session.phase in (FilingPhase.GREETING, FilingPhase.INTENT_PENDING):
            if session.selections.get("state_code"):
                session.phase = FilingPhase.SELECTING_JURISDICTION
            else:
                session.phase = FilingPhase.SELECTING_STATE
    elif intent == "filing_existing":
        session.mode = FilingMode.FILING_EXISTING
        if session.selections.get("state_code"):
            session.phase = FilingPhase.EXISTING_SELECTING_JURISDICTION
        else:
            session.phase = FilingPhase.EXISTING_SELECTING_STATE


def _phase_after_document_type_ready(sel: Dict[str, Any]) -> "FilingPhase":
    """After document_type is ready, ask for filer_type / filing_type if the
    case-type item exposed those API links. If neither URL is present (or the
    selection was already made) proceed to OFFERING_DOCUMENTS."""
    if sel.get("filer_type_codes_url") and not sel.get("filer_type"):
        return FilingPhase.SELECTING_FILER_TYPE
    if sel.get("filing_type_url") and not sel.get("filing_type"):
        return FilingPhase.SELECTING_FILING_TYPE
    return FilingPhase.OFFERING_DOCUMENTS


def _phase_after_filer_type(sel: Dict[str, Any]) -> "FilingPhase":
    if sel.get("filing_type_url") and not sel.get("filing_type"):
        return FilingPhase.SELECTING_FILING_TYPE
    return FilingPhase.OFFERING_DOCUMENTS


def advance_phase_after_selections(session: FilingSession) -> None:
    sel = session.selections
    if session.phase == FilingPhase.SELECTING_STATE and sel.get("state_code"):
        session.phase = FilingPhase.INTENT_PENDING
        return
    if session.mode == FilingMode.FILING_NEW:
        if session.phase == FilingPhase.SELECTING_JURISDICTION and sel.get(
            "jurisdiction_code"
        ):
            session.phase = FilingPhase.SELECTING_CASE_CATEGORY
        elif session.phase == FilingPhase.SELECTING_CASE_CATEGORY and sel.get(
            "case_category_code"
        ):
            session.phase = FilingPhase.SELECTING_CASE_TYPE
        elif session.phase == FilingPhase.SELECTING_CASE_TYPE and sel.get(
            "case_type_code"
        ):
            session.phase = FilingPhase.SELECTING_CASE_PARTIES
        elif session.phase == FilingPhase.SELECTING_CASE_PARTIES and sel.get(
            "party_type_code"
        ):
            sel["case_type"] = sel.get("case_type_name") or sel.get("case_type_code") or ""
            sel.setdefault("sub_case_type", "")
            # Enter SELECTING_FILING_CODE when the case type exposed a
            # filing_codes link (the normal Tyler flow). Fall back to the
            # document-type phase when no live filing-code list is available.
            if sel.get("filing_codes_url"):
                session.phase = FilingPhase.SELECTING_FILING_CODE
            else:
                session.phase = FilingPhase.SELECTING_DOCUMENT_TYPE
        elif session.phase == FilingPhase.SELECTING_FILING_CODE and (
            sel.get("filing_code") or sel.get("filing_code_code")
        ):
            # Normalize to the bare ``filing_code`` scalar the payload
            # assembler expects. ``filter_selections_update`` now writes
            # both keys, but older sessions may only carry ``filing_code_code``.
            if not sel.get("filing_code") and sel.get("filing_code_code"):
                sel["filing_code"] = sel["filing_code_code"]
            session.phase = FilingPhase.SELECTING_DOCUMENT_TYPE
        elif session.phase == FilingPhase.SELECTING_DOCUMENT_TYPE and sel.get(
            "template_questions_ready"
        ):
            session.phase = _phase_after_document_type_ready(sel)
        elif session.phase == FilingPhase.SELECTING_FILER_TYPE and sel.get(
            "filer_type"
        ):
            session.phase = _phase_after_filer_type(sel)
        elif session.phase == FilingPhase.SELECTING_FILING_TYPE and sel.get(
            "filing_type"
        ):
            session.phase = FilingPhase.OFFERING_DOCUMENTS
        elif session.phase == FilingPhase.SELECTING_COUNTY and sel.get("county_name"):
            session.phase = FilingPhase.SELECTING_JURISDICTION
    elif session.mode == FilingMode.FILING_EXISTING:
        if (
            session.phase == FilingPhase.EXISTING_SELECTING_STATE
            and sel.get("state_code")
        ):
            session.phase = FilingPhase.EXISTING_SELECTING_JURISDICTION
        elif (
            session.phase == FilingPhase.EXISTING_SELECTING_JURISDICTION
            and sel.get("jurisdiction_code")
        ):
            session.phase = FilingPhase.EXISTING_ENTER_CASE_NUMBER
        elif session.phase == FilingPhase.SELECTING_DOCUMENT_TYPE and sel.get(
            "template_questions_ready"
        ):
            session.phase = FilingPhase.OFFERING_DOCUMENTS


def auto_pick_single_option(
    session: FilingSession, phase: FilingPhase, options: List[Dict[str, Any]]
) -> bool:
    """
    Auto-select and advance when a code lookup returned exactly one option.

    Returns ``True`` if the phase was auto-advanced. This is called from the
    node that just loaded ``options`` so the user is never asked to pick from
    a one-item dropdown (e.g. ``filing_type`` usually returns just ``EFile``).
    """
    if session.phase != phase or not isinstance(options, list) or len(options) != 1:
        return False
    row = options[0] if isinstance(options[0], dict) else {}
    code = str(row.get("code") or "").strip()
    if not code:
        return False

    if phase == FilingPhase.SELECTING_FILER_TYPE:
        session.selections["filer_type"] = code
        session.selections["filer_type_name"] = str(row.get("name") or code)
        advance_phase_after_selections(session)
        return True
    if phase == FilingPhase.SELECTING_FILING_TYPE:
        session.selections["filing_type"] = code
        session.selections["filing_type_name"] = str(row.get("name") or code)
        advance_phase_after_selections(session)
        return True
    return False


def question_visible(question: Dict[str, Any], answers: Dict[str, Any]) -> bool:
    from app.services.field_mapping_service import is_mapping_question_visible

    return is_mapping_question_visible(question, answers)


def apply_workflow_consolidation(
    session: FilingSession,
    result: Any,
    *,
    workflow_id: Optional[str] = None,
) -> None:
    """Apply consolidated workflow questions to the session."""
    if not result or not result.questions:
        return
    session.workflow_questions = list(result.questions)
    session.checklist = build_checklist_from_questions(
        str(workflow_id) if workflow_id else None, result.questions
    )
    session.metadata["workflow_questions_full"] = list(result.full_questions or [])
    session.metadata["workflow_field_aliases"] = dict(result.field_aliases or {})
    session.metadata["workflow_question_consolidation"] = {
        "original_count": result.original_count,
        "consolidated_count": result.consolidated_count,
        "summary": result.summary,
        "used_llm": result.used_llm,
    }
    session.selections["workflow_questions_consolidated"] = True


async def load_db_workflow_questions(
    filing_repo: Any, session: FilingSession
) -> tuple[List[Dict[str, Any]], Optional[str]]:
    """Load configured workflow questions for the current case selections."""
    sel = session.selections
    workflow_id = sel.get("workflow_id")
    questions: List[Dict[str, Any]] = []
    if workflow_id:
        questions = await filing_repo.get_workflow_questions(str(workflow_id))
    elif sel.get("case_type") or sel.get("case_type_code") or sel.get("case_type_name"):
        questions = await filing_repo.get_workflow_questions_by_case(
            str(
                sel.get("case_type")
                or sel.get("case_type_name")
                or sel.get("case_type_code")
                or ""
            ),
            str(sel.get("sub_case_type") or ""),
            sel.get("jurisdiction_code"),
        )
        if questions and questions[0].get("workflow_id"):
            workflow_id = str(questions[0]["workflow_id"])
            sel["workflow_id"] = workflow_id
    return questions, str(workflow_id) if workflow_id else None


def build_checklist_from_questions(
    workflow_id: Optional[str], questions: List[Dict[str, Any]]
) -> WorkflowChecklist:
    items = [
        ChecklistItem(
            field_name=q["field_name"],
            label=q.get("field_label") or q["field_name"],
            required=bool(q.get("required", True)),
            sort_order=int(q.get("sort_order") or 0),
            visibility_condition=q.get("visibility_condition"),
        )
        for q in questions
    ]
    items.sort(key=lambda i: (i.sort_order, i.field_name))
    return WorkflowChecklist(workflow_id=workflow_id, items=items)


def next_pending_question(session: FilingSession) -> Optional[Dict[str, Any]]:
    answered = set(session.collected_answers.keys())
    for item in sorted(
        session.checklist.items, key=lambda i: (i.sort_order, i.field_name)
    ):
        if item.status == "skipped":
            continue
        q = next(
            (
                q
                for q in session.workflow_questions
                if q["field_name"] == item.field_name
            ),
            None,
        )
        if q and not question_visible(q, session.collected_answers):
            item.status = "skipped"
            continue
        if item.field_name not in answered and item.status == "pending":
            return q or {"field_name": item.field_name, "field_label": item.label}
    return None


def merge_checklist_updates(
    session: FilingSession, updates: List[Dict[str, Any]]
) -> None:
    by_name = {i.field_name: i for i in session.checklist.items}
    for upd in updates or []:
        name = upd.get("field_name")
        if not name or name not in by_name:
            continue
        item = by_name[name]
        status = upd.get("status")
        if status in ("pending", "answered", "skipped"):
            item.status = status
        if "value" in upd:
            item.value = upd["value"]


def workflow_is_complete(session: FilingSession) -> bool:
    for item in session.checklist.items:
        q = next(
            (
                q
                for q in session.workflow_questions
                if q["field_name"] == item.field_name
            ),
            None,
        )
        if q and not question_visible(q, session.collected_answers):
            continue
        if item.status == "skipped":
            continue
        if item.required and item.status != "answered":
            if item.field_name not in session.collected_answers:
                return False
    return bool(session.workflow_questions)


async def init_workflow_phase(
    filing_repo: LegalFilingRepository,
    session: FilingSession,
    *,
    bedrock: Any = None,
) -> None:
    """Document questions come from the selected PDF + field_mapping, not RDS."""
    if session.selections.get("template_questions_ready"):
        return
    session.required_documents = await load_required_documents(
        filing_repo, session
    )
    if session.phase != FilingPhase.SELECTING_DOCUMENT_TYPE:
        session.phase = FilingPhase.SELECTING_DOCUMENT_TYPE


async def handle_lookup(
    filing_repo: LegalFilingRepository,
    session: FilingSession,
    action: Optional[str],
    params: Dict[str, Any],
) -> None:
    if not action:
        return
    if action == "party_search":
        party = params.get("party_names") or params.get("party") or ""
        results = await filing_repo.search_by_party_names(str(party))
        session.selections["search_results"] = results
        session.phase = FilingPhase.EXISTING_SEARCH_PARTY
    elif action == "date_search":
        date = params.get("filing_date") or params.get("date") or ""
        results = await filing_repo.search_by_filing_date(str(date))
        session.selections["search_results"] = results
        session.phase = FilingPhase.EXISTING_SEARCH_DATE
    elif action == "case_number":
        cn = params.get("case_number") or ""
        case = await filing_repo.get_user_details_by_case_number(str(cn))
        if case:
            session.selections["case_metadata"] = case
            session.selections["case_number"] = case.get("case_number")
            session.phase = FilingPhase.EXISTING_CASE_CONFIRM
    elif action == "confirm_case":
        session.phase = FilingPhase.SELECTING_DOCUMENT_TYPE


def result_from_session(
    session: FilingSession,
    message: str,
    event_kind: str = "assistant.message",
    *,
    metadata: Optional[Dict[str, Any]] = None,
    analysis: Optional[Dict[str, Any]] = None,
) -> OrchestratorResult:
    checklist_phases = (
        FilingPhase.OFFERING_DOCUMENTS,
        FilingPhase.AWAITING_DOCUMENT_UPLOAD,
        FilingPhase.COLLECTING_WORKFLOW_ANSWERS,
        FilingPhase.GENERATING_DOCUMENTS,
        FilingPhase.VERIFYING_PLATFORM_PAYMENT,
        FilingPhase.VERIFYING_COURT_PAYMENT,
        FilingPhase.CONFIRMING_EFILE,
        FilingPhase.COMPLETE,
    )
    checklist = (
        session.checklist.to_payload() if session.phase in checklist_phases else None
    )
    meta = dict(metadata or {})
    if session.chat_context:
        meta.setdefault("chat_context", list(session.chat_context))
    return OrchestratorResult(
        assistant_message=message,
        conversation_id=session.conversation_id,
        phase=session.phase,
        mode=session.mode,
        selections=session.selections,
        collected_answers=session.collected_answers,
        checklist=checklist,
        event_kind=event_kind,
        metadata=meta,
        analysis=analysis,
    )


def get_session(conversation_id: str, user_id: str) -> FilingSession:
    return FilingSessionManager.get_or_create(conversation_id, user_id)


def classify_document_offer_reply(
    text: str,
) -> Literal["yes", "no", "done", "unknown"]:
    raw = (text or "").strip()
    if not raw or raw.startswith("["):
        return "unknown"
    if _DONE_RE.search(raw):
        return "done"
    if _NO_RE.search(raw):
        return "no"
    if _YES_RE.search(raw):
        return "yes"
    return "unknown"


def uploads_from_state(state: FilingGraphState) -> List[Dict[str, Any]]:
    uploads = [dict(item) for item in (state.get("uploads") or [])]
    if state.get("file_bytes") or state.get("file_name"):
        single = {
            "file_bytes": state.get("file_bytes") or b"",
            "file_name": state.get("file_name") or "document",
            "file_path": state.get("file_path") or "",
            "file_type": state.get("file_type"),
        }
        if not uploads:
            return [single]
        if not any(u.get("file_name") == single["file_name"] for u in uploads):
            uploads.insert(0, single)
    return uploads


def is_new_filing_prefill_phase(session: FilingSession) -> bool:
    """Uploads in these phases prefill workflow answers instead of existing-case lookup."""
    if session.phase in (
        FilingPhase.OFFERING_DOCUMENTS,
        FilingPhase.AWAITING_DOCUMENT_UPLOAD,
    ):
        return True
    return bool(
        session.selections.get("template_questions_ready")
        and session.phase == FilingPhase.COLLECTING_WORKFLOW_ANSWERS
    )


def merge_prefilled_answers(
    session: FilingSession, answers: Dict[str, Any]
) -> Dict[str, Any]:
    """Fill unanswered fields only. Returns newly applied values."""
    newly: Dict[str, Any] = {}
    for key, value in (answers or {}).items():
        if value in (None, "", [], {}):
            continue
        current = session.collected_answers.get(key)
        if current not in (None, "", [], {}):
            continue
        session.collected_answers[key] = value
        newly[key] = value
    updates = [
        {"field_name": key, "status": "answered", "value": value}
        for key, value in newly.items()
    ]
    merge_checklist_updates(session, updates)
    for key, val in session.collected_answers.items():
        for item in session.checklist.items:
            if item.field_name == key and item.status != "skipped":
                item.status = "answered"
                item.value = val
    return newly


def uploaded_covers_template(
    uploaded_documents: List[Dict[str, Any]], template: Dict[str, Any]
) -> bool:
    code = str(template.get("template_code") or "").strip().lower()
    name = str(template.get("template_name") or "").strip().lower()
    for doc in uploaded_documents:
        if doc.get("ok") is False:
            continue
        classification = str(doc.get("classification") or "").strip().lower()
        file_name = str(doc.get("file_name") or "").strip().lower()
        haystack = f"{classification} {file_name}"
        if code and (code in haystack or classification == code):
            return True
        if name and name in haystack:
            return True
    return False


async def load_required_documents(
    filing_repo: LegalFilingRepository, session: FilingSession
) -> List[Dict[str, Any]]:
    """Load required filing templates by workflow_id, then case/jurisdiction."""
    sel = session.selections
    workflow_id = sel.get("workflow_id") or session.checklist.workflow_id
    docs: List[Dict[str, Any]] = []
    if workflow_id:
        docs = await filing_repo.get_document_templates_by_workflow_id(str(workflow_id))
    if not docs and sel.get("case_type"):
        docs = await filing_repo.get_document_templates_for_workflow(
            sel["case_type"],
            sel.get("sub_case_type") or "",
            sel.get("jurisdiction_code"),
        )
    return docs


def required_templates(session: FilingSession) -> List[Dict[str, Any]]:
    docs = session.required_documents or []
    required = [d for d in docs if d.get("is_required", True)]
    return required or list(docs)


def format_required_document_list(docs: List[Dict[str, Any]]) -> str:
    if not docs:
        return ""
    lines = []
    for doc in docs:
        name = doc.get("template_name") or doc.get("template_code") or "document"
        required = doc.get("is_required", True)
        suffix = " (required)" if required else " (optional)"
        lines.append(f"- {name}{suffix}")
    return "\n".join(lines)


async def analyze_uploads_concurrently(
    analysis_service: Any,
    uploads: List[Dict[str, Any]],
    *,
    concurrency: int = ANALYSIS_CONCURRENCY,
) -> List[Dict[str, Any]]:
    """Analyze each upload concurrently. One failure does not drop the rest."""
    if not uploads:
        return []
    sem = asyncio.Semaphore(max(1, concurrency))

    async def _one(item: Dict[str, Any]) -> Dict[str, Any]:
        file_name = item.get("file_name") or "document"
        async with sem:
            try:
                analysis = await analysis_service.analyze_document(
                    file_path=item.get("file_path") or "",
                    file_bytes=item.get("file_bytes") or b"",
                    file_name=file_name,
                    file_type=item.get("file_type"),
                )
                payload = (
                    analysis.model_dump(mode="json")
                    if hasattr(analysis, "model_dump")
                    else dict(analysis)
                )
                return {
                    "ok": True,
                    "file_name": file_name,
                    "classification": payload.get("document_classification"),
                    "analysis": payload,
                }
            except Exception as exc:  # noqa: BLE001
                logger.warning("Document analysis failed for %s: %s", file_name, exc)
                return {
                    "ok": False,
                    "file_name": file_name,
                    "classification": None,
                    "error": str(exc),
                    "analysis": None,
                }

    return list(await asyncio.gather(*[_one(item) for item in uploads]))
