"""Sanitize assistant-facing text (professional tone, no emojis)."""

from __future__ import annotations

import re

# Common emoji / symbol ranges used in chat model output
_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001FAFF"
    "\U00002700-\U000027BF"
    "\U0001F600-\U0001F64F"
    "\U0001F680-\U0001F6FF"
    "\U00002600-\U000026FF"
    "\U000024C2-\U0001F251"
    "]+",
    flags=re.UNICODE,
)


def strip_emojis(text: str) -> str:
    if not text:
        return text
    return _EMOJI_RE.sub("", text)


def sanitize_assistant_text(text: str) -> str:
    """Professional plain-language output for court filing chat."""
    cleaned = strip_emojis(text or "")
    cleaned = cleaned.replace("**", "")
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    return cleaned.strip()
