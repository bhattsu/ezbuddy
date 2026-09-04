"""Offline tests for jurisdiction sync helpers using real API fixtures."""

from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from sync_jurisdiction_api_data import (  # noqa: E402
    build_court_tree,
    build_jurisdiction_data,
    compute_changes,
    courts_matching_case_type,
    extract_items,
    extract_link,
    parse_case_type_item,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "jurisdiction_sync"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_extract_link_case_category_codes():
    data = _load("01_jurisdiction_codes_tx.json")
    court = data["items"][0]
    url = extract_link(court, "case_category_codes")
    assert url is not None
    assert "case_category_codes" in url
    assert "coa:10" in url or "request_id=" in url


def test_extract_link_case_type_codes():
    data = _load("03_case_category_codes_coa10.json")
    category = data["items"][0]
    url = extract_link(category, "case_type_codes")
    assert url is not None
    assert "case_type_codes" in url


def test_parse_case_type_includes_follow_up_urls():
    data = _load("05_case_type_codes_employment.json")
    parsed = parse_case_type_item(data["items"][0])
    assert parsed["name"] == "Other Employment"
    assert parsed["party_type_codes_url"] is not None
    assert parsed["filing_codes_url"] is not None
    assert parsed["case_subtype_codes_url"] is not None


def test_build_court_tree_coa10():
    jurisdictions = _load("01_jurisdiction_codes_tx.json")
    categories = _load("03_case_category_codes_coa10.json")
    employment_types = _load("05_case_type_codes_employment.json")
    court_item = jurisdictions["items"][0]
    type_url = extract_link(categories["items"][0], "case_type_codes")
    tree = build_court_tree(
        court_item,
        categories,
        {type_url: employment_types},
    )
    assert tree["code"] == "coa:10"
    assert tree["name"] == "10th Court of Appeals"
    assert len(tree["categories"]) == 3
    employment = tree["categories"][0]
    assert employment["name"] == "Civil - Employment"
    assert len(employment["case_types"]) == 2
    assert employment["case_types"][0]["name"] == "Other Employment"


def test_build_jurisdiction_data_two_courts():
    j1 = _load("01_jurisdiction_codes_tx.json")
    categories_coa10 = _load("03_case_category_codes_coa10.json")
    employment_types = _load("05_case_type_codes_employment.json")
    type_url = extract_link(categories_coa10["items"][0], "case_type_codes")

    court10 = build_court_tree(
        j1["items"][0],
        categories_coa10,
        {type_url: employment_types},
    )
    court11 = {
        "code": "coa:11",
        "name": "11th Court of Appeals",
        "categories": [],
    }
    data = build_jurisdiction_data("TX", "Texas", [court10, court11])
    assert data["state_code"] == "TX"
    assert len(data["courts"]) == 2
    assert data["stats"]["court_count"] == 2
    assert data["stats"]["category_count"] == 3
    assert data["stats"]["case_type_count"] == 2


def test_divorce_courts_from_fixtures():
    jurisdictions = _load("01_jurisdiction_codes_tx.json")
    family_categories = _load("04_case_category_codes_coa10_family.json")
    divorce_no_kids = _load("06_case_type_codes_divorce_no_children.json")
    divorce_with_kids = _load("07_case_type_codes_divorce_with_children.json")
    type_url = extract_link(family_categories["items"][0], "case_type_codes")
    combined_types = {
        "items": divorce_no_kids["items"] + divorce_with_kids["items"],
        "count": 2,
    }
    court_tree = build_court_tree(
        jurisdictions["items"][0],
        family_categories,
        {type_url: combined_types},
    )
    jurisdiction_data = build_jurisdiction_data("TX", "Texas", [court_tree])
    matches = courts_matching_case_type(jurisdiction_data, "divorce")
    assert len(matches) == 1
    assert matches[0]["code"] == "coa:10"
    assert len(matches[0]["matched_case_types"]) == 2
    names = {t["name"] for t in matches[0]["matched_case_types"]}
    assert "Divorce No Children" in names
    assert "Divorce with Children" in names


def test_compute_changes_no_op():
    data = build_jurisdiction_data("TX", "Texas", [{"code": "coa:10", "name": "X", "categories": []}])
    assert compute_changes(data, deepcopy(data)) == {}


def test_compute_changes_added_case_type():
    old = build_jurisdiction_data(
        "TX",
        "Texas",
        [
            {
                "code": "coa:10",
                "name": "10th Court of Appeals",
                "categories": [
                    {
                        "code": "131251",
                        "name": "Civil - Employment",
                        "case_types": [
                            {"code": "352660", "name": "Other Employment"},
                        ],
                    }
                ],
            }
        ],
    )
    new = deepcopy(old)
    new["courts"][0]["categories"][0]["case_types"].append(
        {"code": "352661", "name": "Workers' Compensation"}
    )
    changes = compute_changes(old, new)
    added = changes["categories"]["case_types"]["added"]
    assert len(added) == 1
    assert added[0]["case_type_code"] == "352661"


def test_empty_jurisdiction_items():
    empty = _load("10_jurisdiction_codes_empty_items.json")
    assert extract_items(empty) == []
    data = build_jurisdiction_data("TX", "Texas", [])
    assert data["stats"]["court_count"] == 0
    assert courts_matching_case_type(data, "divorce") == []


def test_stats_counts():
    jurisdictions = _load("01_jurisdiction_codes_tx.json")
    categories = _load("03_case_category_codes_coa10.json")
    employment_types = _load("05_case_type_codes_employment.json")
    type_url = extract_link(categories["items"][0], "case_type_codes")
    court_tree = build_court_tree(
        jurisdictions["items"][0],
        categories,
        {type_url: employment_types},
    )
    data = build_jurisdiction_data("TX", "Texas", [court_tree])
    assert data["stats"]["court_count"] == 1
    assert data["stats"]["category_count"] == 3
    assert data["stats"]["case_type_count"] == 2
