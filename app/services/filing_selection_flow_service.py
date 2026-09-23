"""
Deterministic filing intake: dropdown steps via HTTP, reusing the same
session.selections keys and US Legal Pro / RDS loaders as the WebSocket flow.

No Bedrock navigation until form questions are extracted from the template.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional

from app.agents.conversation.orchestration.chat_context import (
    append_chat_turn,
    history_for_llm,
)
from app.agents.conversation.orchestration.context import FilingOrchestratorContext
from app.agents.conversation.orchestration.helpers import (
    advance_mode_from_intent,
    advance_phase_after_selections,
    apply_catalog_shortcuts,
    attach_selection_options_to_result,
    auth_token_for_user,
    cache_phase_options,
    get_session,
    merge_selections,
    options_for_response,
    persist_system_state,
    prefetch_court_catalog,
    restore_from_system_messages,
)
from app.agents.conversation.orchestration.nodes import (
    SELECTION_PHASES,
    _hydrate_template_questions,
)
from app.agents.conversation.orchestration.session_manager import FilingSessionManager
from app.agents.conversation.orchestration.state import FilingSession, OrchestratorResult
from app.agents.utils.db_options_format import (
    build_phase_selection_message,
    filter_selections_update,
    match_option,
    selection_update_for_option,
)
from app.api.schemas.filing_events import FilingMode, FilingPhase, SelectionOptionsPayload
from app.api.schemas.filing_flow import FilingFlowGuideResponse, FilingFlowStepResponse
from app.services.filing_flow_guide_service import FilingFlowGuideService
from app.services.conversation_repository import ConversationRepository
from app.services.legal_filing_repository import LegalFilingRepository

logger = logging.getLogger(__name__)

_INTENT_OPTIONS: List[Dict[str, str]] = [
    {"code": "filing_new", "name": "Start a new case filing"},
    {"code": "filing_existing", "name": "File into an existing case"},
]


class FilingSelectionFlowError(ValueError):
    """Invalid step or selection for the API filing flow."""


class FilingSelectionFlowService:
    def __init__(
        self,
        filing_repo: LegalFilingRepository,
        conversation_repo: ConversationRepository,
        ctx: Optional[FilingOrchestratorContext] = None,
    ):
        self.filing_repo = filing_repo
        self.conversation_repo = conversation_repo
        self._ctx = ctx or FilingOrchestratorContext.create(
            filing_repo=filing_repo,
            conversation_repo=conversation_repo,
        )

    async def create_session(
        self,
        user_id: str,
        case_id: Optional[str] = None,
    ) -> FilingFlowStepResponse:
        row = await self.conversation_repo.create_conversation(user_id, case_id, None)
        if not row:
            raise RuntimeError("Failed to create conversation")
        cid = str(row.get("conversation_id") or uuid.uuid4())
        session = FilingSessionManager.create(cid, user_id)
        session.phase = FilingPhase.SELECTING_STATE
        session.selections["_api_dropdown_flow"] = True
        await persist_system_state(self.conversation_repo, session)
        return await self.get_step(cid)

    async def get_step(self, conversation_id: str) -> FilingFlowStepResponse:
        session = await self._require_session(conversation_id)
        return await self._build_step_response(session)

    async def answer_guide(
        self,
        conversation_id: str,
        question: str,
    ) -> FilingFlowGuideResponse:
        session = await self._require_session(conversation_id)
        q = str(question or "").strip()
        if not q:
            raise FilingSelectionFlowError("question is required")

        rows = await self.conversation_repo.get_history(conversation_id, limit=500)
        history = history_for_llm(session, rows)
        auth_token = await auth_token_for_user(
            self._ctx.user_repo, session.user_id
        )
        options = await options_for_response(
            self.filing_repo,
            session,
            codes_service=self._ctx.codes_service,
            bedrock=None,
            auth_token=auth_token or None,
        )
        step_inst = build_phase_selection_message(
            session.phase.value, session.selections, options
        )
        guide = FilingFlowGuideService(self._ctx.bedrock)
        reply = await guide.answer(
            session=session,
            user_message=q,
            history=history,
            step_instruction=step_inst or "",
        )
        await self.conversation_repo.insert_user_message(conversation_id, q)
        await self.conversation_repo.insert_ai_message(conversation_id, reply)
        append_chat_turn(session, q, reply)
        await persist_system_state(self.conversation_repo, session)

        step = await self._build_step_response(session)
        return FilingFlowGuideResponse(
            **step.model_dump(),
            message=reply,
            guide=reply,
        )

    async def apply_selection(
        self,
        conversation_id: str,
        code: str,
    ) -> FilingFlowStepResponse:
        session = await self._require_session(conversation_id)
        if session.selections.get("template_questions_ready"):
            raise FilingSelectionFlowError(
                "Form questions are already loaded. Continue in chat for Q&A."
            )

        picked = str(code or "").strip()
        if not picked:
            raise FilingSelectionFlowError("code is required")

        if session.phase == FilingPhase.INTENT_PENDING:
            await self._apply_intent(session, picked)
        elif session.phase in SELECTION_PHASES:
            await self._apply_dropdown_code(session, picked)
        else:
            raise FilingSelectionFlowError(
                f"Phase {session.phase.value} does not accept a dropdown selection via API."
            )

        await persist_system_state(self.conversation_repo, session)
        return await self._build_step_response(session)

    async def _require_session(self, conversation_id: str) -> FilingSession:
        conv = await self.conversation_repo.get_conversation(conversation_id)
        if not conv:
            raise FilingSelectionFlowError(f"Conversation not found: {conversation_id}")
        user_id = str(conv.get("user_id") or "")
        session = get_session(conversation_id, user_id)
        rows = await self.conversation_repo.get_history(conversation_id, limit=500)
        if rows and (
            not session.selections or session.phase == FilingPhase.GREETING
        ):
            restore_from_system_messages(rows, session)
        return session

    async def _apply_intent(self, session: FilingSession, code: str) -> None:
        intent = code.strip().lower()
        if intent not in {"filing_new", "filing_existing"}:
            raise FilingSelectionFlowError(
                "Invalid intent. Use filing_new or filing_existing."
            )
        advance_mode_from_intent(session, intent)
        if intent == "filing_new" and session.selections.get("state_code"):
            await prefetch_court_catalog(self.filing_repo, session)
        await self.conversation_repo.insert_user_message(
            session.conversation_id,
            f"[api-select:intent:{intent}]",
        )

    async def _apply_dropdown_code(self, session: FilingSession, code: str) -> None:
        auth_token = await auth_token_for_user(
            self._ctx.user_repo, session.user_id
        )
        db_options = await options_for_response(
            self.filing_repo,
            session,
            codes_service=self._ctx.codes_service,
            bedrock=None,
            auth_token=auth_token or None,
        )
        if not db_options:
            raise FilingSelectionFlowError(
                "No options are available for the current step."
            )
        cache_phase_options(session, session.phase, db_options)

        match, candidates = match_option(db_options, code)
        if not match and candidates:
            raise FilingSelectionFlowError(
                f"Ambiguous selection {code!r}. Pick one of the listed codes."
            )
        if not match:
            raise FilingSelectionFlowError(
                f"Code {code!r} is not valid for phase {session.phase.value}."
            )

        validated = filter_selections_update(
            session.phase.value,
            selection_update_for_option(session.phase.value, match),
            db_options,
        )
        if not validated:
            raise FilingSelectionFlowError(
                f"Could not apply selection {code!r} for phase {session.phase.value}."
            )

        merge_selections(session, validated)
        await self.conversation_repo.insert_user_message(
            session.conversation_id,
            f"[api-select:{session.phase.value}:{code}]",
        )

        if session.phase == FilingPhase.SELECTING_DOCUMENT_TYPE:
            hydrate_msg = await _hydrate_template_questions(self._ctx, session)
            if hydrate_msg and not session.selections.get("template_questions_ready"):
                logger.warning("Template hydrate: %s", hydrate_msg)

        advance_phase_after_selections(session)
        await apply_catalog_shortcuts(
            self.filing_repo, session, bedrock=None
        )

        # Auto-skip single-option filer / filing type (same as chat connect path).
        for _ in range(4):
            if session.selections.get("template_questions_ready"):
                break
            if session.phase not in (
                FilingPhase.SELECTING_FILER_TYPE,
                FilingPhase.SELECTING_FILING_TYPE,
            ):
                break
            next_options = await options_for_response(
                self.filing_repo,
                session,
                codes_service=self._ctx.codes_service,
                bedrock=None,
                auth_token=auth_token or None,
            )
            if len(next_options) != 1:
                break
            auto = filter_selections_update(
                session.phase.value,
                selection_update_for_option(session.phase.value, next_options[0]),
                next_options,
            )
            if not auto:
                break
            merge_selections(session, auto)
            advance_phase_after_selections(session)

    async def _build_step_response(self, session: FilingSession) -> FilingFlowStepResponse:
        auth_token = await auth_token_for_user(
            self._ctx.user_repo, session.user_id
        )
        selection_options: Optional[SelectionOptionsPayload] = None
        message = ""

        if session.selections.get("template_questions_ready"):
            message = (
                "Form questions are ready. Continue in the filing chat WebSocket "
                "to answer them."
            )
        elif session.phase == FilingPhase.INTENT_PENDING:
            selection_options = SelectionOptionsPayload(
                phase="intent_pending",
                type="dropdown",
                prompt="Choose filing intent",
                total=len(_INTENT_OPTIONS),
                options=[
                    {
                        "label": row["name"],
                        "value": row["code"],
                        "code": row["code"],
                    }
                    for row in _INTENT_OPTIONS
                ],
            )
            message = "Choose whether this is a new filing or an existing case."
        elif session.phase in SELECTION_PHASES:
            options = await options_for_response(
                self.filing_repo,
                session,
                codes_service=self._ctx.codes_service,
                bedrock=None,
                auth_token=auth_token or None,
            )
            cache_phase_options(session, session.phase, options)
            result = OrchestratorResult(
                assistant_message="",
                conversation_id=session.conversation_id,
                phase=session.phase,
                mode=session.mode,
                selections=session.selections,
                collected_answers=session.collected_answers,
            )
            result = attach_selection_options_to_result(
                result, session.phase, options
            )
            selection_options = self._payload_from_result(result)
            built = build_phase_selection_message(
                session.phase.value, session.selections, options
            )
            message = built or message
        else:
            message = f"Current phase is {session.phase.value}."

        checklist = None
        if session.checklist.items:
            from app.agents.conversation.orchestration.helpers import (
                sync_checklist_from_answers,
            )

            sync_checklist_from_answers(session)
            checklist = session.checklist.to_payload()

        return FilingFlowStepResponse(
            conversation_id=session.conversation_id,
            phase=session.phase,
            mode=session.mode,
            selections=dict(session.selections),
            selection_options=selection_options,
            template_questions_ready=bool(
                session.selections.get("template_questions_ready")
            ),
            workflow_questions_count=len(session.workflow_questions or []),
            checklist=checklist,
            message=message,
        )

    @staticmethod
    def _payload_from_result(
        result: OrchestratorResult,
    ) -> Optional[SelectionOptionsPayload]:
        raw = (result.metadata or {}).get("selection_options")
        if not raw:
            return None
        return SelectionOptionsPayload.model_validate(raw)
