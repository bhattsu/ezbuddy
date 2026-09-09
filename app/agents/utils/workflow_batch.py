"""Group consecutive workflow questions into batches for fewer turns."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

BATCHABLE_TYPES = frozenset({"TEXT", "BOOLEAN", "DATE", "EMAIL", "PHONE", "NUMBER"})
DEFAULT_MAX_BATCH = 3
ALL_PENDING_BATCH = 10_000


def _question_visible(question: Dict[str, Any], answers: Dict[str, Any]) -> bool:
    from app.services.field_mapping_service import is_mapping_question_visible

    return is_mapping_question_visible(question, answers)


def batch_pending_questions(
    *,
    checklist_items: List[Any],
    workflow_questions: List[Dict[str, Any]],
    collected_answers: Dict[str, Any],
    max_batch: int = DEFAULT_MAX_BATCH,
    same_type_only: bool = True,
) -> List[Dict[str, Any]]:
    """
    Return up to ``max_batch`` consecutive pending questions that can be asked together.

    Batches short factual fields of the same question_type (name, date, yes/no, etc.).
    """
    if max_batch < 1:
        max_batch = 1

    questions_by_field = {q["field_name"]: q for q in workflow_questions}
    answered = set(collected_answers.keys())
    pending: List[Dict[str, Any]] = []

    sorted_items = sorted(
        checklist_items,
        key=lambda i: (getattr(i, "sort_order", 0), getattr(i, "field_name", "")),
    )

    for item in sorted_items:
        if getattr(item, "status", "pending") == "skipped":
            continue
        field_name = getattr(item, "field_name", None)
        if not field_name or field_name in answered:
            continue
        if getattr(item, "status", "pending") != "pending":
            continue

        q = questions_by_field.get(field_name) or {
            "field_name": field_name,
            "field_label": getattr(item, "label", field_name),
            "question_type": "TEXT",
            "required": getattr(item, "required", True),
            "sort_order": getattr(item, "sort_order", 0),
        }

        if not _question_visible(q, collected_answers):
            continue

        if not pending:
            pending.append(q)
            continue

        if len(pending) >= max_batch:
            break

        if not same_type_only:
            pending.append(q)
            continue

        prev = pending[-1]
        prev_type = (prev.get("question_type") or "TEXT").upper()
        curr_type = (q.get("question_type") or "TEXT").upper()

        if prev_type == curr_type and curr_type in BATCHABLE_TYPES:
            pending.append(q)
        else:
            break

    return pending


def list_all_pending_questions(
    *,
    checklist_items: List[Any],
    workflow_questions: List[Dict[str, Any]],
    collected_answers: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Return every visible unanswered question, in checklist order."""
    return batch_pending_questions(
        checklist_items=checklist_items,
        workflow_questions=workflow_questions,
        collected_answers=collected_answers,
        max_batch=ALL_PENDING_BATCH,
        same_type_only=False,
    )


def format_questions_for_user(questions: List[Dict[str, Any]]) -> str:
    """Numbered plain-text list the user can answer in one reply."""
    lines: List[str] = []
    for index, question in enumerate(questions, start=1):
        label = str(
            question.get("field_label") or question.get("field_name") or ""
        ).strip()
        if label:
            lines.append(f"{index}. {label}")
    return "\n".join(lines)


def format_pending_questions_message(
    *,
    checklist_items: List[Any],
    workflow_questions: List[Dict[str, Any]],
    collected_answers: Dict[str, Any],
    intro: str = "",
) -> str:
    pending = list_all_pending_questions(
        checklist_items=checklist_items,
        workflow_questions=workflow_questions,
        collected_answers=collected_answers,
    )
    body = format_questions_for_user(pending)
    prefix = (intro or "").strip()
    if prefix and body:
        return f"{prefix}\n\n{body}"
    return prefix or body


def compact_form_questions(questions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Small field list for the conversational form-fill agent."""
    compact: List[Dict[str, Any]] = []
    for question in questions:
        name = str(question.get("field_name") or "").strip()
        if not name:
            continue
        row: Dict[str, Any] = {
            "field_name": name,
            "question": question.get("field_label") or question.get("question") or name,
            "pdf_field": question.get("pdf_field") or question.get("field"),
        }
        cluster_fields = question.get("cluster_fields")
        if cluster_fields:
            row["cluster_fields"] = cluster_fields
        compact.append(row)
    return compact


def format_next_form_question_message(
    intro: str, question: Optional[Dict[str, Any]]
) -> str:
    """Conversational intro plus the single next form question."""
    prefix = (intro or "").strip()
    label = ""
    if question:
        label = str(
            question.get("field_label") or question.get("question") or question.get("field_name") or ""
        ).strip()
    if prefix and label:
        return f"{prefix}\n\n{label}"
    return prefix or label
