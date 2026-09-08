"""Map collected filing data onto a new-case e-file request via LLM."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.adapters.llm.bedrock import Bedrock, get_bedrock
from app.core.prompts.context import format_llm_prompt
from app.core.prompts.efile_mapping import (
    EFILE_MAPPING_PROMPT,
    EXISTING_CASE_EFILE_MAPPING_PROMPT,
)

logger = logging.getLogger(__name__)

NEW_CASE_EFILE_SAMPLE = {
    "data": {
        "filer_type": "54325",
        "reference_id": "DRAFT-2026-10038",
        "jurisdiction": "harris:dc",
        "payment_account_id": "CC_01eb044f-9e41-4966-93e2-31a5f8d9c01b",
        "filings": [
            {
                "code": "209523",
                "file_name": "complaint.pdf",
                "description": "Petition",
                "doc_type": "53689",
                "file": "https://example.com/complaint.pdf",
                "size": 123261,
                "associated_parties": [],
                "id": "332-c6931c95805340608d6daf5a640618fa",
            }
        ],
        "case_parties": [
            {
                "country": "US",
                "city": "Houston",
                "type": "53024",
                "zip_code": "77002",
                "address_line_1": "1200 Baker Street",
                "id": "Party_8168279",
                "state": "TX",
                "first_name": "JANE",
                "is_business": False,
                "lead_attorney": "PRO SE",
                "last_name": "DOE",
                "additional_attorneys": [],
            },
            {
                "country": "US",
                "type": "271011",
                "id": "Party_0325725",
                "first_name": "JOHN",
                "is_business": False,
                "last_name": "DOE",
                "additional_attorneys": [],
            },
        ],
        "provider_tax": "0.25",
        "filing_type": "EFileAndServe",
        "filing_state": "tx",
        "case_type": "209421",
        "provider_fee": "2.99",
        "case_category": "131370",
        "filing_party_id": "Party_8168279",
    }
}

EXISTING_CASE_EFILE_SAMPLE = {
    "data": {
        "reference_id": "48291",
        "case_tracking_id": "tyler_yuba:sc~example-case-id~CT",
        "payment_account_id": "CC_example-payment-account",
        "filing_party_id": "Party_example",
        "filing_type": "EFile",
        "filings": [
            {
                "code": "29736",
                "file_name": "Notice.pdf",
                "description": "Notice of Appeal",
                "doc_type": "44889",
                "file": "https://example.com/notice.pdf",
            }
        ],
    }
}

EXISTING_CASE_EFILE_KEYS = (
    "reference_id",
    "case_tracking_id",
    "payment_account_id",
    "filing_party_id",
    "filing_type",
    "filings",
)


class EfileMappingLLMOutput(BaseModel):
    data: Dict[str, Any] = Field(default_factory=dict)


def mapped_form_data_from_documents(
    generated_documents: Optional[List[Dict[str, Any]]],
) -> Dict[str, Any]:
    if not generated_documents:
        return {}
    row = dict(generated_documents[-1] or {})
    payload = row.get("request_payload")
    if isinstance(payload, dict) and isinstance(payload.get("form_data"), dict):
        return dict(payload["form_data"])
    fields = row.get("fields")
    if isinstance(fields, dict) and "form_data" not in fields:
        return dict(fields)
    if isinstance(fields, dict) and isinstance(fields.get("form_data"), dict):
        return dict(fields["form_data"])
    return {}


def compact_catalog_options(rows: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    compact: List[Dict[str, str]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        code = str(row.get("code") or "").strip()
        name = str(row.get("name") or row.get("label") or "").strip()
        if code or name:
            compact.append({"code": code, "name": name})
    return compact


def apply_known_efile_facts(
    data: Dict[str, Any],
    *,
    known: Dict[str, Any],
    filing: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Keep LLM-mapped parties/text, but never invent payment, codes, or file URL."""
    merged = dict(data or {})
    for key in (
        "payment_account_id",
        "jurisdiction",
        "filing_state",
        "case_category",
        "case_type",
        "reference_id",
        "case_tracking_id",
        "filer_type",
        "filing_type",
        "provider_tax",
        "provider_fee",
        "filing_party_id",
    ):
        value = known.get(key)
        if value not in (None, ""):
            merged[key] = value

    known_filing = dict(filing or known.get("filing") or {})
    filings = merged.get("filings")
    if not isinstance(filings, list) or not filings:
        merged["filings"] = [known_filing] if known_filing else []
    elif known_filing:
        first = dict(filings[0]) if isinstance(filings[0], dict) else {}
        for key in ("file", "file_name", "code", "doc_type", "size", "id"):
            if known_filing.get(key) not in (None, ""):
                first[key] = known_filing[key]
        if not first.get("description") and known_filing.get("description"):
            first["description"] = known_filing["description"]
        if "associated_parties" not in first:
            first["associated_parties"] = known_filing.get("associated_parties") or []
        filings[0] = first
        merged["filings"] = filings

    parties = merged.get("case_parties")
    if isinstance(parties, list):
        merged["case_parties"] = [dict(row) for row in parties if isinstance(row, dict)]
    if not merged.get("filing_party_id"):
        first_party = (merged.get("case_parties") or [{}])[0]
        if isinstance(first_party, dict) and first_party.get("id"):
            merged["filing_party_id"] = str(first_party["id"])
    return merged


