"""Agentic phase redirects and fallback guidance for filing chat."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from app.agents.conversation.orchestration.helpers import (
    CASE_DETAIL_LINK_KEYS,
    is_affirmative_reply,
)
from app.agents.conversation.orchestration.state import (
    FilingSession,
    WorkflowChecklist,
)
from app.api.schemas.filing_events import FilingMode, FilingPhase
from app.services.court_catalog import infer_case_topic, normalize_catalog_text

_AFFIRM = frozenset(
    {"yes", "y", "ok", "okay", "sure", "please", "yeah", "correct", "go ahead"}
)

_COURT_RE = re.compile(
    r"\b("
    r"(?:select|choose|chose|pick|change|switch)\s+(?:a\s+)?(?:the\s+)?(?:different|another|new)\s+(?:court|jurisdiction)"
    r"|(?:different|another|new)\s+(?:court|jurisdiction)"
    r"|(?:go\s+back|back)\s+(?:to\s+)?(?:the\s+)?(?:court|jurisdiction)"
    r"|select\s+jurisdiction\s+again"
    r"|(?:want|need)\s+to\s+(?:select|choose|chose|pick|change)\s+(?:a\s+)?(?:different|another|new)\s+(?:court|jurisdiction)"
    r"|(?:want|need)\s+to\s+change\s+(?:the\s+)?(?:court|jurisdiction)\b"
    r"|change\s+(?:the\s+)?(?:court|jurisdiction)\b"
    r")\b",
    re.I,
)
# Catches phrasing the strict pattern misses, e.g. "change your court jurisdiction".
_JURISDICTION_CHANGE_LOOSE = re.compile(
    r"\b(?:"
    r"change\b[\w\s]{0,40}\b(?:court|jurisdiction)\b"
    r"|(?:different|another|new)\b[\w\s]{0,24}\b(?:court|jurisdiction)\b"
    r"|(?:court|jurisdiction)\b[\w\s]{0,40}\b(?:change|switch|pick|select)\b"
    r"|(?:pick|select|choose|switch)\b[\w\s]{0,24}\b(?:court|jurisdiction)\b"
    r")\b",
    re.I,
)
_LLM_JURISDICTION_RETURN_RE = re.compile(
    r"\b(?:return(?:ing)?|go(?:ing)?\s+back)\b[\w\s]{0,40}\b(?:court|jurisdiction)\b"
    r"|\bjurisdiction\s+selection\s+step\b"
    r"|\b(?:court|jurisdiction)\s+selection\b",
    re.I,
)
_CACHED_OPTION_KEYS = (
    "cached_jurisdictions",
    "cached_case_categories",
    "cached_case_types",
    "cached_party_types",
    "cached_filer_types",
    "cached_filing_codes",
    "cached_doc_type_codes",
    "cached_document_types",
    "cached_filing_types",
)
_CONFIRM_COURT_RE = re.compile(
    r"\bconfirm\b.*\bproceed\b"
    r"|\b(?:court|jurisdiction)\b.*\b(?:confirm|proceed|continue)\b"
    r"|\b(?:confirm|proceed|continue)\b.*\b(?:court|jurisdiction)\b",
    re.I,
)
_TOPIC_CHANGE_RE = re.compile(
    r"\b("
    r"(?:need|want|like|would\s+like)\s+to\s+file"
    r"|file\s+(?:a|an)\s+"
    r"|(?:switch|change)\s+(?:to|case\s+(?:type|category)\s+to)"
    r"|instead\s+of"
    r")\b",
    re.I,
)
_MID_FLOW_TOPIC_PHASES = frozenset(
    {
        FilingPhase.SELECTING_JURISDICTION,
        FilingPhase.SELECTING_CASE_CATEGORY,
        FilingPhase.SELECTING_CASE_TYPE,
        FilingPhase.SELECTING_CASE_PARTIES,
        FilingPhase.SELECTING_FILER_TYPE,
        FilingPhase.SELECTING_FILING_TYPE,
        FilingPhase.SELECTING_FILING_CODE,
        FilingPhase.SELECTING_DOC_TYPE_CODE,
        FilingPhase.SELECTING_DOCUMENT_TYPE,
        FilingPhase.COLLECTING_WORKFLOW_ANSWERS,
        FilingPhase.CONFIRMING_WORKFLOW_ANSWERS,
        FilingPhase.OFFERING_DOCUMENTS,
        FilingPhase.AWAITING_DOCUMENT_UPLOAD,
    }
)
_CATEGORY_RE = re.compile(
    r"\b(?:change|select|choose|pick|switch|different|another)\s+(?:the\s+)?(?:case\s+)?category\b",
    re.I,
)
_CASE_TYPE_RE = re.compile(
    r"\b(?:change|select|choose|pick|switch|different|another)\s+(?:the\s+)?case\s+type\b",
    re.I,
)
_PARTY_TYPE_RE = re.compile(
    r"\b(?:change|select|choose|pick|switch|different|another)\s+(?:the\s+)?party(?:\s+type)?\b"
    r"|\b(?:need|want)\s+to\s+change\s+(?:the\s+)?party(?:\s+type)?\b",
    re.I,
)
_STATE_RE = re.compile(
    r"\b(?:change|select|choose|switch|different|another)\s+(?:the\s+)?state\b",
    re.I,
)
_FILING_CODE_RE = re.compile(
    r"\b(?:change|select|choose|pick|switch|different|another)\s+(?:the\s+)?filing\s+code\b",
    re.I,
)
_DOC_TYPE_RE = re.compile(
    r"\b(?:change|select|choose|pick|switch|different|another)\s+(?:the\s+)?(?:doc(?:ument)?\s+type|court\s+document\s+type)\b",
    re.I,
)
_DOCUMENT_TEMPLATE_RE = re.compile(
    r"\b(?:change|select|choose|pick|switch|different|another)\s+(?:the\s+)?(?:document\s+type|template)\b",
    re.I,
)
_RETRY_CASE_NUMBER_RE = re.compile(
    r"\b(?:try\s+again|retry|re-?enter|enter\s+again|different\s+case\s+number|new\s+case\s+number)\b",
    re.I,
)
_RESTART_SEARCH_RE = re.compile(
    r"\b(?:start\s+over|from\s+(?:the\s+)?start|search\s+case\s+again|search\s+again\s+from\s+start)\b",
    re.I,
)
_WRONG_CASE_RE = re.compile(
    r"\b(?:wrong\s+case|not\s+(?:the\s+)?right\s+case|different\s+case)\b",
    re.I,
)
_STEP_HINTS = {
    "court": FilingPhase.SELECTING_JURISDICTION,
    "jurisdiction": FilingPhase.SELECTING_JURISDICTION,
    "category": FilingPhase.SELECTING_CASE_CATEGORY,
    "case type": FilingPhase.SELECTING_CASE_TYPE,
    "case number": FilingPhase.EXISTING_ENTER_CASE_NUMBER,
    "filing code": FilingPhase.SELECTING_FILING_CODE,
    "document type": FilingPhase.SELECTING_DOC_TYPE_CODE,
    "payment": FilingPhase.VERIFYING_COURT_PAYMENT,
}
_GO_TO_STEP_RE = re.compile(
    r"\b(?:go\s+(?:back\s+)?to|jump\s+to|return\s+to|take\s+me\s+to)\s+(?:the\s+)?(.+?)\s*(?:step|phase|screen)?\s*$",
    re.I,
)

_PENDING_KEY = "_pending_flow_redirect"
_FRESH_CATALOG_PHASE_KEY = "_fresh_catalog_phase"
_TOPIC_FILTER_KEYS = (
    "matched_court_codes",
    "matched_case_type_codes",
    "case_intent",
)
_FRESH_CATALOG_LABELS = frozenset(
    {
        "change_court_new",
        "change_court_existing",
        "change_category",
        "change_case_type",
        "confirm_court_change",
        "confirmed_redirect",
    }
)
_FRESH_CATALOG_PHASES = frozenset(
    {
        FilingPhase.SELECTING_JURISDICTION,
        FilingPhase.EXISTING_SELECTING_JURISDICTION,
        FilingPhase.SELECTING_CASE_CATEGORY,
        FilingPhase.SELECTING_CASE_TYPE,
    }
)


@dataclass(frozen=True)
class FlowRedirect:
    target_phase: FilingPhase
    clear_workflow: bool = False
    label: str = ""


def _normalize_redirect_text(text: str) -> str:
    """Fix common typos before redirect pattern matching."""
    normalized = str(text or "").strip()
    normalized = re.sub(r"\bdi+[f]+erent\b", "different", normalized, flags=re.I)
    normalized = re.sub(r"\bde+fer+ent\b", "different", normalized, flags=re.I)
    normalized = re.sub(r"\bjurisdition\b", "jurisdiction", normalized, flags=re.I)
    return normalized


def looks_like_flow_redirect(text: str) -> bool:
    """Fast check for router use (does not evaluate pending yes/no offers)."""
    raw = _normalize_redirect_text(text)
    if not raw:
        return False
    return bool(
        _COURT_RE.search(raw)
        or _JURISDICTION_CHANGE_LOOSE.search(raw)
        or _CATEGORY_RE.search(raw)
        or _CASE_TYPE_RE.search(raw)
        or _PARTY_TYPE_RE.search(raw)
        or _STATE_RE.search(raw)
        or _FILING_CODE_RE.search(raw)
        or _DOC_TYPE_RE.search(raw)
        or _DOCUMENT_TEMPLATE_RE.search(raw)
        or _RESTART_SEARCH_RE.search(raw)
        or _RETRY_CASE_NUMBER_RE.search(raw)
        or _WRONG_CASE_RE.search(raw)
        or _GO_TO_STEP_RE.search(raw)
        or _TOPIC_CHANGE_RE.search(raw)
    )


def resolve_flow_redirect(
    session: FilingSession,
    user_message: str,
    *,
    last_assistant_message: str = "",
) -> Optional[FlowRedirect]:
    """Detect whether the user wants to jump back to an earlier filing step."""
    raw = _normalize_redirect_text(user_message)
    if not raw or raw.startswith("["):
        return None

    lowered = raw.lower()
    court_confirm = _resolve_court_change_confirmation(
        session, lowered, last_assistant_message
    )
    if court_confirm:
        return court_confirm
    if (
        session.mode == FilingMode.FILING_EXISTING
        and session.phase == FilingPhase.EXISTING_CASE_CONFIRM
    ):
        session.selections.pop(_PENDING_KEY, None)
        if is_affirmative_reply(lowered):
            return None
        if lowered in {"no", "n", "wrong", "incorrect", "not this one"}:
            return FlowRedirect(
                target_phase=FilingPhase.EXISTING_ENTER_CASE_NUMBER,
                label="reject_case_confirm",
            )
    if lowered in _AFFIRM:
        pending = str(session.selections.get(_PENDING_KEY) or "").strip()
        if pending:
            try:
                phase = FilingPhase(pending)
                return FlowRedirect(target_phase=phase, label="confirmed_redirect")
            except ValueError:
                session.selections.pop(_PENDING_KEY, None)

    if _COURT_RE.search(raw) or _JURISDICTION_CHANGE_LOOSE.search(raw):
        return _court_redirect(session.mode)
    topic_redirect = _topic_change_redirect(session, raw)
    if topic_redirect:
        return topic_redirect
    if _STATE_RE.search(raw):
        return FlowRedirect(
            target_phase=(
                FilingPhase.EXISTING_SELECTING_STATE
                if session.mode == FilingMode.FILING_EXISTING
                else FilingPhase.SELECTING_STATE
            ),
            clear_workflow=True,
            label="change_state",
        )
    if _CATEGORY_RE.search(raw):
        return FlowRedirect(
            target_phase=FilingPhase.SELECTING_CASE_CATEGORY,
            clear_workflow=True,
            label="change_category",
        )
    if _CASE_TYPE_RE.search(raw):
        return FlowRedirect(
            target_phase=FilingPhase.SELECTING_CASE_TYPE,
            clear_workflow=True,
            label="change_case_type",
        )
    if _PARTY_TYPE_RE.search(raw):
        return FlowRedirect(
            target_phase=FilingPhase.SELECTING_CASE_PARTIES,
            clear_workflow=True,
            label="change_party_type",
        )
    if _FILING_CODE_RE.search(raw):
        return FlowRedirect(
            target_phase=FilingPhase.SELECTING_FILING_CODE,
            label="change_filing_code",
        )
    if _DOC_TYPE_RE.search(raw):
        return FlowRedirect(
            target_phase=FilingPhase.SELECTING_DOC_TYPE_CODE,
            label="change_doc_type_code",
        )
    if _DOCUMENT_TEMPLATE_RE.search(raw) and session.mode == FilingMode.FILING_NEW:
        return FlowRedirect(
            target_phase=FilingPhase.SELECTING_DOCUMENT_TYPE,
            clear_workflow=True,
            label="change_document_template",
        )

    if session.mode == FilingMode.FILING_EXISTING:
        if _RESTART_SEARCH_RE.search(raw) or (
            _WRONG_CASE_RE.search(raw) and session.phase != FilingPhase.EXISTING_CASE_CONFIRM
        ):
            return _court_redirect(session.mode)
        if _RETRY_CASE_NUMBER_RE.search(raw):
            return FlowRedirect(
                target_phase=FilingPhase.EXISTING_ENTER_CASE_NUMBER,
                label="retry_case_number",
            )

    go_to = _GO_TO_STEP_RE.search(raw)
    if go_to:
        hint = str(go_to.group(1) or "").strip().lower()
        for key, phase in _STEP_HINTS.items():
            if key in hint:
                if session.mode == FilingMode.FILING_EXISTING and phase == FilingPhase.SELECTING_JURISDICTION:
                    return _court_redirect(session.mode)
                return FlowRedirect(
                    target_phase=phase,
                    clear_workflow=phase
                    in {
                        FilingPhase.SELECTING_CASE_CATEGORY,
                        FilingPhase.SELECTING_CASE_TYPE,
                        FilingPhase.SELECTING_DOCUMENT_TYPE,
                    },
                    label=f"go_to_{key.replace(' ', '_')}",
                )

    if "different court" in last_assistant_message.lower() and lowered in _AFFIRM:
        return _court_redirect(session.mode)

    return None


def _resolve_court_change_confirmation(
    session: FilingSession,
    lowered: str,
    last_assistant_message: str,
) -> Optional[FlowRedirect]:
    """After the LLM asks to confirm a new court, ``yes`` restarts category selection."""
    if not is_affirmative_reply(lowered):
        return None
    last = str(last_assistant_message or "")
    if not _CONFIRM_COURT_RE.search(last):
        return None
    if not session.selections.get("jurisdiction_code"):
        return None
    session.selections.pop(_PENDING_KEY, None)
    return FlowRedirect(
        target_phase=FilingPhase.SELECTING_CASE_CATEGORY,
        clear_workflow=True,
        label="confirm_court_change",
    )


def sync_phase_after_court_change(
    session: FilingSession,
    prior_jurisdiction_code: str,
) -> bool:
    """
    If jurisdiction changed while the session was past court selection, drop stale
    category/type/filing selections so phase and dropdowns stay aligned.
    """
    if session.mode != FilingMode.FILING_NEW:
        return False
    new_code = str(session.selections.get("jurisdiction_code") or "").strip()
    prior = str(prior_jurisdiction_code or "").strip()
    if not new_code or new_code == prior:
        return False
    if session.phase in (
        FilingPhase.SELECTING_JURISDICTION,
        FilingPhase.SELECTING_STATE,
        FilingPhase.INTENT_PENDING,
        FilingPhase.GREETING,
    ):
        return False
    _clear_from_phase(session, FilingPhase.SELECTING_CASE_CATEGORY)
    _reset_workflow_state(session)
    session.phase = FilingPhase.SELECTING_CASE_CATEGORY
    session.selections.pop(_PENDING_KEY, None)
    return True


def _topic_change_redirect(session: FilingSession, raw: str) -> Optional[FlowRedirect]:
    """Restart court selection when the user switches case topic mid-flow."""
    if session.mode != FilingMode.FILING_NEW:
        return None
    if session.phase not in _MID_FLOW_TOPIC_PHASES:
        return None
    if not _TOPIC_CHANGE_RE.search(raw):
        return None
    topic = infer_case_topic(raw)
    if not topic or len(topic) < 3:
        return None
    prior = normalize_catalog_text(session.selections.get("case_topic"))
    new_topic = normalize_catalog_text(topic)
    if (
        new_topic == prior
        and session.selections.get("jurisdiction_code")
        and session.phase != FilingPhase.SELECTING_JURISDICTION
    ):
        return None
    session.selections["case_topic"] = topic
    session.selections.pop("matched_court_codes", None)
    session.selections.pop("matched_case_type_codes", None)
    session.selections.pop("case_intent", None)
    return FlowRedirect(
        target_phase=FilingPhase.SELECTING_JURISDICTION,
        clear_workflow=True,
        label="change_case_topic",
    )


def _court_redirect(mode: FilingMode) -> FlowRedirect:
    if mode == FilingMode.FILING_EXISTING:
        return FlowRedirect(
            target_phase=FilingPhase.EXISTING_SELECTING_JURISDICTION,
            clear_workflow=True,
            label="change_court_existing",
        )
    return FlowRedirect(
        target_phase=FilingPhase.SELECTING_JURISDICTION,
        clear_workflow=True,
        label="change_court_new",
    )


def clear_pending_flow_redirect(session: FilingSession) -> None:
    session.selections.pop(_PENDING_KEY, None)


def is_fresh_catalog_phase(selections: Dict[str, Any], phase: FilingPhase) -> bool:
    """True when the user explicitly restarted this step and wants full lists."""
    fresh = str(selections.get(_FRESH_CATALOG_PHASE_KEY) or "").strip().lower()
    return fresh == phase.value.lower()


def clear_fresh_catalog_on_selection(session: FilingSession) -> None:
    """Drop the fresh-list flag once the user picks an option at that step."""
    fresh = str(session.selections.get(_FRESH_CATALOG_PHASE_KEY) or "").strip().lower()
    if not fresh:
        return
    sel = session.selections
    if fresh == FilingPhase.SELECTING_JURISDICTION.value and sel.get("jurisdiction_code"):
        session.selections.pop(_FRESH_CATALOG_PHASE_KEY, None)
    elif fresh == FilingPhase.EXISTING_SELECTING_JURISDICTION.value and sel.get(
        "jurisdiction_code"
    ):
        session.selections.pop(_FRESH_CATALOG_PHASE_KEY, None)
    elif fresh == FilingPhase.SELECTING_CASE_CATEGORY.value and sel.get(
        "case_category_code"
    ):
        session.selections.pop(_FRESH_CATALOG_PHASE_KEY, None)
    elif fresh == FilingPhase.SELECTING_CASE_TYPE.value and sel.get("case_type_code"):
        session.selections.pop(_FRESH_CATALOG_PHASE_KEY, None)


def _mark_fresh_catalog_phase(session: FilingSession, redirect: FlowRedirect) -> None:
    """Show unfiltered court/category/type lists after an explicit change request."""
    if redirect.label == "change_case_topic":
        session.selections.pop(_FRESH_CATALOG_PHASE_KEY, None)
        return
    label_ok = redirect.label in _FRESH_CATALOG_LABELS or redirect.label.startswith(
        "go_to_"
    )
    if not label_ok:
        return
    if redirect.target_phase not in _FRESH_CATALOG_PHASES:
        return
    session.selections[_FRESH_CATALOG_PHASE_KEY] = redirect.target_phase.value
    if redirect.target_phase in (
        FilingPhase.SELECTING_JURISDICTION,
        FilingPhase.EXISTING_SELECTING_JURISDICTION,
    ):
        for key in _TOPIC_FILTER_KEYS:
            session.selections.pop(key, None)


def apply_flow_redirect(session: FilingSession, redirect: FlowRedirect) -> str:
    """Move the session to ``redirect.target_phase`` and clear downstream data."""
    session.selections.pop(_PENDING_KEY, None)
    _clear_from_phase(session, redirect.target_phase)
    if redirect.clear_workflow:
        _reset_workflow_state(session)
    _clear_cached_option_lists(session)
    _mark_fresh_catalog_phase(session, redirect)
    session.phase = redirect.target_phase
    return _redirect_message(session, redirect)


def reconcile_missed_flow_redirect(
    session: FilingSession,
    user_message: str,
    *,
    assistant_message: str = "",
) -> Optional[str]:
    """Apply a step redirect when the LLM replied but phase/options were not reset."""
    jurisdiction_msg = reconcile_missed_jurisdiction_redirect(
        session, user_message, assistant_message=assistant_message
    )
    if jurisdiction_msg:
        return jurisdiction_msg
    redirect = resolve_flow_redirect(
        session, user_message, last_assistant_message=assistant_message
    )
    if not redirect:
        return None
    if session.phase == redirect.target_phase:
        if redirect.label == "change_party_type" and session.selections.get("filing_code"):
            return apply_flow_redirect(session, redirect)
        return None
    return apply_flow_redirect(session, redirect)


def reconcile_missed_jurisdiction_redirect(
    session: FilingSession,
    user_message: str,
    *,
    assistant_message: str = "",
) -> Optional[str]:
    """
    Safety net when the LLM acknowledged a court change but phase/options were
    not reset (regex miss or model reply without orchestrator redirect).
    """
    if session.mode != FilingMode.FILING_NEW:
        return None
    raw = _normalize_redirect_text(user_message)
    user_wants = bool(
        _COURT_RE.search(raw) or _JURISDICTION_CHANGE_LOOSE.search(raw)
    )
    llm_says_return = bool(
        _LLM_JURISDICTION_RETURN_RE.search(str(assistant_message or ""))
    )
    if not user_wants and not llm_says_return:
        return None
    target = FilingPhase.SELECTING_JURISDICTION
    if (
        session.phase == target
        and not session.selections.get("jurisdiction_code")
    ):
        return None
    return apply_flow_redirect(session, _court_redirect(session.mode))


def _clear_cached_option_lists(session: FilingSession) -> None:
    for key in _CACHED_OPTION_KEYS:
        session.selections.pop(key, None)


def enrich_failure_with_guidance(
    session: FilingSession,
    message: str,
    *,
    context: str = "case_search",
) -> str:
    """Append next-step guidance and store a pending redirect offer."""
    base = str(message or "").strip()
    if context == "case_search":
        session.selections[_PENDING_KEY] = FilingPhase.EXISTING_SELECTING_JURISDICTION.value
        guidance = (
            "You can enter a different case number to try again, "
            "or reply **yes** to select a different court."
        )
    elif context == "no_options":
        if session.mode == FilingMode.FILING_EXISTING:
            session.selections[_PENDING_KEY] = (
                FilingPhase.EXISTING_SELECTING_JURISDICTION.value
            )
            guidance = (
                "None of the listed options fit. Reply **yes** to pick a different court, "
                "or tell me which step you want to change (for example, case category or case type)."
            )
        else:
            session.selections[_PENDING_KEY] = FilingPhase.SELECTING_JURISDICTION.value
            guidance = (
                "None of the listed options fit your case. "
                "Reply **yes** to choose a different court, or say which detail you want to change."
            )
    else:
        guidance = "Tell me which step you want to change and I will take you there."
    if guidance.lower() in base.lower():
        return base
    return f"{base}\n\n{guidance}"


def looks_like_case_number_entry(session: FilingSession, text: str) -> bool:
    """True when the message is a case number, not a navigation request."""
    raw = str(text or "").strip()
    if not raw or resolve_flow_redirect(session, raw):
        return False
    compact = re.sub(r"[\s\-/\.]", "", raw)
    return bool(compact) and compact.isalnum() and sum(ch.isdigit() for ch in compact) >= 3


def _reset_workflow_state(session: FilingSession) -> None:
    session.collected_answers = {}
    session.workflow_questions = []
    session.checklist = WorkflowChecklist()
    session.required_documents = []
    session.generated_documents = []
    for key in (
        "template_questions_ready",
        "template_id",
        "document_type_code",
        "document_type_name",
        "workflow_questions_consolidated",
        "workflow_id",
    ):
        session.selections.pop(key, None)


def _clear_from_phase(session: FilingSession, target: FilingPhase) -> None:
    sel = session.selections
    for key in _keys_to_clear(session.mode, target):
        sel.pop(key, None)


def _keys_to_clear(mode: FilingMode, target: FilingPhase) -> List[str]:
    keys: List[str] = []
    if mode == FilingMode.FILING_EXISTING:
        if target == FilingPhase.EXISTING_SELECTING_STATE:
            keys.extend(_EXISTING_STATE_AND_BELOW)
        elif target == FilingPhase.EXISTING_SELECTING_JURISDICTION:
            keys.extend(_EXISTING_JURISDICTION_AND_BELOW)
        elif target == FilingPhase.EXISTING_ENTER_CASE_NUMBER:
            keys.extend(_EXISTING_CASE_LOOKUP_AND_BELOW)
        elif target == FilingPhase.SELECTING_FILING_CODE:
            keys.extend(_EXISTING_FILING_AND_BELOW)
        elif target == FilingPhase.SELECTING_DOC_TYPE_CODE:
            keys.extend(_EXISTING_DOC_TYPE_AND_BELOW)
        elif target == FilingPhase.SELECTING_DOCUMENT_TYPE:
            keys.extend(_TEMPLATE_AND_BELOW)
    elif mode == FilingMode.FILING_NEW:
        if target == FilingPhase.SELECTING_STATE:
            keys.extend(_NEW_STATE_AND_BELOW)
        elif target == FilingPhase.SELECTING_JURISDICTION:
            keys.extend(_NEW_JURISDICTION_AND_BELOW)
        elif target == FilingPhase.SELECTING_CASE_CATEGORY:
            keys.extend(_NEW_CATEGORY_AND_BELOW)
        elif target == FilingPhase.SELECTING_CASE_TYPE:
            keys.extend(_NEW_CASE_TYPE_AND_BELOW)
        elif target == FilingPhase.SELECTING_CASE_PARTIES:
            keys.extend(_NEW_PARTY_AND_BELOW)
        elif target == FilingPhase.SELECTING_FILING_CODE:
            keys.extend(_NEW_FILING_CODE_AND_BELOW)
        elif target == FilingPhase.SELECTING_DOCUMENT_TYPE:
            keys.extend(_TEMPLATE_AND_BELOW)
    return keys


def _link_url_keys() -> List[str]:
    return [f"{key}_url" for key in CASE_DETAIL_LINK_KEYS]


# Defined leaf-to-root so forward references are valid at import time.
_TEMPLATE_AND_BELOW = [
    "document_type_code",
    "document_type_name",
    "template_id",
    "template_questions_ready",
    "workflow_id",
    "workflow_questions_consolidated",
    "court_payment_account_id",
    "court_payment_accounts",
    "reference_id",
    "envelope_id",
]

_EXISTING_DOC_TYPE_AND_BELOW = [
    "doc_type_code",
    "doc_type_name",
    "document_type_codes_url",
    *_TEMPLATE_AND_BELOW,
]

_EXISTING_FILING_AND_BELOW = [
    "filing_code",
    "filing_code_name",
    "filing_code_code",
    "filing_codes_url",
    *_EXISTING_DOC_TYPE_AND_BELOW,
]

_EXISTING_CASE_LOOKUP_AND_BELOW = [
    "case_number",
    "case_tracking_id",
    "case_title",
    "case_metadata",
    "case_details",
    "case_search_result",
    "existing_case_parties",
    "filing_party_id",
    "case_detail_links",
    "search_results",
    *_link_url_keys(),
    *_EXISTING_FILING_AND_BELOW,
]

_EXISTING_JURISDICTION_AND_BELOW = [
    "jurisdiction_code",
    "jurisdiction_name",
    "selected_jurisdiction",
    "cached_jurisdictions",
    *_EXISTING_CASE_LOOKUP_AND_BELOW,
]

_EXISTING_STATE_AND_BELOW = [
    "state_code",
    "state_name",
    *_EXISTING_JURISDICTION_AND_BELOW,
]

_NEW_FILING_CODE_AND_BELOW = [
    "filing_code",
    "filing_code_name",
    "filing_code_code",
    "document_type_codes_url",
    "filer_type",
    "filer_type_name",
    "filing_type",
    "filing_type_name",
    *_TEMPLATE_AND_BELOW,
]

_NEW_PARTY_AND_BELOW = [
    "party_type_code",
    "party_type_name",
    "selected_party_type",
    "case_parties",
    "parties_complete",
    *_NEW_FILING_CODE_AND_BELOW,
]

_NEW_CASE_TYPE_AND_BELOW = [
    "case_type_code",
    "case_type_name",
    "case_type",
    "sub_case_type",
    "party_type_code",
    "party_type_name",
    "party_type_codes_url",
    "case_subtype_codes_url",
    "filer_type_codes_url",
    "filing_type_url",
    "filing_codes_url",
    *_NEW_FILING_CODE_AND_BELOW,
]

_NEW_CATEGORY_AND_BELOW = [
    "case_category_code",
    "case_category_name",
    "case_type_codes_url",
    *_NEW_CASE_TYPE_AND_BELOW,
]

_NEW_JURISDICTION_AND_BELOW = [
    "jurisdiction_code",
    "jurisdiction_name",
    "selected_jurisdiction",
    "cached_jurisdictions",
    "case_category_codes_url",
    *_TOPIC_FILTER_KEYS,
    *_NEW_CATEGORY_AND_BELOW,
]

_NEW_STATE_AND_BELOW = [
    "state_code",
    "state_name",
    "county_name",
    "county_id",
    *_NEW_JURISDICTION_AND_BELOW,
]


def _redirect_message(session: FilingSession, redirect: FlowRedirect) -> str:
    phase = redirect.target_phase
    sel = session.selections
    if phase == FilingPhase.EXISTING_SELECTING_JURISDICTION:
        county = sel.get("jurisdiction_name") or sel.get("state_name") or "your state"
        return (
            f"Understood. Please select the court for your filing from the list for "
            f"{county}."
        )
    if phase == FilingPhase.SELECTING_JURISDICTION:
        topic = str(sel.get("case_topic") or "").strip()
        if redirect.label == "change_case_topic" and topic:
            return (
                f"Understood. You would like to file a {topic} case. "
                "Please select the court for your filing from the available options."
            )
        return "Understood. Please select the court for your filing from the available options."
    if phase == FilingPhase.SELECTING_CASE_CATEGORY:
        court = sel.get("jurisdiction_name") or sel.get("jurisdiction_code") or "the selected court"
        if redirect.label == "confirm_court_change":
            return (
                f"Thank you for confirming. Your court has been updated to {court}. "
                "Please select the case category to continue."
            )
        return f"Understood. Please select the case category for {court}."
    if phase == FilingPhase.SELECTING_CASE_TYPE:
        category = sel.get("case_category_name") or sel.get("case_category_code") or "your category"
        return f"Understood. Please select the case type under {category}."
    if phase == FilingPhase.SELECTING_CASE_PARTIES:
        case_type = sel.get("case_type_name") or sel.get("case_type_code") or "your case type"
        return f"Understood. Please select the party type for {case_type}."
    if phase == FilingPhase.EXISTING_ENTER_CASE_NUMBER:
        court = sel.get("jurisdiction_name") or sel.get("jurisdiction_code") or "the selected court"
        return f"Understood. Please enter the existing case number for {court}."
    if phase == FilingPhase.SELECTING_FILING_CODE:
        return "Understood. Please select the filing code for the document you are filing."
    if phase == FilingPhase.SELECTING_DOC_TYPE_CODE:
        return "Understood. Please select the court document type for this filing."
    if phase == FilingPhase.SELECTING_DOCUMENT_TYPE:
        return "Understood. Please select the document template to use."
    if phase in (FilingPhase.SELECTING_STATE, FilingPhase.EXISTING_SELECTING_STATE):
        return "Understood. Please select the state for your filing."
    return "Understood. Continue from the step shown below."
