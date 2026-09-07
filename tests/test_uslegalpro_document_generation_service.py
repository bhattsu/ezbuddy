"""Tests for US Legal Pro generate_documents request/response handling."""

import pytest

from app.services.uslegalpro_document_generation_service import (
    GENERATED_PDF_NAME,
    USLegalProDocumentGenerationService,
    build_generate_documents_request,
    extract_document_url,
)


def test_build_generate_documents_request():
    envelope = {
        "id": "TX_DIVORCE_PETITION_WITH_CHILDREN",
        "state": "TX",
        "jurisdiction": "harris:dc",
        "version": "1.0",
        "form_data": {
            "_PLAINTIFF_1_FULL_NAME": "John Michael Doe",
            "_CHILDREN": None,
        },
    }
    payload = build_generate_documents_request(payload=envelope)
    assert payload == {
        "id": "TX_DIVORCE_PETITION_WITH_CHILDREN",
        "state": "TX",
        "jurisdiction": "harris:dc",
        "version": "1.0",
        "form_data": {
            "_PLAINTIFF_1_FULL_NAME": "John Michael Doe",
            "_CHILDREN": "",
        },
    }


def test_extract_document_url_from_common_shapes():
    url = "https://s3.amazonaws.com/bucket/case_document.pdf"
    assert extract_document_url({"item": {"s3_url": url}}) == url
    assert extract_document_url({"download_url": url}) == url
    assert extract_document_url({"items": [{"url": url}]}) == url
    assert extract_document_url({"document": {"link": url}}) == url
    assert extract_document_url({"message": "ok"}) == ""


def test_generated_pdf_name():
    assert GENERATED_PDF_NAME == "case_document.pdf"


@pytest.mark.asyncio
async def test_generate_posts_form_data_and_returns_s3_link():
    class FakeClient:
        client_token = "GENS77"

        async def post_json(self, path, payload, include_auth=True):
            assert path == "/ai/tx/generate_documents"
            assert include_auth is False
            assert payload["id"] == "TX_DIVORCE_PETITION_WITH_CHILDREN"
            assert payload["state"] == "TX"
            assert payload["jurisdiction"] == "harris:dc"
            assert payload["version"] == "1.0"
            assert payload["form_data"]["_COUNTY_COURT"] == "Harris"
            return {
                "item": {
                    "s3_url": "https://s3.amazonaws.com/bucket/case_document.pdf"
                }
            }

    rendered = await USLegalProDocumentGenerationService(client=FakeClient()).generate(
        payload={
            "id": "TX_DIVORCE_PETITION_WITH_CHILDREN",
            "state": "TX",
            "jurisdiction": "harris:dc",
            "version": "1.0",
            "form_data": {"_COUNTY_COURT": "Harris"},
        },
    )
    assert rendered.file_name == "case_document.pdf"
    assert rendered.download_url == "https://s3.amazonaws.com/bucket/case_document.pdf"
