"""Tests for LLM-based court catalog filtering."""

import asyncio

import pytest

from app.services.court_catalog import (
    flatten_jurisdiction_data,
    infer_case_topic,
    summarize_courts_from_payload,
    summarize_courts_from_rows,
)
from app.services.court_catalog_filter_service import (
    CourtCatalogFilterService,
    format_court_summary_line,
    is_retryable_llm_error,
    keyword_match_court_codes,
    retry_async,
)


SAMPLE = {
    "state_code": "TX",
    "courts": [
        {
            "code": "harris:dc",
            "name": "Harris County - District Clerk",
            "categories": [
                {
                    "code": "fam1",
                    "name": "Family",
                    "case_types": [
                        {"code": "div1", "name": "Divorce No Children"},
                        {"code": "prot1", "name": "Protective Orders: No Divorce"},
                    ],
                }
            ],
        },
        {
            "code": "travis:dc",
            "name": "Travis County - District Clerk",
            "categories": [
                {
                    "code": "fam1",
                    "name": "Family",
                    "case_types": [
                        {"code": "div3", "name": "Divorce With Children"},
                    ],
                }
            ],
        },
        {
            "code": "bexar:dc",
            "name": "Bexar County - District Clerk",
            "categories": [
                {
                    "code": "cr1",
                    "name": "Criminal",
                    "case_types": [
                        {"code": "mis1", "name": "Misdemeanor"},
                    ],
                }
            ],
        },
    ],
}


def test_infer_case_topic_strips_need():
    assert infer_case_topic("I need to file a case for divorce") == "divorce"


def test_summarize_courts_from_payload():
    summaries = summarize_courts_from_payload(SAMPLE)
    by_code = {court["code"]: court for court in summaries}
    assert set(by_code) == {"harris:dc", "travis:dc", "bexar:dc"}
    assert "Divorce No Children" in by_code["harris:dc"]["case_types"]
    assert "Divorce With Children" in by_code["travis:dc"]["case_types"]
    assert "Misdemeanor" in by_code["bexar:dc"]["case_types"]


def test_summarize_courts_from_rows_matches_payload():
    rows = flatten_jurisdiction_data(SAMPLE)
    assert summarize_courts_from_rows(rows) == summarize_courts_from_payload(SAMPLE)


def test_format_court_summary_line():
    line = format_court_summary_line(
        {
            "code": "harris:dc",
            "name": "Harris County - District Clerk",
            "case_types": ["Divorce No Children"],
        }
    )
    assert line.startswith("harris:dc | Harris County - District Clerk |")


def test_keyword_match_court_codes():
    rows = flatten_jurisdiction_data(SAMPLE)
    codes = keyword_match_court_codes(rows, "divorce")
    assert set(codes) == {"harris:dc", "travis:dc"}


def test_is_retryable_llm_error_detects_throttling():
    assert is_retryable_llm_error(RuntimeError("ThrottlingException: rate exceeded"))
    assert is_retryable_llm_error(TimeoutError())
    assert not is_retryable_llm_error(ValueError("invalid json"))


@pytest.mark.asyncio
async def test_retry_async_recovers_from_throttling():
    attempts = 0

    async def _flaky():
        nonlocal attempts
        attempts += 1
        if attempts < 2:
            raise RuntimeError("ThrottlingException: Too many requests")
        return ["harris:dc"]

    result = await retry_async("test op", _flaky, max_attempts=3, base_delay_sec=0.01)
    assert result == ["harris:dc"]
    assert attempts == 2


@pytest.mark.asyncio
async def test_match_court_codes_retries_throttled_batch():
    rows = flatten_jurisdiction_data(SAMPLE)
    attempts = 0

    class _Bedrock:
        async def invoke_structured_prompt(self, prompt, schema):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("ThrottlingException: Too many requests")
            return schema(matched_court_codes=["harris:dc", "travis:dc"], reason="ok")

        async def invoke_prompt_with_timeout(self, body):
            return {}

    service = CourtCatalogFilterService(bedrock=_Bedrock())
    codes = await service.match_court_codes(
        rows,
        case_topic="divorce",
        summary="divorce",
        search_phrases=["divorce"],
        state_code="TX",
    )
    assert set(codes) == {"harris:dc", "travis:dc"}
    assert attempts == 2


