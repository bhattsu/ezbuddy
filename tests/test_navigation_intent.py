import pytest

from app.agents.conversation.orchestration.helpers import (
    advance_mode_from_intent,
    classify_navigation_intent_from_text,
)
from app.agents.conversation.orchestration.state import FilingSession
from app.agents.conversation.schemas.filing_llm_schemas import (
    NavigationIntentClassificationOutput,
)
from app.api.schemas.filing_events import FilingMode, FilingPhase
from app.services.filing_flow_guide_service import classify_intake_user_message
from app.services.navigation_intent_service import (
    classify_navigation_intent_with_llm,
    resolve_navigation_intent,
)


def test_classify_navigation_intent_exact_dropdown_labels():
    assert classify_navigation_intent_from_text("File a new court case") == "filing_new"
    assert (
        classify_navigation_intent_from_text("look up an existing court case")
        == "filing_existing"
    )
    assert classify_navigation_intent_from_text("check my case status") is None


def test_classify_navigation_intent_free_text_requires_llm():
    assert classify_navigation_intent_from_text("I need to file for divorce") is None


@pytest.mark.asyncio
async def test_resolve_navigation_intent_uses_llm_for_free_text():
    class _MockBedrock:
        async def invoke_structured_prompt(self, prompt, schema):
            assert schema is NavigationIntentClassificationOutput
            return NavigationIntentClassificationOutput(intent="filing_new")

    result = await resolve_navigation_intent(
        "I need to file for divorce", bedrock=_MockBedrock()
    )
    assert result == "filing_new"


@pytest.mark.asyncio
async def test_classify_navigation_intent_with_llm_routes():
    class _MockBedrock:
        async def invoke_structured_prompt(self, prompt, schema):
            return NavigationIntentClassificationOutput(intent="check_status")

    result = await classify_navigation_intent_with_llm(
        "where is my envelope", bedrock=_MockBedrock()
    )
    assert result == "check_status"


def test_intent_pending_free_text_not_treated_as_flow_guide():
    assert (
        classify_intake_user_message(
            "file a new case",
            phase=FilingPhase.INTENT_PENDING,
            navigation_intent="filing_new",
        )
        == "selection"
    )
    assert (
        classify_intake_user_message(
            "How can I apply divorce",
            phase=FilingPhase.INTENT_PENDING,
            navigation_intent=None,
        )
        == "flow_guide"
    )
    assert (
        classify_intake_user_message(
            "I want to file for divorce",
            phase=FilingPhase.INTENT_PENDING,
            navigation_intent="filing_new",
        )
        == "selection"
    )


def test_advance_mode_from_intent_new_case_after_state():
    session = FilingSession(
        conversation_id="c1",
        user_id="u1",
        mode=FilingMode.UNSET,
        phase=FilingPhase.INTENT_PENDING,
        selections={"state_code": "tx", "state_name": "Texas"},
    )
    advance_mode_from_intent(session, "filing_new")
    assert session.mode == FilingMode.FILING_NEW
    assert session.phase == FilingPhase.SELECTING_JURISDICTION
