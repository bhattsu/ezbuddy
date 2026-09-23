import pytest

from app.services.uslegalpro_codes_service import USLegalProCodesService


class _FakeClient:
    def __init__(self, responses):
        self._responses = responses
        self.calls = []

    async def get_json(self, path, params=None):
        self.calls.append((path, dict(params or {})))
        key = (path, tuple(sorted((params or {}).items())))
        return self._responses.get(key) or self._responses.get(path)


@pytest.mark.asyncio
async def test_get_jurisdictions_uses_tyler_initial_query():
    client = _FakeClient(
        {
            (
                "/v2/tx/code/jurisdiction_codes",
                (("court_system", "tyler"), ("is_initial", "true")),
            ): {
                "items": [
                    {
                        "code": "harris:dc",
                        "name": "Harris District Clerk",
                        "link": {
                            "case_category_codes": {
                                "link": "/v2/tx/code/case_category_codes?request_id=abc"
                            }
                        },
                    }
                ]
            }
        }
    )
    service = USLegalProCodesService(api_client=client)
    rows = await service.get_jurisdictions("TX")
    assert len(rows) == 1
    assert rows[0]["code"] == "harris:dc"
    assert "case_category_codes_url" in rows[0]
    assert client.calls[0][1]["court_system"] == "tyler"
    assert client.calls[0][1]["is_initial"] == "true"


@pytest.mark.asyncio
async def test_resolve_case_type_url_walks_live_links():
    client = _FakeClient(
        {
            (
                "/v2/tx/code/jurisdiction_codes",
                (("court_system", "tyler"), ("is_initial", "true")),
            ): {
                "items": [
                    {
                        "code": "court1",
                        "name": "Court 1",
                        "link": {
                            "case_category_codes": {
                                "link": "/v2/tx/code/case_category_codes?request_id=j1"
                            }
                        },
                    }
                ]
            },
            (
                "/v2/tx/code/case_category_codes",
                (("request_id", "j1"),),
            ): {
                "items": [
                    {
                        "code": "cat1",
                        "name": "Category 1",
                        "link": {
                            "case_type_codes": {
                                "link": "/v2/tx/code/case_type_codes?request_id=c1"
                            }
                        },
                    }
                ]
            },
        }
    )
    service = USLegalProCodesService(api_client=client)
    url = await service.resolve_case_type_codes_url(
        {
            "state_code": "TX",
            "jurisdiction_code": "court1",
            "case_category_code": "cat1",
        }
    )
    assert url.endswith("request_id=c1")