@pytest.mark.asyncio
async def test_extract_case_intent_llm():
    class _Bedrock:
        async def invoke_structured_prompt(self, prompt, schema):
            return schema(
                case_topic="divorce",
                summary="User wants to file for divorce",
                search_phrases=["divorce", "dissolution"],
            )

        async def invoke_prompt_with_timeout(self, body):
            return {}

    service = CourtCatalogFilterService(bedrock=_Bedrock())
    intent = await service.extract_case_intent("I need to file a case for divorce")
    assert intent.case_topic == "divorce"
    assert "divorce" in intent.search_phrases


@pytest.mark.asyncio
async def test_match_court_codes_llm_returns_supported_courts():
    rows = flatten_jurisdiction_data(SAMPLE)

    class _Bedrock:
        async def invoke_structured_prompt(self, prompt, schema):
            return schema(
                matched_court_codes=["harris:dc", "travis:dc"],
                reason="these courts offer divorce case types",
            )

        async def invoke_prompt_with_timeout(self, body):
            return {}

    service = CourtCatalogFilterService(bedrock=_Bedrock())
    codes = await service.match_court_codes(
        rows,
        case_topic="divorce",
        summary="User wants divorce",
        search_phrases=["divorce"],
        state_code="TX",
    )
    assert set(codes) == {"harris:dc", "travis:dc"}


@pytest.mark.asyncio
async def test_match_court_codes_runs_batches_in_parallel():
    rows = flatten_jurisdiction_data(SAMPLE)
    expanded_rows = rows * 3
    in_flight = 0
    max_in_flight = 0

    class _Bedrock:
        async def invoke_structured_prompt(self, prompt, schema):
            nonlocal in_flight, max_in_flight
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
            await asyncio.sleep(0.05)
            in_flight -= 1
            if "harris:dc" in prompt:
                return schema(matched_court_codes=["harris:dc"], reason="ok")
            return schema(matched_court_codes=[], reason="none")

        async def invoke_prompt_with_timeout(self, body):
            return {}

    service = CourtCatalogFilterService(bedrock=_Bedrock())
    original_summarize = summarize_courts_from_rows

    def _many_courts(_rows):
        courts = original_summarize(_rows)
        return courts + [
            {**court, "code": f"{court['code']}-{idx}"}
            for idx in range(2)
            for court in courts
        ]

    import app.services.court_catalog_filter_service as mod

    mod.COURTS_PER_LLM_BATCH = 2
    mod.MAX_CONCURRENT_COURT_MATCH_BATCHES = 2
    mod.summarize_courts_from_rows = _many_courts
    try:
        codes = await service.match_court_codes(
            expanded_rows,
            case_topic="divorce",
            summary="divorce",
            search_phrases=["divorce"],
            state_code="TX",
        )
    finally:
        mod.COURTS_PER_LLM_BATCH = 150
        mod.MAX_CONCURRENT_COURT_MATCH_BATCHES = 3
        mod.summarize_courts_from_rows = original_summarize

    assert "harris:dc" in codes
    assert max_in_flight > 1


@pytest.mark.asyncio
async def test_filter_catalog_rows_skips_llm_when_court_selected():
    rows = flatten_jurisdiction_data(SAMPLE)
    selections = {
        "case_topic": "divorce",
        "jurisdiction_code": "harris:dc",
    }

    class _Bedrock:
        async def invoke_structured_prompt(self, prompt, schema):
            raise AssertionError("LLM should not run after jurisdiction is selected")

        async def invoke_prompt_with_timeout(self, body):
            raise AssertionError("LLM should not run after jurisdiction is selected")

    service = CourtCatalogFilterService(bedrock=_Bedrock())
    filtered = await service.filter_catalog_rows(rows, selections)
    assert {row["case_type_name"] for row in filtered} == {"Divorce No Children"}


@pytest.mark.asyncio
async def test_filter_catalog_rows_returns_only_matching_courts():
    rows = flatten_jurisdiction_data(SAMPLE)
    selections = {
        "case_topic": "divorce",
        "case_intent": {
            "case_topic": "divorce",
            "summary": "divorce",
            "search_phrases": ["divorce"],
        },
        "matched_court_codes": ["harris:dc", "travis:dc"],
        "state_code": "TX",
    }
    service = CourtCatalogFilterService(bedrock=None)
    filtered = await service.filter_catalog_rows(rows, selections)
    courts = {row["jurisdiction_code"] for row in filtered}
    assert courts == {"harris:dc", "travis:dc"}
    assert "bexar:dc" not in courts
    assert all("divorce" in row["case_type_name"].lower() for row in filtered)
