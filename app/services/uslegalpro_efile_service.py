"""US Legal Pro eFile submit/status orchestration helpers."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from app.adapters.uslegalpro.tokens import resolve_auth_token
from app.config.settings import settings
from app.services.operational_user_repository import OperationalUserRepository
from app.services.uslegalpro_api_client import USLegalProApiClient
from app.services.uslegalpro_codes_service import USLegalProCodesService

logger = logging.getLogger(__name__)


@dataclass
class EFileSubmitResult:
    envelope_id: str
    reference_id: str
    status: str
    message: str
    raw: Dict[str, Any]


def unique_reference_id(prefix: str = "EFILE") -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"{prefix}-{stamp}-{uuid4().hex[:12]}"


def _items(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    items = payload.get("items")
    if isinstance(items, list):
        return [dict(item) for item in items if isinstance(item, dict)]
    item = payload.get("item")
    if isinstance(item, dict):
        return [dict(item)]
    return []


def _first_filing(selections: Dict[str, Any], generated_documents: List[Dict[str, Any]]) -> Dict[str, Any]:
    # Preferred explicit filing list from upstream caller.
    explicit = selections.get("efile_filings")
    if isinstance(explicit, list) and explicit and isinstance(explicit[0], dict):
        return dict(explicit[0])
    if generated_documents:
        row = dict(generated_documents[0])
        return {
            "file_name": row.get("file_name") or "case_document.pdf",
            "description": row.get("template_name") or "Court Filing",
            "file": (
                row.get("file")
                or row.get("file_url")
                or row.get("download_url")
                or row.get("s3_url")
                or selections.get("efile_file_url")
            ),
            "size": row.get("size"),
            "doc_type": selections.get("doc_type") or selections.get("document_type_code"),
            "code": selections.get("filing_code"),
            "id": row.get("document_id") or f"Doc_{uuid4().hex[:8]}",
            "associated_parties": row.get("associated_parties") or [],
        }
    return {
        "file_name": "document.pdf",
        "description": "Court Filing",
        "file": selections.get("efile_file_url"),
        "size": selections.get("efile_file_size"),
        "doc_type": selections.get("doc_type") or selections.get("document_type_code"),
        "code": selections.get("filing_code"),
        "id": f"Doc_{uuid4().hex[:8]}",
        "associated_parties": [],
    }


class USLegalProEFileService:
    """Build payloads and call eFile / envelope APIs with user auth context."""

    def __init__(
        self,
        *,
        user_repo: Optional[OperationalUserRepository] = None,
        codes_service: Optional[USLegalProCodesService] = None,
    ) -> None:
        self.user_repo = user_repo
        self.codes_service = codes_service or USLegalProCodesService()

    async def _auth_token_for_user(self, user_id: str) -> str:
        user_row = await self.user_repo.get_by_user_id(user_id) if self.user_repo else None
        return resolve_auth_token(user_row)

    async def _client_for_user(self, user_id: str) -> USLegalProApiClient:
        auth_token = await self._auth_token_for_user(user_id)
        return USLegalProApiClient(auth_token=auth_token)

    async def _ensure_filing_code(self, selections: Dict[str, Any], filing: Dict[str, Any]) -> None:
        if filing.get("code"):
            return
        url = str(selections.get("filing_codes_url") or "").strip()
        if not url:
            return
        options = await self.codes_service.fetch_by_url(url)
        if not options:
            return
        selected = str(selections.get("filing_code") or "").strip()
        doc_type = str(selections.get("doc_type") or selections.get("document_type_code") or "").strip()
        match = None
        if selected:
            match = next((row for row in options if str(row.get("code") or "") == selected), None)
        if match is None and doc_type:
            match = next((row for row in options if str(row.get("code") or "") == doc_type), None)
        if match is None:
            match = options[0]
        filing["code"] = str(match.get("code") or "").strip()

    def _build_case_parties(self, selections: Dict[str, Any], filing_party_id: str) -> List[Dict[str, Any]]:
        explicit = selections.get("efile_case_parties")
        if isinstance(explicit, list):
            return [dict(item) for item in explicit if isinstance(item, dict)]
        party_type_code = str(selections.get("party_type_code") or "").strip()
        first_name = str(selections.get("first_name") or selections.get("plaintiff_first_name") or "").strip()
        last_name = str(selections.get("last_name") or selections.get("plaintiff_last_name") or "").strip()
        if not party_type_code:
            return []
        return [
            {
                "id": filing_party_id or f"Party_{uuid4().hex[:8]}",
                "type": party_type_code,
                "first_name": first_name,
                "last_name": last_name,
                "is_business": False,
                "additional_attorneys": [],
            }
        ]

    async def build_submit_payload(
        self,
        *,
        mode: str,
        selections: Dict[str, Any],
        collected_answers: Dict[str, Any],
        generated_documents: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        payload = dict(selections.get("efile_payload_override") or {})
        if payload:
            return payload

        filing = _first_filing(selections, generated_documents)
        await self._ensure_filing_code(selections, filing)
        if not filing.get("file"):
            raise ValueError(
                "No filing document URL is available for e-file submission. "
                "Provide selections.efile_file_url or efile_filings with file URLs."
            )
        if not filing.get("doc_type"):
            raise ValueError("Document type code is required for e-file submission.")
        if not filing.get("code"):
            raise ValueError("Filing code is required for e-file submission.")

        filing_party_id = str(
            selections.get("filing_party_id")
            or selections.get("efile_filing_party_id")
            or f"Party_{uuid4().hex[:8]}"
        )
        reference_id = str(selections.get("reference_id") or unique_reference_id())
        payment_account_id = str(selections.get("court_payment_account_id") or "").strip()
        if not payment_account_id:
            raise ValueError("Court payment account id is required before e-filing.")

        state_code = str(selections.get("state_code") or "").strip().lower()
        if not state_code:
            raise ValueError("State code is required before e-filing.")

        data: Dict[str, Any] = {
            "reference_id": reference_id,
            "payment_account_id": payment_account_id,
            "filing_party_id": filing_party_id,
            "filings": [filing],
        }

        if mode == "filing_existing":
            case_tracking_id = str(selections.get("case_tracking_id") or "").strip()
            if not case_tracking_id:
                raise ValueError("Existing-case filing requires case_tracking_id.")
            data.update(
                {
                    "case_tracking_id": case_tracking_id,
                    "filing_type": str(selections.get("filing_type") or "EFile"),
                }
            )
        else:
            # New-case filing path.
            case_parties = self._build_case_parties(selections, filing_party_id)
            if not case_parties:
                raise ValueError(
                    "New-case filing requires case party details. "
                    "Provide selections.efile_case_parties or party type data."
                )
            data.update(
                {
                    "filer_type": str(selections.get("filer_type") or settings.ENV or "PRO_SE"),
                    "jurisdiction": str(selections.get("jurisdiction_code") or ""),
                    "case_category": str(selections.get("case_category_code") or ""),
                    "case_type": str(selections.get("case_type_code") or ""),
                    "provider_tax": str(selections.get("provider_tax") or "0"),
                    "provider_fee": str(selections.get("provider_fee") or "0"),
                    "filing_type": str(selections.get("filing_type") or "EFileAndServe"),
                    "filing_state": state_code,
                    "case_parties": case_parties,
                }
            )
            if not data["jurisdiction"] or not data["case_category"] or not data["case_type"]:
                raise ValueError(
                    "New-case filing requires jurisdiction, case category, and case type codes."
                )

        return {"data": data}

    async def submit(
        self,
        *,
        user_id: str,
        mode: str,
        selections: Dict[str, Any],
        collected_answers: Dict[str, Any],
        generated_documents: List[Dict[str, Any]],
    ) -> EFileSubmitResult:
        client = await self._client_for_user(user_id)
        state_code = str(selections.get("state_code") or "").strip().lower()
        payload = await self.build_submit_payload(
            mode=mode,
            selections=selections,
            collected_answers=collected_answers,
            generated_documents=generated_documents,
        )
        response = await client.submit_efile(state_code, payload)
        item = (_items(response) or [{}])[0]
        envelope_id = str(item.get("id") or item.get("envelope_id") or "").strip()
        status = str(item.get("status") or response.get("status") or "submitted").strip()
        message = str(response.get("message") or item.get("message") or "E-file request submitted.").strip()
        reference_id = str(payload.get("data", {}).get("reference_id") or "").strip()
        return EFileSubmitResult(
            envelope_id=envelope_id,
            reference_id=reference_id,
            status=status,
            message=message,
            raw=response,
        )

    async def envelope_status(
        self,
        *,
        user_id: str,
        state_code: str,
        envelope_id: str,
        fields: str = "",
    ) -> Dict[str, Any]:
        client = await self._client_for_user(user_id)
        return await client.get_envelope(
            state_code,
            envelope_id,
            fields=fields or None,
        )

