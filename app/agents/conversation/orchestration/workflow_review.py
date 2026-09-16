"""Review collected workflow answers before document generation."""

from __future__ import annotations

import re
from typing import Any, Dict, List

from app.agents.conversation.orchestration.helpers import (
    is_affirmative_reply,
    question_visible,
)
from app.agents.conversation.orchestration.state import FilingSession
from app.api.schemas.filing_events import FilingPhase

_PROCEED_RE = re.compile(
    r"\b("
    r"yes|y|ok|okay|confirm|confirmed|proceed|continue|go ahead|looks good|all good|"
    r"no changes|that'?s? correct|correct|generate(?: the document)?|"
    r"generate documents|ready to generate"
    r")\b",
    re.I,
)
_NEGATIVE_RE = re.compile(r"^(?:no|n)\.?$", re.I)
_MAX_REVIEW_LINES = 40


def begin_workflow_review(session: FilingSession) -> None:
    session.phase = FilingPhase.CONFIRMING_WORKFLOW_ANSWERS


def looks_like_proceed_to_generation(text: str) -> bool:
    raw = str(text or "").strip()
    if not raw or raw.startswith("["):
        return False
    if _NEGATIVE_RE.match(raw):
        return False
    if is_affirmative_reply(raw):
        return True
    return bool(_PROCEED_RE.search(raw))


def looks_like_review_decline(text: str) -> bool:
    return bool(_NEGATIVE_RE.match(str(text or "").strip()))


def _answer_lines(session: FilingSession) -> List[str]:
    questions_by_field = {
        str(q.get("field_name") or ""): q for q in session.workflow_questions
    }
    lines: List[str] = []
    sorted_items = sorted(
        session.checklist.items,
        key=lambda item: (getattr(item, "sort_order", 0), getattr(item, "field_name", "")),
    )
    for item in sorted_items:
        field_name = str(getattr(item, "field_name", "") or "").strip()
        if not field_name:
            continue
        if getattr(item, "status", "pending") == "skipped":
            continue
        question = questions_by_field.get(field_name) or {
            "field_name": field_name,
            "field_label": getattr(item, "label", field_name),
        }
        if not question_visible(question, session.collected_answers):
            continue
        value = session.collected_answers.get(field_name)
        if value in (None, "", [], {}):
            continue
        label = str(
            question.get("field_label") or getattr(item, "label", "") or field_name
        ).strip()
        lines.append(f"- {label}: {_format_answer_value(value)}")
    if not lines:
        for question in session.workflow_questions:
            field_name = str(question.get("field_name") or "").strip()
            if not field_name:
                continue
            if not question_visible(question, session.collected_answers):
                continue
            value = session.collected_answers.get(field_name)
            if value in (None, "", [], {}):
                continue
            label = str(question.get("field_label") or field_name).strip()
            lines.append(f"- {label}: {_format_answer_value(value)}")
    return lines


def _format_answer_value(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return ", ".join(str(item) for item in value if str(item).strip())
    if isinstance(value, dict):
        return ", ".join(f"{key}={val}" for key, val in value.items())
    return str(value).strip()


def format_workflow_review_message(
    session: FilingSession,
    *,
    intro: str = "",
) -> str:
    lines = _answer_lines(session)
    if len(lines) > _MAX_REVIEW_LINES:
        hidden = len(lines) - _MAX_REVIEW_LINES
        lines = lines[:_MAX_REVIEW_LINES] + [f"- ... and {hidden} more field(s)"]
    summary = "\n".join(lines) if lines else "- (No answers recorded yet.)"
    prefix = f"{intro.strip()} " if intro.strip() else ""
    return (
        f"{prefix}I have collected the following details for your document:\n\n"
        f"{summary}\n\n"
        "Please review the information above. If anything needs to be changed, tell me "
        'what to update (for example, "change the phone number to 555-0100", or say '
        "you want to change the court or case type). When everything looks correct, "
        "reply yes to generate your document."
    ).strip()


def review_decline_message() -> str:
    return (
        "What would you like to change? You can update any answer "
        '(for example, "change the address to 123 Main St"), or say you want to '
        "change the court, case category, or case type."
    )
