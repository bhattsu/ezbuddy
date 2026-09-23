"""In-flow filing wizard help (chat history + step context, no catalog/API dumps)."""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from app.adapters.llm.bedrock import Bedrock, get_bedrock

if TYPE_CHECKING:
    from app.agents.conversation.orchestration.state import FilingSession
from app.agents.utils.db_options_format import build_phase_selection_message, slim_selections_for_llm
from app.agents.utils.text_sanitize import sanitize_assistant_text
from app.api.schemas.filing_events import FilingPhase
from app.core.prompts.context import format_llm_prompt
from app.core.prompts.filing_flow_guide import FILING_FLOW_GUIDE_PROMPT

logger = logging.getLogger(__name__)

_FLOW_HELP_RE = re.compile(
    r"(?i)(\?|what should|what do i|what to do|what to select|what do i select|"
    r"how do i|how can i|how to|help me|guide me|help\b|confused|next step|"
    r"what now|what's next|whats next|where am i|what is this step|"
    r"what does this mean|i don't understand|dont understand|not sure what|"
    r"can i change|change my|go back|wrong court|wrong state|which court|"
    r"which case type|what court|this screen|this step|use the dropdown|"
    r"how does this work|how do i file|how can i file|how do i apply|how can i apply|"
    r"start a case|file for|file a )"
)

_WIZARD_UI_RE = re.compile(
    r"(?i)(dropdown|this step|this screen|what to select|select from|"
    r"wizard|next step|guide me|help me|where am i)"
)

_GENERIC_LEGAL_RE = re.compile(
    r"(?i)(statute|statutes|legal requirement|residency requirement|"
    r"waiting period|deadline|statute of limitations|can i sue|my rights|"
    r"alimony|child support amount|grounds for|void marriage|annulment|"
    r"custody law|visitation rights|property division|spousal support|"
    r"is it legal|against the law|court rule says|texas law|family code)"
)

IntakeMessageKind = Literal["flow_guide", "generic_legal", "selection"]

_INTAKE_PHASES = frozenset(
    {
        FilingPhase.GREETING,
        FilingPhase.INTENT_PENDING,
        FilingPhase.SELECTING_STATE,
        FilingPhase.SELECTING_COUNTY,
        FilingPhase.SELECTING_JURISDICTION,
        FilingPhase.SELECTING_CASE_CATEGORY,
        FilingPhase.SELECTING_CASE_TYPE,
        FilingPhase.SELECTING_CASE_PARTIES,
        FilingPhase.SELECTING_FILER_TYPE,
        FilingPhase.SELECTING_FILING_CODE,
        FilingPhase.SELECTING_DOC_TYPE_CODE,
        FilingPhase.SELECTING_DOCUMENT_TYPE,
        FilingPhase.SELECTING_FILING_TYPE,
        FilingPhase.OFFERING_DOCUMENTS,
        FilingPhase.AWAITING_DOCUMENT_UPLOAD,
        FilingPhase.COLLECTING_WORKFLOW_ANSWERS,
        FilingPhase.CONFIRMING_WORKFLOW_ANSWERS,
        FilingPhase.VERIFYING_PLATFORM_PAYMENT,
        FilingPhase.VERIFYING_COURT_PAYMENT,
        FilingPhase.SELECTING_BRAINTREE_CARD,
        FilingPhase.CONFIRMING_PAYMENT_AUTHORIZATION,
        FilingPhase.CONFIRMING_EFILE,
    }
)


class FlowGuideOutput(BaseModel):
    assistant_message: str = Field(default="")


def looks_like_flow_help(user_message: str) -> bool:
    text = str(user_message or "").strip()
    if not text or text.startswith("["):
        return False
    if len(text) < 5:
        return False
    return bool(_FLOW_HELP_RE.search(text))


def looks_like_intake_question(user_message: str) -> bool:
    """Free-text that is unlikely to be a dropdown filter (e.g. court name fragment)."""
    text = str(user_message or "").strip()
    if not text or text.startswith("["):
        return False
    if len(text) < 12:
        return False
    if "?" in text:
        return True
    lower = text.lower()
    if _WIZARD_UI_RE.search(text):
        return True
    starters = (
        "how ",
        "what ",
        "which ",
        "where ",
        "can i ",
        "should i ",
        "do i ",
        "why ",
        "when ",
        "could i ",
    )
    return any(lower.startswith(starter) for starter in starters)


