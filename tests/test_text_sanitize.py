"""Tests for assistant text sanitization."""

from app.agents.utils.text_sanitize import sanitize_assistant_text, strip_emojis


def test_strip_emojis():
    assert strip_emojis("Hello 👋 World") == "Hello  World"
    assert "🚀" not in strip_emojis("Let's go 🚀")


def test_sanitize_removes_markdown_bold():
    text = sanitize_assistant_text("Welcome to **US Legal Pro**!")
    assert "**" not in text
    assert "US Legal Pro" in text
