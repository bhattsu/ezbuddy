"""LLM routing for post-state intake (new / existing / status / legal question)."""

from __future__ import annotations

import logging
from typing import Dict, List, Literal, Optional

from app.adapters.llm.bedrock import Bedrock, get_bedrock
from app.agents.utils.db_options_format import match_option
from app.agents.conversation.schemas.filing_llm_schemas import (
    NavigationIntentClassificationOutput,
)
from app.core.prompts.context import format_llm_prompt
from app.core.prompts.filing_assistant import NAVIGATION_INTENT_CLASSIFICATION_PROMPT

logger = logging.getLogger(__name__)

NavigationIntentKind = Literal[
    "filing_new", "filing_existing", "generic_legal", "check_status"
]

_STRICT_INTENT_OPTIONS: List[Dict[str, str]] = [
    {"code": "filing_new", "name": "Start a new case filing"},
    {"code": "filing_existing", "name": "File into an existing case"},
    {"code": "filing_new", "name": "File a new court case"},
    {"code": "filing_existing", "name": "Look up an existing court case"},
    {"code": "generic_legal", "name": "Answer a general legal question"},
    {"code": "check_status", "name": "Check the case status"},
]

_VALID_ROUTES = frozenset(
    {"filing_new", "filing_existing", "generic_legal", "check_status", "unclear"}
)


def match_navigation_intent_from_exact_text(
    user_message: str,
) -> Optional[NavigationIntentKind]:
    """Match dropdown labels/codes only (no pattern matching)."""
    text = str(user_message or "").strip()
    if not text or text.startswith("["):
        return None
    match, _ = match_option(_STRICT_INTENT_OPTIONS, text)
    if not match:
        return None
    code = str(match.get("code") or "").strip().lower()
    if code in {"filing_new", "filing_existing", "generic_legal", "check_status"}:
        return code  # type: ignore[return-value]
    return None


async def resolve_navigation_intent(
    user_message: str,
    *,
    bedrock: Optional[Bedrock] = None,
) -> Optional[NavigationIntentKind]:
    exact = match_navigation_intent_from_exact_text(user_message)
    if exact:
        return exact
    return await classify_navigation_intent_with_llm(user_message, bedrock=bedrock)


async def classify_navigation_intent_with_llm(
    user_message: str,
    *,
    bedrock: Optional[Bedrock] = None,
) -> Optional[NavigationIntentKind]:
    """Map free text to a filing flow using a short structured LLM call."""
    text = str(user_message or "").strip()
    if not text or text.startswith("["):
        return None

    llm = bedrock or get_bedrock()
    prompt = format_llm_prompt(
        NAVIGATION_INTENT_CLASSIFICATION_PROMPT,
        user_message=text[:2000],
    )
    try:
        parsed = await llm.invoke_structured_prompt(
            prompt, NavigationIntentClassificationOutput
        )
        route = str(parsed.intent or "unclear").strip().lower()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Navigation intent LLM failed: %s", exc)
        return None

    if route not in _VALID_ROUTES or route == "unclear":
        return None
    return route  # type: ignore[return-value]
