"""Tests for db_options allow-list formatting."""

from app.agents.utils.db_options_format import (
    build_phase_selection_message,
    build_selection_options_payload,
    filter_selections_update,
    format_db_options_summary,
    match_option,
    selection_update_for_option,
)


def test_match_option_accepts_dropdown_state_label():
    options = [{"state_code": "TX", "state_name": "Texas"}]
    match, candidates = match_option(options, "Texas (TX)")
    assert candidates == []
    assert match == options[0]
    validated = filter_selections_update(
        "selecting_state",
        selection_update_for_option("selecting_state", match),
        options,
    )
    assert validated["state_code"] == "TX"
    assert validated["state_name"] == "Texas"


def test_format_states_summary():
    options = [
        {"state_code": "TX", "state_name": "Texas"},
        {"state_code": "CA", "state_name": "California"},
    ]
    summary = format_db_options_summary("selecting_state", options)
    assert "Texas (TX)" in summary
    assert "California (CA)" in summary
    assert "ONLY" in summary


def test_format_empty_options():
    summary = format_db_options_summary("selecting_state", [])
    assert "do NOT" in summary


def test_filter_selections_state_valid():
    options = [{"state_code": "TX", "state_name": "Texas"}]
    result = filter_selections_update(
        "selecting_state", {"state_code": "TX"}, options
    )
    assert result["state_code"] == "TX"
    assert result["state_name"] == "Texas"


def test_build_county_message_after_state():
    options = [
        {"county_name": "Harris"},
        {"county_name": "Dallas"},
    ]
    msg = build_phase_selection_message(
        "selecting_county",
        {"state_name": "Texas", "state_code": "TX"},
        options,
    )
    assert msg is not None
    assert "dropdown" in msg.lower()
    assert "Harris" not in msg
    assert "Dallas" not in msg


def test_build_jurisdiction_message_uses_dropdown_prompt():
    options = [{"name": f"Court {idx}", "code": f"c{idx}"} for idx in range(50)]
    msg = build_phase_selection_message(
        "selecting_jurisdiction",
        {"state_name": "Texas", "state_code": "TX"},
        options,
    )
    assert msg is not None
    assert "dropdown" in msg.lower()
    assert "Court 0" not in msg


def test_build_selection_options_payload():
    options = [
        {"name": "10th Court of Appeals", "code": "tx:appeals10"},
        {"name": "11th Court of Appeals", "code": "tx:appeals11"},
    ]
    payload = build_selection_options_payload("selecting_jurisdiction", options)
    assert payload is not None
    assert payload["type"] == "dropdown"
    assert payload["total"] == 2
    assert payload["options"][0]["label"] == "10th Court of Appeals"
    assert payload["options"][0]["value"] == "10th Court of Appeals"
    assert payload["options"][0]["code"] == "tx:appeals10"


def test_build_selection_options_payload_large_list():
    options = [{"name": f"Court {idx}", "code": f"c{idx}"} for idx in range(914)]
    payload = build_selection_options_payload("selecting_jurisdiction", options)
    assert payload is not None
    assert payload["total"] == 914
    assert len(payload["options"]) == 914


def test_build_county_message_empty():
    msg = build_phase_selection_message(
        "selecting_county",
        {"state_name": "Texas", "state_code": "TX"},
        [],
    )
    assert msg is not None
    assert "no counties" in msg.lower()


def test_existing_jurisdiction_uses_dropdown():
    options = [
        {"name": "Refugio County - District Clerk", "code": "refugio:dc"},
        {"name": "Travis County - District Clerk", "code": "travis:dc"},
    ]
    msg = build_phase_selection_message(
        "existing_selecting_jurisdiction",
        {"state_name": "Texas", "state_code": "TX"},
        options,
    )
    assert msg is not None
    assert "dropdown" in msg.lower()
    assert "jurisdiction code" not in msg.lower()


def test_filter_existing_jurisdiction_uses_code_from_name():
    options = [
        {"name": "Refugio County - District Clerk", "code": "refugio:dc"},
    ]
    result = filter_selections_update(
        "existing_selecting_jurisdiction",
        {"jurisdiction_name": "Refugio County - District Clerk"},
        options,
    )
    assert result["jurisdiction_code"] == "refugio:dc"
    assert result["jurisdiction_name"] == "Refugio County - District Clerk"


def test_filter_selections_state_invalid():
    options = [{"state_code": "TX", "state_name": "Texas"}]
    result = filter_selections_update(
        "selecting_state", {"state_code": "NY"}, options
    )
    assert result == {}


def test_filter_case_type_keeps_party_url_from_catalog():
    options = [
        {
            "code": "div1",
            "name": "Divorce No Children",
            "party_type_codes_url": "https://example/parties/div",
            "case_subtype_codes_url": "https://example/subtypes/div",
            "case_category_code": "fam1",
            "case_category_name": "Family",
        }
    ]
    result = filter_selections_update(
        "selecting_case_type",
        {"case_type_name": "Divorce No Children"},
        options,
    )
    assert result["case_type_code"] == "div1"
    assert result["party_type_codes_url"] == "https://example/parties/div"
    assert result["case_subtype_codes_url"] == "https://example/subtypes/div"
    assert result["case_category_code"] == "fam1"


def test_filter_document_type_caches_field_mapping():
    options = [
        {
            "code": "PETITION_NO_CHILDREN",
            "name": "Original Petition for Divorce Without Children",
            "doc_type": "PETITION_NO_CHILDREN",
            "template_id": "abc",
            "field_mapping": '_FULL_NAME=join(" ", PLAINTIFF_1_FIRST_NAME, PLAINTIFF_1_LAST_NAME)',
        }
    ]
    result = filter_selections_update(
        "selecting_document_type",
        {"document_type_name": "Original Petition for Divorce Without Children"},
        options,
    )
    assert result["template_id"] == "abc"
    assert result["field_mapping"].startswith("_FULL_NAME=")
