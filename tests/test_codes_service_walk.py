"""Tests for :meth:`USLegalProCodesService.walk_case_type_chain`."""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from app.services.uslegalpro_codes_service import (
    CodeBundle,
    USLegalProCodesService,
    parse_filing_type_response,
)


# ---------------------------------------------------------------------------
# parse_filing_type_response


def test_parse_filing_type_response_dict_shape() -> None:
    rows = parse_filing_type_response(
        {"message_code": 0, "item": {"EFile": "EFile"}}
    )
    assert rows == [{"code": "EFile", "name": "EFile"}]


def test_parse_filing_type_response_multiple_options() -> None:
    rows = parse_filing_type_response(
        {"item": {"EFile": "EFile", "EFileAndServe": "EFile & Serve"}}
    )
    assert {r["code"] for r in rows} == {"EFile", "EFileAndServe"}
    assert {r["name"] for r in rows} == {"EFile", "EFile & Serve"}


def test_parse_filing_type_response_list_shape() -> None:
    rows = parse_filing_type_response(
        {"items": [{"code": "EFile", "name": "EFile"}]}
    )
    assert rows == [{"code": "EFile", "name": "EFile"}]


def test_parse_filing_type_response_empty() -> None:
    assert parse_filing_type_response({}) == []
    assert parse_filing_type_response(None) == []


# ---------------------------------------------------------------------------
# walk_case_type_chain against a stub client


class _StubClient:
    """Records requested URLs and returns canned responses."""

    def __init__(self, responses: Dict[str, Any]):
        self.responses = responses
        self.calls: List[str] = []

    async def get_json(self, path: str, params: Dict[str, Any] | None = None):
        # Reassemble the URL the way ``USLegalProCodesService`` would.
        if params:
            qs = "&".join(f"{k}={v}" for k, v in params.items())
            key = f"{path}?{qs}" if qs else path
        else:
            key = path
        self.calls.append(key)
        if key in self.responses:
            return self.responses[key]
        # Fall back to matching by path prefix so callers only need the
        # discriminating fragment.
        for pattern, value in self.responses.items():
            if pattern in key:
                return value
        raise AssertionError(f"unexpected URL: {key}")


def _jurisdiction_response() -> Dict[str, Any]:
    return {
        "items": [
            {
                "code": "harris:dc",
                "name": "Harris DC",
                "link": {
                    "case_category_codes": {
                        "link": "https://api/case_category_codes?request_id=cat"
                    }
                },
            }
        ]
    }


def _category_response() -> Dict[str, Any]:
    return {
        "items": [
            {
                "code": "131370",
                "name": "Family",
                "link": {
                    "case_type_codes": {
                        "link": "https://api/case_type_codes?request_id=type"
                    }
                },
            }
        ]
    }


def _case_type_response() -> Dict[str, Any]:
    return {
        "items": [
            {
                "code": "209421",
                "name": "Divorce No Children",
                "link": {
                    "filer_type_codes": {"link": "https://api/filer_type_codes?rid=ft"},
                    "filing_type": {"link": "https://api/filing_type?rid=ft2"},
                    "filing_codes": {"link": "https://api/filing_codes?rid=fc"},
                    "party_type_codes": {
                        "link": "https://api/party_type_codes?rid=pt"
                    },
                    "case_subtype_codes": {
                        "link": "https://api/case_subtype_codes?rid=cst"
                    },
                    "name_suffix_codes": {
                        "link": "https://api/name_suffix_codes?rid=ns"
                    },
                },
            }
        ]
    }


def _filing_codes_response() -> Dict[str, Any]:
    return {
        "items": [
            {
                "code": "209523",
                "name": "Petition",
                "link": {
                    "document_type_codes": {
                        "link": "https://api/document_type_codes?rid=dt-a"
                    },
                    "optional_service_codes": {
                        "link": "https://api/optional_service_codes?rid=os-a"
                    },
                },
            },
            {
                "code": "209524",
                "name": "Exhibit",
                "link": {
                    "document_type_codes": {
                        "link": "https://api/document_type_codes?rid=dt-b"
                    },
                },
            },
        ]
    }


def _responses(with_optional_services: bool = False) -> Dict[str, Any]:
    return {
        # jurisdiction_codes (built from ``get_jurisdictions``)
        "/v2/tx/code/jurisdiction_codes": _jurisdiction_response(),
        "https://api/case_category_codes": _category_response(),
        "https://api/case_type_codes": _case_type_response(),
        "https://api/filer_type_codes": {
            "items": [{"code": "54325", "name": "Attorney"}]
        },
        "https://api/filing_type": {
            "message_code": 0,
            "item": {"EFile": "EFile"},
        },
        "https://api/filing_codes": _filing_codes_response(),
        "https://api/party_type_codes": {
            "items": [
                {"code": "53024", "name": "Petitioner", "is_required": "True"},
                {"code": "271011", "name": "Respondent", "is_required": "True"},
            ]
        },
        "https://api/case_subtype_codes": {"items": []},
        "https://api/name_suffix_codes": {
            "items": [{"code": "JR", "name": "Jr."}]
        },
        "https://api/document_type_codes?rid=dt-a": {
            "items": [{"code": "53689", "name": "Petition-Divorce"}]
        },
        "https://api/document_type_codes?rid=dt-b": {
            "items": [{"code": "53690", "name": "Exhibit A"}]
        },
        "https://api/optional_service_codes?rid=os-a": {
            "items": [{"code": "OPT1", "name": "Optional 1"}]
        },
    }


