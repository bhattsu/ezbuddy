"""Bedrock provider package — use ``app.adapters.llm.bedrock`` for all LLM calls."""

from app.adapters.llm.bedrock import Bedrock, get_bedrock

__all__ = ["Bedrock", "get_bedrock"]
