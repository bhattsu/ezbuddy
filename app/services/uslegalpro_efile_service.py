"""US Legal Pro eFile submit/status orchestration helpers."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from app.adapters.uslegalpro.tokens import (
    client_token_from_auth_token,
    resolve_auth_token,
)
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
    case_tracking_id: str = ""
    filings: List[Dict[str, Any]] = field(default_factory=list)


def format_efile_success_message(result: EFileSubmitResult) -> str:
    lines = ["E-filed successfully."]
    if result.envelope_id:
        lines.append(f"Envelope ID: {result.envelope_id}")
    if result.case_tracking_id:
        lines.append(f"Case tracking ID: {result.case_tracking_id}")
    if result.reference_id:
        lines.append(f"Reference ID: {result.reference_id}")
    if result.status:
        lines.append(f"Status: {result.status}")
    for filing in result.filings:
        if not isinstance(filing, dict):
            continue
        code = str(filing.get("code") or "").strip()
        filing_id = str(filing.get("id") or "").strip()
        status = str(filing.get("status") or "").strip()
        label = f"Filing {code}".strip() if code else "Filing"
        detail = status or "submitted"
        if filing_id:
            detail = f"{detail} ({filing_id})"
        lines.append(f"{label}: {detail}")
    return "\n".join(lines)


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
        mapping_service: Optional[Any] = None,
    ) -> None:
        self.user_repo = user_repo
        self.codes_service = codes_service or USLegalProCodesService()
        self._mapping_service = mapping_service

    @property
    def mapping_service(self):
        if self._mapping_service is None:
            from app.services.efile_mapping_service import EfileMappingService

            self._mapping_service = EfileMappingService()
        return self._mapping_service

    async def _auth_token_for_user(self, user_id: str) -> str:
        user_row = await self.user_repo.get_by_user_id(user_id) if self.user_repo else None
        return resolve_auth_token(user_row)

    async def _client_for_user(self, user_id: str) -> USLegalProApiClient:
        auth_token = await self._auth_token_for_user(user_id)
        client_token = (
            client_token_from_auth_token(auth_token) or settings.USLEGALPRO_CLIENT_TOKEN
        )
        return USLegalProApiClient(
            auth_token=auth_token,
            client_token=client_token or None,
        )

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

    async def _catalog_options(self, selections: Dict[str, Any]) -> Dict[str, Any]:
        party_rows: List[Dict[str, Any]] = []
        party_url = str(selections.get("party_type_codes_url") or "").strip()
        if party_url:
            party_rows = await self.codes_service.fetch_by_url(party_url)
        if not party_rows:
            party_rows = await self.codes_service.get_party_types_for_case_type(
                str(selections.get("state_code") or ""),
                str(selections.get("jurisdiction_code") or ""),
                str(selections.get("case_category_code") or ""),
                str(selections.get("case_type_code") or ""),
            )
        filing_rows: List[Dict[str, Any]] = []
        filing_url = str(selections.get("filing_codes_url") or "").strip()
        if filing_url:
            filing_rows = await self.codes_service.fetch_by_url(filing_url)
        document_rows: List[Dict[str, Any]] = []
        document_url = str(selections.get("document_type_codes_url") or "").strip()
        if document_url:
            document_rows = await self.codes_service.fetch_by_url(document_url)
        from app.services.efile_mapping_service import compact_catalog_options

        existing_parties = []
        details = selections.get("case_details") or selections.get("case_metadata") or {}
        if isinstance(details, dict):
            raw_parties = details.get("case_parties") or details.get("parties") or []
            if isinstance(raw_parties, list):
                existing_parties = [
                    {
                        "id": str(row.get("id") or "").strip(),
                        "type": str(row.get("type") or row.get("party_type") or "").strip(),
                        "first_name": str(row.get("first_name") or "").strip(),
                        "last_name": str(row.get("last_name") or "").strip(),
                    }
                    for row in raw_parties
                    if isinstance(row, dict)
                ]
        return {
            "party_types": compact_catalog_options(party_rows),
            "filing_codes": compact_catalog_options(filing_rows),
            "document_types": compact_catalog_options(document_rows),
            "existing_case_parties": existing_parties,
        }

    async def _map_new_case_data(
        self,
        *,
        selections: Dict[str, Any],
        collected_answers: Dict[str, Any],
        generated_documents: List[Dict[str, Any]],
        filing: Dict[str, Any],
        known: Dict[str, Any],
        workflow_questions: Optional[List[Any]] = None,
    ) -> Dict[str, Any]:
        try:
            catalog = await self._catalog_options(selections)
        except Exception as exc:  # noqa: BLE001
            logger.warning("E-file catalog lookup failed: %s", exc)
            catalog = {}
        from app.services.efile_mapping_service import (
            apply_known_efile_facts,
            mapped_form_data_from_documents,
        )

        try:
            mapped = await self.mapping_service.map_new_case(
                known_facts=known,
                catalog_options=catalog,
                collected_answers=collected_answers,
                form_data=mapped_form_data_from_documents(generated_documents),
                workflow_questions=workflow_questions,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("E-file LLM mapping failed: %s", exc)
            return {}
        if not mapped:
            return {}
        return apply_known_efile_facts(mapped, known=known, filing=filing)

    async def _map_existing_case_data(
        self,
        *,
        selections: Dict[str, Any],
        collected_answers: Dict[str, Any],
        generated_documents: List[Dict[str, Any]],
        filing: Dict[str, Any],
        known: Dict[str, Any],
        workflow_questions: Optional[List[Any]] = None,
    ) -> Dict[str, Any]:
        try:
            catalog = await self._catalog_options(selections)
        except Exception as exc:  # noqa: BLE001
            logger.warning("E-file catalog lookup failed: %s", exc)
            catalog = {}
        from app.services.efile_mapping_service import (
            apply_known_efile_facts,
            mapped_form_data_from_documents,
            slim_existing_case_data,
        )

        try:
            mapped = await self.mapping_service.map_existing_case(
                known_facts=known,
                catalog_options=catalog,
                collected_answers=collected_answers,
                form_data=mapped_form_data_from_documents(generated_documents),
                workflow_questions=workflow_questions,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Existing-case e-file LLM mapping failed: %s", exc)
            return {}
        if not mapped:
            return {}
        return slim_existing_case_data(
            apply_known_efile_facts(mapped, known=known, filing=filing)
        )

    async def build_submit_payload(
        self,
        *,
        mode: str,
        selections: Dict[str, Any],
        collected_answers: Dict[str, Any],
        generated_documents: List[Dict[str, Any]],
        workflow_questions: Optional[List[Any]] = None,
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
        if mode != "filing_existing":
            if not filing.get("doc_type"):
                raise ValueError("Document type code is required for e-file submission.")
            if not filing.get("code"):
                raise ValueError("Filing code is required for e-file submission.")

        selected_party_id = str(
            selections.get("filing_party_id")
            or selections.get("efile_filing_party_id")
            or ""
        ).strip()
        filing_party_id = selected_party_id or f"Party_{uuid4().hex[:8]}"
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
            known = {
                "reference_id": reference_id,
                "case_tracking_id": case_tracking_id,
                "payment_account_id": payment_account_id,
                "filing_party_id": selected_party_id,
                "filing_type": str(selections.get("filing_type") or "").strip(),
            }
            mapped = await self._map_existing_case_data(
                selections=selections,
                collected_answers=collected_answers,
                generated_documents=generated_documents,
                filing=filing,
                known=known,
                workflow_questions=workflow_questions,
            )
            if mapped:
                data = mapped
            else:
                data.update(
                    {
                        "case_tracking_id": case_tracking_id,
                        "filing_type": str(selections.get("filing_type") or ""),
                    }
                )
        else:
            known = {
                "filer_type": str(selections.get("filer_type") or "").strip(),
                "reference_id": reference_id,
                "jurisdiction": str(selections.get("jurisdiction_code") or "").strip(),
                "payment_account_id": payment_account_id,
                "case_category": str(selections.get("case_category_code") or "").strip(),
                "case_type": str(
                    selections.get("case_type_code") or selections.get("case_type") or ""
                ).strip(),
                "filing_type": str(selections.get("filing_type") or "").strip(),
                "filing_state": state_code,
                "provider_tax": str(selections.get("provider_tax") or "").strip(),
                "provider_fee": str(selections.get("provider_fee") or "").strip(),
                "filing_party_id": selected_party_id,
                "party_type_code": str(selections.get("party_type_code") or "").strip(),
                "party_type_name": str(selections.get("party_type_name") or "").strip(),
            }
            mapped = await self._map_new_case_data(
                selections=selections,
                collected_answers=collected_answers,
                generated_documents=generated_documents,
                filing=filing,
                known=known,
                workflow_questions=workflow_questions,
            )
            if mapped:
                data = mapped
            else:
                case_parties = self._build_case_parties(selections, filing_party_id)
                if not case_parties:
                    raise ValueError(
                        "New-case filing requires case party details. "
                        "Provide selections.efile_case_parties or party type data."
                    )
                data.update(
                    {
                        "filer_type": str(
                            selections.get("filer_type") or settings.ENV or "PRO_SE"
                        ),
                        "jurisdiction": str(selections.get("jurisdiction_code") or ""),
                        "case_category": str(selections.get("case_category_code") or ""),
                        "case_type": str(selections.get("case_type_code") or ""),
                        "provider_tax": str(selections.get("provider_tax") or "0"),
                        "provider_fee": str(selections.get("provider_fee") or "0"),
                        "filing_type": str(
                            selections.get("filing_type") or "EFileAndServe"
                        ),
                        "filing_state": state_code,
                        "case_parties": case_parties,
                    }
                )
            if not data.get("jurisdiction") or not data.get("case_category") or not data.get("case_type"):
                raise ValueError(
                    "New-case filing requires jurisdiction, case category, and case type codes."
                )
            if not data.get("case_parties"):
                raise ValueError(
                    "New-case filing requires case party details. "
                    "Provide selections.efile_case_parties or party type data."
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
        workflow_questions: Optional[List[Any]] = None,
    ) -> EFileSubmitResult:
        client = await self._client_for_user(user_id)
        state_code = str(selections.get("state_code") or "").strip().lower()
        payload = await self.build_submit_payload(
            mode=mode,
            selections=selections,
            collected_answers=collected_answers,
            generated_documents=generated_documents,
            workflow_questions=workflow_questions,
        )
        response = await client.submit_efile(state_code, payload)
        item = (_items(response) or [{}])[0]
        filings = item.get("filings") if isinstance(item.get("filings"), list) else []
        first_filing = filings[0] if filings and isinstance(filings[0], dict) else {}
        envelope_id = str(item.get("id") or item.get("envelope_id") or "").strip()
        case_tracking_id = str(item.get("case_tracking_id") or "").strip()
        status = str(
            first_filing.get("status")
            or item.get("status")
            or response.get("status")
            or "submitted"
        ).strip()
        message = str(response.get("message") or item.get("message") or "E-filed successfully.").strip()
        reference_id = str(payload.get("data", {}).get("reference_id") or "").strip()
        return EFileSubmitResult(
            envelope_id=envelope_id,
            reference_id=reference_id,
            status=status,
            message=message,
            raw=response,
            case_tracking_id=case_tracking_id,
            filings=[dict(row) for row in filings if isinstance(row, dict)],
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