@pytest.mark.asyncio
async def test_walk_case_type_chain_returns_full_bundle() -> None:
    stub = _StubClient(_responses())
    service = USLegalProCodesService(api_client=stub)  # type: ignore[arg-type]
    bundle = await service.walk_case_type_chain(
        "tx",
        "harris:dc",
        "131370",
        "209421",
        filing_codes=["209523", "209524"],
    )
    assert isinstance(bundle, CodeBundle)
    assert bundle.state == "tx"
    assert bundle.jurisdiction["code"] == "harris:dc"
    assert bundle.case_category["code"] == "131370"
    assert bundle.case_type["code"] == "209421"
    assert [r["code"] for r in bundle.filer_types] == ["54325"]
    assert [r["code"] for r in bundle.filing_type_options] == ["EFile"]
    assert {r["code"] for r in bundle.filing_codes} == {"209523", "209524"}
    assert {r["code"] for r in bundle.party_types} == {"53024", "271011"}
    assert bundle.party_types[0]["is_required"] is True
    # multi-filing-code branch
    assert set(bundle.document_types_by_filing_code) == {"209523", "209524"}
    assert bundle.document_types_by_filing_code["209523"][0]["code"] == "53689"
    assert bundle.document_types_by_filing_code["209524"][0]["code"] == "53690"


@pytest.mark.asyncio
async def test_walk_case_type_chain_does_not_fetch_optional_services_by_default() -> None:
    stub = _StubClient(_responses())
    service = USLegalProCodesService(api_client=stub)  # type: ignore[arg-type]
    bundle = await service.walk_case_type_chain(
        "tx",
        "harris:dc",
        "131370",
        "209421",
        filing_codes=["209523"],
    )
    assert bundle.optional_services_by_filing_code == {}
    assert not any("optional_service_codes" in url for url in stub.calls)


@pytest.mark.asyncio
async def test_walk_case_type_chain_fetches_optional_services_when_asked() -> None:
    stub = _StubClient(_responses())
    service = USLegalProCodesService(api_client=stub)  # type: ignore[arg-type]
    bundle = await service.walk_case_type_chain(
        "tx",
        "harris:dc",
        "131370",
        "209421",
        filing_codes=["209523"],
        fetch_optional_services=True,
    )
    assert set(bundle.optional_services_by_filing_code) == {"209523"}
    assert bundle.optional_services_by_filing_code["209523"][0]["code"] == "OPT1"


@pytest.mark.asyncio
async def test_walk_case_type_chain_missing_case_type_returns_empty() -> None:
    stub = _StubClient(_responses())
    service = USLegalProCodesService(api_client=stub)  # type: ignore[arg-type]
    bundle = await service.walk_case_type_chain(
        "tx",
        "harris:dc",
        "131370",
        "does-not-exist",
    )
    assert bundle.case_type == {}
    assert bundle.filer_types == []
    assert bundle.filing_codes == []


@pytest.mark.asyncio
async def test_walk_case_type_chain_unknown_filing_code_records_empty_doc_types() -> None:
    stub = _StubClient(_responses())
    service = USLegalProCodesService(api_client=stub)  # type: ignore[arg-type]
    bundle = await service.walk_case_type_chain(
        "tx",
        "harris:dc",
        "131370",
        "209421",
        filing_codes=["999999"],
    )
    # ``999999`` is not in the returned filing_codes, so the map should still
    # contain the key so the validator can flag it.
    assert bundle.document_types_by_filing_code.get("999999") == []


def test_code_bundle_to_from_serializable_roundtrip() -> None:
    bundle = CodeBundle(
        state="tx",
        jurisdiction={"code": "harris:dc", "name": "Harris", "links": {"x": 1}, "raw": {"y": 2}},
        case_category={"code": "131370"},
        case_type={"code": "209421"},
        filer_types=[{"code": "54325", "name": "Attorney"}],
        filing_type_options=[{"code": "EFile", "name": "EFile"}],
        filing_codes=[{"code": "209523", "name": "Petition"}],
        party_types=[{"code": "53024", "name": "Petitioner", "is_required": True}],
        document_types_by_filing_code={"209523": [{"code": "53689", "name": "Petition-Divorce"}]},
    )
    round_trip = CodeBundle.from_serializable(bundle.to_serializable())
    assert round_trip.state == "tx"
    assert round_trip.jurisdiction["code"] == "harris:dc"
    # Non-JSON fields are stripped.
    assert "links" not in round_trip.jurisdiction
    assert "raw" not in round_trip.jurisdiction
    assert round_trip.filing_codes[0]["code"] == "209523"
    assert round_trip.document_types_by_filing_code["209523"][0]["code"] == "53689"
    assert round_trip.party_types[0]["is_required"] is True