def slim_existing_case_data(data: Dict[str, Any]) -> Dict[str, Any]:
    """Keep only the existing-case e-file keys from the sample body."""
    source = dict(data or {})
    slim = {key: source.get(key, "") for key in EXISTING_CASE_EFILE_KEYS}
    filings = slim.get("filings")
    if not isinstance(filings, list):
        slim["filings"] = []
    return slim


class EfileMappingService:
    """Produce a new-case e-file `data` object from collected filing context."""

    def __init__(self, bedrock: Optional[Bedrock] = None) -> None:
        self.bedrock = bedrock or get_bedrock()

    async def map_new_case(
        self,
        *,
        known_facts: Dict[str, Any],
        catalog_options: Dict[str, Any],
        collected_answers: Dict[str, Any],
        form_data: Dict[str, Any],
        workflow_questions: Optional[List[Any]] = None,
    ) -> Dict[str, Any]:
        return await self._map(
            EFILE_MAPPING_PROMPT,
            NEW_CASE_EFILE_SAMPLE,
            known_facts=known_facts,
            catalog_options=catalog_options,
            collected_answers=collected_answers,
            form_data=form_data,
            workflow_questions=workflow_questions,
        )

    async def map_existing_case(
        self,
        *,
        known_facts: Dict[str, Any],
        catalog_options: Dict[str, Any],
        collected_answers: Dict[str, Any],
        form_data: Dict[str, Any],
        workflow_questions: Optional[List[Any]] = None,
    ) -> Dict[str, Any]:
        return await self._map(
            EXISTING_CASE_EFILE_MAPPING_PROMPT,
            EXISTING_CASE_EFILE_SAMPLE,
            known_facts=known_facts,
            catalog_options=catalog_options,
            collected_answers=collected_answers,
            form_data=form_data,
            workflow_questions=workflow_questions,
        )

    async def _map(
        self,
        prompt_template: str,
        sample: Dict[str, Any],
        *,
        known_facts: Dict[str, Any],
        catalog_options: Dict[str, Any],
        collected_answers: Dict[str, Any],
        form_data: Dict[str, Any],
        workflow_questions: Optional[List[Any]] = None,
    ) -> Dict[str, Any]:
        prompt = format_llm_prompt(
            prompt_template,
            sample_request_json=json.dumps(sample, default=str)[:12000],
            known_facts_json=json.dumps(known_facts or {}, default=str)[:12000],
            catalog_options_json=json.dumps(catalog_options or {}, default=str)[:12000],
            workflow_questions_json=json.dumps(
                list(workflow_questions or []), default=str
            )[:12000],
            collected_answers_json=json.dumps(
                collected_answers or {}, default=str
            )[:12000],
            form_data_json=json.dumps(form_data or {}, default=str)[:12000],
        )
        mapped = await self._ask_llm(prompt)
        data = mapped.get("data") if isinstance(mapped.get("data"), dict) else mapped
        if not isinstance(data, dict) or not data:
            return {}
        return data

    async def _ask_llm(self, prompt: str) -> Dict[str, Any]:
        try:
            parsed = await self.bedrock.invoke_structured_prompt(
                prompt, EfileMappingLLMOutput
            )
            return {"data": dict(parsed.data or {})}
        except Exception as exc:  # noqa: BLE001
            logger.warning("E-file mapping structured invoke failed: %s", exc)

        try:
            body = {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 8192,
                "temperature": 0,
                "messages": [{"role": "user", "content": prompt}],
            }
            raw = await self.bedrock.invoke_prompt_with_timeout(body)
            from app.agents.utils.json_utils import parse_llm_json

            parsed = parse_llm_json(raw)
            if isinstance(parsed, dict):
                return parsed
        except Exception as exc:  # noqa: BLE001
            logger.warning("E-file mapping prompt fallback failed: %s", exc)
        return {}
