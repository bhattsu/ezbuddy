"""Navigation and intent LLM agent for the filing assistant."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from app.adapters.llm.bedrock import Bedrock, get_bedrock
from app.agents.conversation.schemas.filing_llm_schemas import FilingNavigationOutput
from app.agents.utils.db_options_format import (
    compact_db_options,
    format_db_options_summary,
)
from app.agents.utils.json_utils import parse_llm_json
from app.agents.utils.text_sanitize import sanitize_assistant_text
from app.core.prompts.context import format_llm_prompt
from app.core.prompts.filing_assistant import FILING_ASSISTANT_PROMPT

logger = logging.getLogger(__name__)


class FilingAssistantAgent:
    """LLM agent for greeting, intent, and navigation phases."""

    def __init__(self, bedrock: Optional[Bedrock] = None):
        self.bedrock = bedrock or get_bedrock()

    @staticmethod
    def build_prompt(
        *,
        mode: str,
        phase: str,
        selections: Dict[str, Any],
        db_options: List[Dict[str, Any]],
        history: List[Dict[str, str]],
        user_message: str,
    ) -> str:
        return format_llm_prompt(
            FILING_ASSISTANT_PROMPT,
            mode=mode,
            phase=phase,
            selections_json=json.dumps(selections, default=str),
            db_options_json=json.dumps(compact_db_options(db_options), default=str),
            options_summary=format_db_options_summary(phase, db_options),
            history_text=FilingAssistantAgent._format_history(history),
            user_message=user_message or "(empty)",
        )

    @staticmethod
    def normalize_output(
        parsed: Dict[str, Any],
        raw: Any = None,
        bedrock: Optional[Bedrock] = None,
    ) -> Dict[str, Any]:
        if not parsed.get("assistant_message") and bedrock is not None and raw is not None:
            text = bedrock.extract_text_field(raw, "assistant_message", "text", "content")
            parsed["assistant_message"] = text or (
                "Welcome to US Legal Pro. I can help you file a new case, "
                "continue an existing case, or answer brief legal questions. How may I assist you?"
            )
        parsed.setdefault(
            "assistant_message",
            "Welcome to US Legal Pro. I can help you file a new case, "
            "continue an existing case, or answer brief legal questions. How may I assist you?",
        )
        parsed.setdefault("intent", "continue")
        parsed.setdefault("selections_update", {})
        parsed.setdefault("lookup_action", None)
        parsed.setdefault("lookup_params", {})
        parsed.setdefault("phase_complete", False)
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
                prompt, FilingNavigationOutput
            )
            data = result.model_dump()
            if data.get("assistant_message"):
                data["assistant_message"] = sanitize_assistant_text(
                    data["assistant_message"]
                )
            return data
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Structured navigation invoke failed, falling back to prompt body: %s",
                exc,
            )
            body = Bedrock.build_text_prompt_body(prompt)
            raw = await self.bedrock.invoke_prompt_with_timeout(body)
            parsed = parse_llm_json(raw)
            return self.normalize_output(parsed, raw, self.bedrock)

    async def run(
        self,
        *,
        mode: str,
        phase: str,
        selections: Dict[str, Any],
        db_options: List[Dict[str, Any]],
        history: List[Dict[str, str]],
        user_message: str,
    ) -> Dict[str, Any]:
        prompt = self.build_prompt(
            mode=mode,
            phase=phase,
            selections=selections,
            db_options=db_options,
            history=history,
            user_message=user_message,
        )
        try:
            return await self._invoke_structured(prompt)
        except Exception as exc:  # noqa: BLE001
            logger.error("FilingAssistantAgent LLM failed: %s", exc)
            return {
                "intent": "continue",
                "assistant_message": sanitize_assistant_text(
                    "A technical issue occurred. Please try again in a moment."
                ),
                "selections_update": {},
                "lookup_action": None,
                "lookup_params": {},
                "phase_complete": False,
            }
