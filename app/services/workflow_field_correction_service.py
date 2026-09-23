"""LLM-backed mapping of review corrections to a single workflow field."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.adapters.llm.bedrock import Bedrock, get_bedrock
from app.agents.conversation.orchestration.state import FilingSession
from app.core.prompts.context import format_llm_prompt
from app.core.prompts.workflow_field_correction import WORKFLOW_FIELD_CORRECTION_PROMPT
from app.services.document_template_match import (
    filing_user_party_side,
    is_full_person_name_field,
    party_side_for_field,
)

logger = logging.getLogger(__name__)


class WorkflowFieldCorrectionOutput(BaseModel):
    intent: str = "unclear"
    field_names: List[str] = Field(default_factory=list)
    value: Optional[str] = None


def _normalize_correction_message(text: str) -> str:
    import re

    raw = str(text or "").strip()
    raw = re.sub(r"\bchange\s+me\s+name\b", "change my name", raw, flags=re.I)
    raw = re.sub(r"\bupdate\s+me\s+name\b", "update my name", raw, flags=re.I)
    raw = re.sub(r"\bchange\s+me\s+to\b", "change my name to", raw, flags=re.I)
    return raw


def _field_catalog(session: FilingSession) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    for question in session.workflow_questions or []:
        name = str(question.get("field_name") or "").strip()
        if not name:
            continue
        label = str(
            question.get("field_label") or question.get("question") or name
        ).strip()
        rows.append({"field_name": name, "question": label})
    return rows


def _validate_pick(session: FilingSession, field_name: str, user_message: str) -> bool:
    import re

    message = _normalize_correction_message(user_message).lower()
    question = next(
        (
            q
            for q in (session.workflow_questions or [])
            if str(q.get("field_name") or "") == field_name
        ),
        None,
    )
    if not question:
        return False
    label = str(question.get("field_label") or question.get("question") or "")
    if re.search(r"\b(my name|me name)\b", message):
        side = filing_user_party_side(session.selections)
        if not is_full_person_name_field(field_name, label):
            return False
        return party_side_for_field(field_name, label) == side
    if re.search(r"\bdefendant\b|\brespondent\b", message):
        return party_side_for_field(field_name, label) == "defendant" and is_full_person_name_field(
            field_name, label
        )
    if re.search(r"\bplaintiff\b|\bpetitioner\b", message):
        return party_side_for_field(field_name, label) == "plaintiff" and is_full_person_name_field(
            field_name, label
        )
    return True


async def apply_llm_field_correction(
    session: FilingSession,
    user_message: str,
    *,
    bedrock: Optional[Bedrock] = None,
) -> List[str]:
    """Return updated field_name list (0 or 1 item)."""
    raw = _normalize_correction_message(user_message)
    if not raw or not session.workflow_questions:
        return []

    llm = bedrock or get_bedrock()
    fields = _field_catalog(session)
    if not fields:
        return []

    prompt = format_llm_prompt(
        WORKFLOW_FIELD_CORRECTION_PROMPT,
        fields_json=json.dumps(fields, indent=2),
        answers_json=json.dumps(session.collected_answers or {}, default=str),
        user_message=raw[:2000],
    )
    try:
        parsed = await llm.invoke_structured_prompt(prompt, WorkflowFieldCorrectionOutput)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Workflow field correction LLM failed: %s", exc)
        return []

    if parsed.intent != "update" or not parsed.field_names or not parsed.value:
        return []

    known = {row["field_name"] for row in fields}
    picks = [name for name in parsed.field_names if name in known]
    if not picks:
        return []
    field_name = picks[0]
    if len(picks) > 1:
        logger.info("Workflow correction LLM returned multiple fields; using first only")
    if not _validate_pick(session, field_name, raw):
        return []

    session.collected_answers[field_name] = str(parsed.value).strip()
    return [field_name]
