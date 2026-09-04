"""LLM-based case intent extraction and court catalog filtering."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional, Sequence

from pydantic import BaseModel, Field

from app.adapters.llm.bedrock import Bedrock, get_bedrock
from app.agents.utils.json_utils import parse_llm_json
from app.core.prompts.context import format_llm_prompt
from app.core.prompts.court_catalog_filter import (
    CASE_INTENT_EXTRACTION_PROMPT,
    COURT_MATCH_PROMPT,
)
from app.services.court_catalog import (
    infer_case_topic,
    search_catalog_rows,
    summarize_courts_from_rows,
)

logger = logging.getLogger(__name__)

COURTS_PER_LLM_BATCH = 150
MAX_CASE_TYPES_PER_COURT = 50
MAX_CONCURRENT_COURT_MATCH_BATCHES = 3
COURT_MATCH_MAX_RETRIES = 3
COURT_MATCH_RETRY_BASE_DELAY_SEC = 1.5

_RETRYABLE_LLM_MARKERS = (
    "throttl",
    "too many requests",
    "429",
    "rate exceeded",
    "service unavailable",
    "503",
    "timeout",
    "timed out",
    "connection reset",
    "capacity",
    "modelstreamerrorexception",
    "internal server error",
    "500",
)


def is_retryable_llm_error(exc: BaseException) -> bool:
    """True for Bedrock/network errors worth retrying (e.g. throttling)."""
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError, ConnectionError, OSError)):
        return True
    text = str(exc).lower()
    exc_name = type(exc).__name__.lower()
    return any(marker in text or marker in exc_name for marker in _RETRYABLE_LLM_MARKERS)


async def retry_async(
    operation: str,
    fn,
    *,
    max_attempts: int = COURT_MATCH_MAX_RETRIES,
    base_delay_sec: float = COURT_MATCH_RETRY_BASE_DELAY_SEC,
):
    """Run an async callable with exponential backoff on retryable failures."""
    last_exc: Optional[BaseException] = None
    for attempt in range(1, max_attempts + 1):
        try:
            return await fn()
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt >= max_attempts or not is_retryable_llm_error(exc):
                raise
            delay = base_delay_sec * (2 ** (attempt - 1))
            logger.warning(
                "%s retryable failure (attempt %d/%d); sleeping %.1fs: %s",
                operation,
                attempt,
                max_attempts,
                delay,
                exc,
            )
            await asyncio.sleep(delay)
    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"{operation} failed without exception")


class CaseIntentOutput(BaseModel):
    case_topic: str = ""
    summary: str = ""
    search_phrases: List[str] = Field(default_factory=list)


class CourtMatchOutput(BaseModel):
    matched_court_codes: List[str] = Field(default_factory=list)
    reason: str = ""


def format_court_summary_line(court: Dict[str, Any]) -> str:
    """One court per line for the LLM prompt."""
    case_types = list(court.get("case_types") or [])
    if len(case_types) > MAX_CASE_TYPES_PER_COURT:
        shown = case_types[:MAX_CASE_TYPES_PER_COURT]
        types_text = "; ".join(shown) + "; ..."
    else:
        types_text = "; ".join(case_types)
    return f"{court['code']} | {court['name']} | {types_text}"


def keyword_match_court_codes(
    rows: List[Dict[str, str]], topic: str
) -> List[str]:
    """Deterministic fallback: courts that offer case types matching the topic."""
    return sorted(
        {
            str(row.get("jurisdiction_code") or "").strip()
            for row in search_catalog_rows(rows, topic)
            if row.get("jurisdiction_code")
        }
    )


class CourtCatalogFilterService:
    """Extract filing intent and filter courts via LLM using court name + case types."""

    def __init__(self, bedrock: Optional[Bedrock] = None):
        self.bedrock = bedrock or get_bedrock()

    async def extract_case_intent(
        self,
        user_message: str,
        selections: Optional[Dict[str, Any]] = None,
        history: Optional[Sequence[Dict[str, str]]] = None,
    ) -> CaseIntentOutput:
        message = str(user_message or "").strip()
        if not message:
            return CaseIntentOutput()
        if self.bedrock is None:
            topic = infer_case_topic(message)
            return CaseIntentOutput(
                case_topic=topic,
                summary=message,
                search_phrases=[topic] if topic else [],
            )

        prompt = format_llm_prompt(
            CASE_INTENT_EXTRACTION_PROMPT,
            user_message=message[:4000],
            selections_json=json.dumps(selections or {}, default=str)[:4000],
            history_text=self._format_history(history or []),
        )
        try:
            parsed = await self.bedrock.invoke_structured_prompt(prompt, CaseIntentOutput)
            return parsed
        except Exception as exc:  # noqa: BLE001
            logger.warning("Case intent structured invoke failed: %s", exc)
        try:
            body = {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 1024,
                "temperature": 0,
                "messages": [{"role": "user", "content": prompt}],
            }
            raw = await self.bedrock.invoke_prompt_with_timeout(body)
            parsed = parse_llm_json(raw)
            return CaseIntentOutput.model_validate(parsed)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Case intent fallback failed: %s", exc)
            topic = infer_case_topic(message)
            return CaseIntentOutput(
                case_topic=topic,
                summary=message,
                search_phrases=[topic] if topic else [],
            )

    async def match_court_codes(
        self,
        rows: List[Dict[str, str]],
        *,
        case_topic: str,
        summary: str = "",
        search_phrases: Optional[List[str]] = None,
        state_code: str = "",
    ) -> List[str]:
        """Return court codes whose offered case types match the user's intent."""
        topic = str(case_topic or "").strip()
        if not rows or not topic:
            return []

        summaries = summarize_courts_from_rows(rows)
        if not summaries:
            return []

        if self.bedrock is None:
            return keyword_match_court_codes(rows, topic)

        allowed = {court["code"] for court in summaries}
        batches = [
            summaries[start : start + COURTS_PER_LLM_BATCH]
            for start in range(0, len(summaries), COURTS_PER_LLM_BATCH)
        ]
        sem = asyncio.Semaphore(max(1, MAX_CONCURRENT_COURT_MATCH_BATCHES))

        async def _match_batch(
            batch: List[Dict[str, Any]], batch_index: int
        ) -> List[str]:
            court_lines = "\n".join(
                format_court_summary_line(court) for court in batch
            )
            prompt = format_llm_prompt(
                COURT_MATCH_PROMPT,
                case_topic=topic,
                summary=(summary or topic)[:1000],
                search_phrases=json.dumps(search_phrases or [topic]),
                state_code=state_code or "the selected state",
                court_lines=court_lines,
            )
            async with sem:
                return await retry_async(
                    f"Court match batch {batch_index + 1}/{len(batches)}",
                    lambda: self._invoke_court_match_once(prompt, allowed),
                )

        batch_results = await asyncio.gather(
            *[
                _match_batch(batch, index)
                for index, batch in enumerate(batches)
            ],
            return_exceptions=True,
        )
        matched: set[str] = set()
        for index, result in enumerate(batch_results):
            if isinstance(result, BaseException):
                logger.warning(
                    "Court match batch %d/%d failed after retries: %s",
                    index + 1,
                    len(batches),
                    result,
                )
                continue
            matched.update(result)

        if matched:
            logger.info(
                "LLM matched %d courts for topic=%r in %s "
                "(%d batches, max %d concurrent)",
                len(matched),
                topic,
                state_code or "state",
                len(batches),
                MAX_CONCURRENT_COURT_MATCH_BATCHES,
            )
            return sorted(matched)

        logger.info(
            "LLM returned no courts for topic=%r; using keyword fallback", topic
        )
        return keyword_match_court_codes(rows, topic)

    async def _invoke_court_match_once(
        self, prompt: str, allowed: set[str]
    ) -> List[str]:
        try:
            parsed = await self.bedrock.invoke_structured_prompt(prompt, CourtMatchOutput)
            return [
                code
                for code in (parsed.matched_court_codes or [])
                if str(code).strip() in allowed
            ]
        except Exception as exc:  # noqa: BLE001
            if is_retryable_llm_error(exc):
                raise
            logger.warning("Court match structured invoke failed: %s", exc)
        try:
            body = {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 4096,
                "temperature": 0,
                "messages": [{"role": "user", "content": prompt}],
            }
            raw = await self.bedrock.invoke_prompt_with_timeout(body)
            parsed = parse_llm_json(raw)
            return [
                str(code).strip()
                for code in (parsed.get("matched_court_codes") or [])
                if str(code).strip() in allowed
            ]
        except Exception as exc:  # noqa: BLE001
            if is_retryable_llm_error(exc):
                raise
            logger.warning("Court match fallback failed: %s", exc)
        return []

    async def filter_catalog_rows(
        self,
        rows: List[Dict[str, str]],
        selections: Dict[str, Any],
    ) -> List[Dict[str, str]]:
        j_code = str(selections.get("jurisdiction_code") or "").strip()
        if j_code:
            from app.services.court_catalog import filter_court

            court_rows = filter_court(rows, j_code)
            topic = str(selections.get("case_topic") or "").strip()
            if topic:
                return search_catalog_rows(court_rows, topic)
            return court_rows

        topic = str(selections.get("case_topic") or "").strip()
        if not topic:
            return rows

        intent = selections.get("case_intent") or {}
        summary = str(intent.get("summary") or topic)
        search_phrases = intent.get("search_phrases") or [topic]
        state_code = str(selections.get("state_code") or "")

        matched_courts = selections.get("matched_court_codes")
        if not isinstance(matched_courts, list):
            matched_courts = await self.match_court_codes(
                rows,
                case_topic=topic,
                summary=summary,
                search_phrases=search_phrases,
                state_code=state_code,
            )
            selections["matched_court_codes"] = matched_courts

        if not matched_courts:
            return []

        court_set = {str(code).strip() for code in matched_courts if str(code).strip()}
        court_rows = [
            row
            for row in rows
            if str(row.get("jurisdiction_code") or "").strip() in court_set
        ]
        return search_catalog_rows(court_rows, topic)

    @staticmethod
    def _format_history(history: Sequence[Dict[str, str]]) -> str:
        if not history:
            return "(none)"
        lines = []
        for msg in history[-8:]:
            role = msg.get("role", "user")
            content = str(msg.get("content") or "").strip()
            if content:
                lines.append(f"{role.upper()}: {content[:500]}")
        return "\n".join(lines) or "(none)"