def looks_like_generic_legal_question(user_message: str) -> bool:
    text = str(user_message or "").strip()
    if not text or text.startswith("["):
        return False
    if len(text) < 15:
        return False
    if _WIZARD_UI_RE.search(text):
        return False
    if looks_like_flow_help(text) and not _GENERIC_LEGAL_RE.search(text):
        return False
    if _GENERIC_LEGAL_RE.search(text):
        return True
    lower = text.lower()
    if "?" not in text:
        return False
    legal_hints = (
        " law",
        " legal",
        " court order",
        " judge",
        " attorney",
        " lawyer",
        " sue ",
        " lawsuit",
        " divorce decree",
        " petition",
    )
    process_hints = (
        "how do i file",
        "how can i file",
        "how do i apply",
        "how can i apply",
        "how to file",
        "how to apply",
        "what do i select",
        "what should i select",
    )
    if any(h in lower for h in process_hints):
        return False
    return any(h in lower for h in legal_hints)


def classify_intake_user_message(
    user_message: str,
    *,
    phase: Optional[FilingPhase] = None,
    navigation_intent: Optional[str] = None,
    navigation_intent_resolved: bool = False,
) -> IntakeMessageKind:
    text = str(user_message or "").strip()
    if not text or text.startswith("["):
        return "selection"
    if phase == FilingPhase.INTENT_PENDING:
        nav = navigation_intent
        if nav is None and not navigation_intent_resolved:
            from app.agents.conversation.orchestration.helpers import (
                classify_navigation_intent_from_text,
            )

            nav = classify_navigation_intent_from_text(text)
        if nav in ("filing_new", "filing_existing", "check_status"):
            return "selection"
        if nav == "generic_legal":
            return "generic_legal"
        if navigation_intent_resolved:
            return "selection"
    if looks_like_generic_legal_question(text):
        return "generic_legal"
    if looks_like_flow_help(text) or looks_like_intake_question(text):
        return "flow_guide"
    return "selection"


def _selections_summary(session: "FilingSession") -> str:
    slim = slim_selections_for_llm(session.selections or {})
    keys = (
        "state_code",
        "state_name",
        "jurisdiction_code",
        "jurisdiction_name",
        "case_category_code",
        "case_category_name",
        "case_type_code",
        "case_type_name",
        "party_type_code",
        "party_type_name",
        "document_type_name",
        "doc_type",
        "filing_code",
        "template_code",
    )
    picked = {key: slim[key] for key in keys if slim.get(key)}
    if not picked:
        return "(none yet)"
    return json.dumps(picked, default=str)


class FilingFlowGuideService:
    def __init__(self, bedrock: Optional[Bedrock] = None):
        self.bedrock = bedrock or get_bedrock()

    async def answer(
        self,
        *,
        session: "FilingSession",
        user_message: str,
        history: List[Dict[str, str]],
        step_instruction: str = "",
    ) -> str:
        from app.agents.conversation.orchestration.chat_context import (
            format_request_response_history,
        )

        if session.phase not in _INTAKE_PHASES and session.phase != FilingPhase.COMPLETE:
            step_instruction = step_instruction or f"Current phase is {session.phase.value}."

        prompt = format_llm_prompt(
            FILING_FLOW_GUIDE_PROMPT,
            mode=session.mode.value,
            phase=session.phase.value,
            selections_summary=_selections_summary(session),
            step_instruction=step_instruction or "(use the dropdown for this step)",
            history_text=format_request_response_history(history),
            user_message=user_message[:2000],
        )
        try:
            parsed = await self.bedrock.invoke_structured_prompt(prompt, FlowGuideOutput)
            text = str(parsed.assistant_message or "").strip()
            if text:
                return sanitize_assistant_text(text)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Flow guide structured invoke failed: %s", exc)

        return sanitize_assistant_text(
            f"You are on step {session.phase.value.replace('_', ' ')}. "
            f"{step_instruction or 'Please use the dropdown to choose the option that fits your case.'}"
        )
