from app.services.filing_flow_guide_service import (
    classify_intake_user_message,
    looks_like_flow_help,
    looks_like_generic_legal_question,
)


def test_looks_like_flow_help_detects_wizard_questions():
    assert looks_like_flow_help("What should I do now?")
    assert looks_like_flow_help("How do I change the court?")
    assert looks_like_flow_help("How can I apply divorce")
    assert looks_like_flow_help("what to select , guide me")


def test_looks_like_flow_help_ignores_short_selection():
    assert not looks_like_flow_help("TX")
    assert not looks_like_flow_help("[session_start]")
    assert not looks_like_flow_help("ha")


def test_classify_intake_flow_vs_generic():
    assert classify_intake_user_message("How can I apply divorce") == "flow_guide"
    assert classify_intake_user_message("what to select , guide me") == "flow_guide"
    assert (
        classify_intake_user_message(
            "What is the waiting period for divorce in Texas?"
        )
        == "generic_legal"
    )
    assert classify_intake_user_message("Harris District Clerk") == "selection"
    assert classify_intake_user_message("ha") == "selection"


def test_generic_legal_heuristic():
    assert looks_like_generic_legal_question(
        "What is the residency requirement before filing for divorce?"
    )
    assert not looks_like_generic_legal_question("How can I apply divorce")
