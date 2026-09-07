"""Call US Legal Pro POST /ai/{state}/generate_documents with mapped form JSON."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from app.adapters.uslegalpro.client import USLegalProClient
from app.config.settings import settings

logger = logging.getLogger(__name__)

GENERATED_PDF_NAME = "case_document.pdf"

_URL_KEYS = (
    "download_url",
    "s3_url",
    "s3_link",
    "file_url",
    "pdf_url",
    "document_url",
    "url",
    "link",
    "href",
)


def build_generate_documents_request(
    *,
    payload: Optional[Dict[str, Any]] = None,
    document_id: str = "",
    state: str = "",
    jurisdiction: str = "",
    form_data: Optional[Dict[str, Any]] = None,
    version: str = "",
) -> Dict[str, Any]:
    """Build the generate_documents body from a field_mapping envelope.

    Top-level id/state/jurisdiction/version stay as provided. Only form_data
    values are stringified. Nothing is hardcoded.
    """
    source = dict(payload or {})
    inner = source.get("form_data") if isinstance(source.get("form_data"), dict) else None
    if inner is None:
        inner = form_data or {}
    request = dict(source)
    if not str(request.get("id") or "").strip():
        request["id"] = str(document_id or "").strip()
    else:
        request["id"] = str(request["id"]).strip()
    if not str(request.get("state") or "").strip():
        request["state"] = str(state or "").strip()
    else:
        request["state"] = str(request["state"]).strip()
    if not str(request.get("jurisdiction") or "").strip():
        request["jurisdiction"] = str(jurisdiction or "").strip()
    else:
        request["jurisdiction"] = str(request["jurisdiction"]).strip()
    from app.services.field_mapping_service import format_generate_documents_version

    request["version"] = format_generate_documents_version(
        request.get("version"),
        version,
    )
    request["form_data"] = {
        str(key): "" if value is None else str(value)
        for key, value in inner.items()
    }
    return request


def extract_document_url(payload: Any) -> str:
    """Find the first HTTP(S) document URL in a generate_documents response."""
    if isinstance(payload, str):
        text = payload.strip()
        if text.startswith("http://") or text.startswith("https://"):
            return text
        return ""
    if isinstance(payload, dict):
        for key in _URL_KEYS:
            found = extract_document_url(payload.get(key))
            if found:
                return found
        for nested_key in ("item", "data", "document", "result"):
            found = extract_document_url(payload.get(nested_key))
            if found:
                return found
        for list_key in ("items", "documents", "files"):
            found = extract_document_url(payload.get(list_key))
            if found:
                return found
        return ""
    if isinstance(payload, list):
        for item in payload:
            found = extract_document_url(item)
            if found:
                return found
    return ""


@dataclass
class GeneratedCourtDocument:
    download_url: str
    file_name: str = GENERATED_PDF_NAME
    raw: Dict[str, Any] = field(default_factory=dict)
    request_payload: Dict[str, Any] = field(default_factory=dict)


class USLegalProDocumentGenerationService:
    """POST mapped form_data to the platform document-generation API."""

    def __init__(self, client: Optional[USLegalProClient] = None) -> None:
        timeout = float(
            getattr(
                settings,
                "USLEGALPRO_DOC_GEN_TIMEOUT_SECONDS",
                settings.USLEGALPRO_API_TIMEOUT_SECONDS,
            )
            or 180.0
        )
        self._client = client or USLegalProClient(timeout_seconds=timeout)

    async def generate(
        self,
        *,
        payload: Optional[Dict[str, Any]] = None,
        state_code: str = "",
        jurisdiction_code: str = "",
        document_id: str = "",
        form_data: Optional[Dict[str, Any]] = None,
        version: str = "",
    ) -> GeneratedCourtDocument:
        payload = build_generate_documents_request(
            payload=payload,
            document_id=document_id,
            state=state_code,
            jurisdiction=jurisdiction_code,
            form_data=form_data,
            version=version,
        )
        if not payload["id"]:
            raise RuntimeError(
                "No document template id is available for generate_documents."
            )
        if not payload["state"]:
            raise RuntimeError("No state is available for generate_documents.")
        if not payload["jurisdiction"]:
            raise RuntimeError("No jurisdiction is available for generate_documents.")
        if not payload["version"]:
            raise RuntimeError(
                "No document template version is available for generate_documents."
            )
        if not self._client.client_token:
            raise RuntimeError(
                "USLEGALPRO_CLIENT_TOKEN is required to generate court documents."
            )

        state = payload["state"].lower()
        logger.info(
            "Generating court document id=%s state=%s jurisdiction=%s",
            payload["id"],
            state,
            payload["jurisdiction"],
        )
        response = await self._client.post_json(
            f"/ai/{state}/generate_documents",
            payload,
            include_auth=False,
        )
        body = dict(response) if isinstance(response, dict) else {"item": response}
        download_url = extract_document_url(body)
        if not download_url:
            raise RuntimeError(
                "The document generation API did not return an S3 link."
            )
        return GeneratedCourtDocument(
            download_url=download_url,
            request_payload=payload,
            raw=body,
        )
