"""Apply mid-session corrections from chat to collected answers and selections."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from app.agents.conversation.orchestration.state import FilingSession
from app.services.document_template_match import apply_known_case_answers

_CORRECTION_CUE_RE = re.compile(
    r"\b(change|update|correct|edit|rename|replace|instead|actually|make it)\b",
    re.I,
)
_CHANGE_TO_RE = re.compile(
    r"\b(?:change|update|correct|edit|rename|set|replace)\b"
    r"(?:\s+the)?\s+(?P<field>.+?)\s+(?:to|as|with)\s+(?P<value>.+)$",
    re.I,
)
_NO_CHILDREN_RE = re.compile(
    r"\b(without children|no children|no kids|without kids)\b", re.I
)
_WITH_CHILDREN_RE = re.compile(
    r"\b(with children|has children|have children|with kids)\b", re.I
)
_SELECTION_KEYS = (
    "case_type_name",
    "case_type_display",
    "case_type",
    "sub_case_type",
    "document_type_name",
    "doc_type",
)
_NAME_TOKENS = ("name", "full_name", "first_name", "last_name", "petitioner", "respondent")


def looks_like_detail_correction(text: str) -> bool:
    raw = str(text or "").strip()
    if not raw or raw.startswith("["):
        return False
    if _NO_CHILDREN_RE.search(raw) or _WITH_CHILDREN_RE.search(raw):
        return True
    return bool(_CORRECTION_CUE_RE.search(raw) and _CHANGE_TO_RE.search(raw))


def apply_chat_detail_corrections(session: FilingSession, user_message: str) -> List[str]:
    """Update answers/selections from a correction message. Returns changed field names."""
    raw = str(user_message or "").strip()
    if not raw or raw.startswith("["):
        return []
    changed: List[str] = []
    changed.extend(_apply_children_correction(session, raw))
    changed.extend(_apply_change_to_value(session, raw))
    if changed:
        _refresh_known_case_skips(session)
    return list(dict.fromkeys(changed))


def _apply_children_correction(session: FilingSession, raw: str) -> List[str]:
    has_no = bool(_NO_CHILDREN_RE.search(raw))
    has_with = bool(_WITH_CHILDREN_RE.search(raw)) and not has_no
    if not has_no and not has_with:
        return []
    has_children = has_with
    _rewrite_children_selections(session.selections, has_children)
    value = "yes" if has_children else "no"
    changed: List[str] = []
    for question in session.workflow_questions:
        name = str(question.get("field_name") or "")
        label = str(question.get("field_label") or name).lower()
        stem = name.lower().replace("-", "_")
        if stem.endswith("children") or "children" in label or stem == "has_children":
            session.collected_answers[name] = value
            changed.append(name)
    if not changed:
        session.collected_answers["CHILDREN"] = value
        changed.append("CHILDREN")
    return changed


def _rewrite_children_selections(selections: Dict[str, Any], has_children: bool) -> None:
    for key in _SELECTION_KEYS:
        current = str(selections.get(key) or "")
        if not current:
            continue
        if has_children:
            updated = re.sub(
                r"without children|no children",
                "with children",
                current,
                flags=re.I,
            )
        else:
            updated = re.sub(r"with children", "without children", current, flags=re.I)
            if updated == current and "children" not in current.lower():
                updated = f"{current} without children".strip()
        selections[key] = updated


def _apply_change_to_value(session: FilingSession, raw: str) -> List[str]:
    match = _CHANGE_TO_RE.search(raw)
    if not match:
        return []
    field_hint = str(match.group("field") or "").strip().strip("\"'")
    value = str(match.group("value") or "").strip().strip(" .\"'")
    if not field_hint or not value:
        return []
    if re.search(r"\b(case type|case subtype|sub.?type|children)\b", field_hint, re.I):
        return []
    targets = _match_answer_fields(session, field_hint)
    changed: List[str] = []
    for name in targets:
        session.collected_answers[name] = _value_for_field(name, value, session)
        changed.append(name)
    return changed


def _match_answer_fields(session: FilingSession, field_hint: str) -> List[str]:
    hint = re.sub(r"[^a-z0-9]+", " ", field_hint.lower()).strip()
    if not hint:
        return []
    hint_tokens = set(hint.split())
    scored: List[tuple[int, str]] = []
    for question in session.workflow_questions:
        name = str(question.get("field_name") or "").strip()
        if not name:
            continue
        label = str(question.get("field_label") or name)
        blob = re.sub(r"[^a-z0-9]+", " ", f"{name} {label}".lower())
        tokens = set(blob.split())
        overlap = len(hint_tokens & tokens)
        if overlap:
            scored.append((overlap, name))
            continue
        if hint in blob or any(token in blob for token in hint_tokens if len(token) > 2):
            scored.append((1, name))
    if scored:
        scored.sort(key=lambda item: (-item[0], item[1]))
        best = scored[0][0]
        return [name for score, name in scored if score == best]
    if any(token in _NAME_TOKENS for token in hint_tokens) or hint in {"name", "the name"}:
        names = [
            str(q.get("field_name") or "")
            for q in session.workflow_questions
            if "name" in str(q.get("field_name") or "").lower()
            or "name" in str(q.get("field_label") or "").lower()
        ]
        answered = [name for name in names if session.collected_answers.get(name) not in (None, "", [], {})]
        return answered or [name for name in names if name]
    return []


def _value_for_field(field_name: str, value: str, session: FilingSession) -> str:
    stem = field_name.lower()
    if "last_name" in stem and " " in value:
        return value.split()[-1]
    if "first_name" in stem and " " in value:
        return value.split()[0]
    current = session.collected_answers.get(field_name)
    if (
        isinstance(current, str)
        and " " in current
        and " " not in value
        and "name" in stem
        and "last" not in stem
        and "first" not in stem
    ):
        parts = current.split()
        parts[0] = value
        return " ".join(parts)
    return value


def _refresh_known_case_skips(session: FilingSession) -> None:
    if not session.workflow_questions:
        return
    known, skipped = apply_known_case_answers(
        session.workflow_questions, session.selections
    )
    for key, value in known.items():
        if session.collected_answers.get(key) in (None, "", [], {}):
            session.collected_answers[key] = value
    skip_children = any(
        str(session.collected_answers.get(name) or "").strip().lower() in {"no", "false", "0"}
        for name in session.collected_answers
        if "children" in name.lower()
    )
    if skip_children:
        for name in skipped:
            session.collected_answers.pop(name, None)


def correction_summary(changed: List[str], session: FilingSession) -> Optional[str]:
    if not changed:
        return None
    parts = []
    for name in changed:
        value = session.collected_answers.get(name)
        if value in (None, "", [], {}):
            continue
        parts.append(f"{name} is now {value}")
    if not parts:
        return "I updated your details."
    return "Updated: " + "; ".join(parts) + "."
