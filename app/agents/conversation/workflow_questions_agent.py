"""Workflow Q&A LLM agent with checklist-aware output."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from app.adapters.llm.bedrock import Bedrock, get_bedrock
from app.agents.conversation.schemas.filing_llm_schemas import WorkflowQuestionOutput
from app.agents.utils.json_utils import parse_llm_json
from app.agents.utils.text_sanitize import sanitize_assistant_text
from app.core.prompts.context import format_llm_prompt
from app.core.prompts.workflow_questions import WORKFLOW_QUESTIONS_PROMPT

logger = logging.getLogger(__name__)


class WorkflowQuestionsAgent:
    """LLM agent for collecting workflow form answers."""

    def __init__(self, bedrock: Optional[Bedrock] = None):
        self.bedrock = bedrock or get_bedrock()

    @staticmethod
    def build_prompt(
        *,
        selections: Dict[str, Any],
        workflow_questions: List[Dict[str, Any]],
        checklist: Dict[str, Any],
        collected_answers: Dict[str, Any],
        pending_questions_batch: List[Dict[str, Any]],
        next_pending_question: Optional[Dict[str, Any]],
        history: List[Dict[str, str]],
        user_message: str,
    ) -> str:
        primary = next_pending_question or (
            pending_questions_batch[0] if pending_questions_batch else {}
        )
        return format_llm_prompt(
            WORKFLOW_QUESTIONS_PROMPT,
            selections_json=json.dumps(selections, default=str),
            pending_questions_batch_json=json.dumps(
                pending_questions_batch, default=str
            ),
            next_pending_question_json=json.dumps(primary, default=str),
            collected_answers_json=json.dumps(collected_answers, default=str),
            checklist_json=json.dumps(checklist, default=str),
            history_text=WorkflowQuestionsAgent._format_history(history),
            user_message=user_message or "(empty)",
        )

    @staticmethod
    def normalize_output(
        parsed: Dict[str, Any],
        raw: Any = None,
        bedrock: Optional[Bedrock] = None,
    ) -> Dict[str, Any]:
        if not parsed.get("assistant_message") and bedrock is not None and raw is not None:
            text = bedrock.extract_text_field(raw, "assistant_message", "text")
            parsed["assistant_message"] = text or "Please provide the requested information."
        parsed.setdefault("assistant_message", "Please provide the requested information.")
        parsed.setdefault("answers_update", {})
        parsed.setdefault("checklist_updates", [])
        parsed.setdefault("skipped_fields", [])
        parsed.setdefault("workflow_complete", False)
        if parsed.get("assistant_message"):
            parsed["assistant_message"] = sanitize_assistant_text(parsed["assistant_message"])
        return parsed

    @staticmethod
    def _format_history(history: List[Dict[str, str]]) -> str:
        from app.agents.conversation.orchestration.chat_context import (
            format_request_response_history,
        )

        return format_request_response_history(history)

    async def _invoke_structured(self, prompt: str) -> Dict[str, Any]:
        try:
            result = await self.bedrock.invoke_structured_prompt(
                prompt, WorkflowQuestionOutput
            )
            data = result.model_dump()
            if data.get("assistant_message"):
                data["assistant_message"] = sanitize_assistant_text(
                    data["assistant_message"]
                )
            return data
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Structured workflow invoke failed, falling back to prompt body: %s",
                exc,
            )
            body = Bedrock.build_text_prompt_body(prompt)
            raw = await self.bedrock.invoke_prompt_with_timeout(body)
            parsed = parse_llm_json(raw)
            return self.normalize_output(parsed, raw, self.bedrock)

    async def run(
        self,
        *,
        selections: Dict[str, Any],
        workflow_questions: List[Dict[str, Any]],
        checklist: Dict[str, Any],
        collected_answers: Dict[str, Any],
        pending_questions_batch: Optional[List[Dict[str, Any]]] = None,
        next_pending_question: Optional[Dict[str, Any]] = None,
        history: List[Dict[str, str]],
        user_message: str,
    ) -> Dict[str, Any]:
        batch = pending_questions_batch or (
            [next_pending_question] if next_pending_question else []
        )
        prompt = self.build_prompt(
            selections=selections,
            workflow_questions=workflow_questions,
            checklist=checklist,
            collected_answers=collected_answers,
            pending_questions_batch=batch,
            next_pending_question=next_pending_question or (batch[0] if batch else None),
            history=history,
            user_message=user_message,
        )
        try:
            return await self._invoke_structured(prompt)
        except Exception as exc:  # noqa: BLE001
            logger.error("WorkflowQuestionsAgent LLM failed: %s", exc)
            return {
                "assistant_message": sanitize_assistant_text(
                    "A technical issue occurred. Please repeat your last answer."
                ),
                "answers_update": {},
                "checklist_updates": [],
                "skipped_fields": [],
                "workflow_complete": False,
            }
