"""Tests for shared LLM prompt date context."""

from __future__ import annotations

from datetime import date

from app.core.prompts.context import format_llm_prompt, get_current_date_context


def test_get_current_date_context():
    ctx = get_current_date_context(on_date=date(2026, 8, 30))
    assert ctx["current_date"] == "2026-08-30"
    assert ctx["current_date_long"] == "August 30, 2026"
    assert "August 30, 2026" in ctx["current_date_line"]


def test_format_llm_prompt_injects_date_when_missing():
    prompt = format_llm_prompt("Hello {name}", name="world", on_date=date(2026, 8, 30))
    assert "August 30, 2026" in prompt
    assert "Hello world" in prompt
    assert "English only" in prompt


def test_format_llm_prompt_respects_existing_date_placeholder():
    prompt = format_llm_prompt(
        "Today is {current_date_long}. Task: {task}",
        task="file",
        on_date=date(2025, 2, 28),
    )
    assert "February 28, 2025" in prompt
    assert "Task: file" in prompt
    assert prompt.count("Today's date") == 0
