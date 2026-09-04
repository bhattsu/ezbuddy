"""Strictly map API filing selections to a workflow definition."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from app.adapters.llm.bedrock import Bedrock, get_bedrock
from app.agents.conversation.schemas.filing_llm_schemas import WorkflowMatchOutput
from app.core.prompts.context import format_llm_prompt

logger = logging.getLogger(__name__)

MIN_MATCH_CONFIDENCE = 0.90

WORKFLOW_MATCH_PROMPT = """Confirm whether the selected API filing details match
one of the strictly pre-matched workflow definitions.

API selections:
{selections_json}

Expected workflow names derived from the selections:
{expected_names_json}

Strict workflow_name candidates from workflow_definitions:
{candidates_json}

Rules:
- Candidates were selected using exact normalized workflow_name equality.
- Confirm a candidate only when its meaning is fully consistent with the API
  state, case category, case type, jurisdiction, and party type.
- Do not use a loosely related case type.
- Choose only a workflow_id present in the candidates.
- Return matched=false if any selected detail materially conflicts.
- Never invent a workflow_id.
"""


def _normalize(value: Any) -> str:
    """Case/punctuation-insensitive equality; does not perform fuzzy matching."""
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


class WorkflowMatchAgent:
    """Exact workflow-name gate followed by an LLM consistency check."""

    def __init__(self, bedrock: Optional[Bedrock] = None) -> None:
        self.bedrock = bedrock or get_bedrock()

    @staticmethod
    def expected_workflow_names(selections: Dict[str, Any]) -> List[str]:
        """Construct names such as ``Texas Divorce With Children``."""
        state_name = str(selections.get("state_name") or "").strip()
        state_code = str(selections.get("state_code") or "").strip()
        category = str(selections.get("case_category_name") or "").strip()
        case_type = str(selections.get("case_type_name") or "").strip()

        if not case_type:
            return []

        names = [case_type]
        for state in (state_name, state_code):
            if state:
                names.append(f"{state} {case_type}")
                if category:
                    names.append(f"{state} {category} {case_type}")
        if category:
            names.append(f"{category} {case_type}")

        # Preserve order while removing normalized duplicates.
        unique: List[str] = []
        seen: set[str] = set()
        for name in names:
            normalized = _normalize(name)
            if normalized and normalized not in seen:
                seen.add(normalized)
                unique.append(name)
        return unique

    @classmethod
    def strict_candidates(
        cls,
        selections: Dict[str, Any],
        workflows: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        expected = {
            _normalize(name) for name in cls.expected_workflow_names(selections)
        }
        return [
            row
            for row in workflows
            if _normalize(row.get("workflow_name")) in expected
        ]

    @staticmethod
    def _selection_context(selections: Dict[str, Any]) -> Dict[str, Any]:
        keys = (
            "state_code",
            "state_name",
            "jurisdiction_code",
            "jurisdiction_name",
            "case_category_code",
            "case_category_name",
            "case_type_code",
            "case_type_name",
            "party_type_code",
            "party_type_name",
        )
        return {key: selections.get(key) for key in keys if selections.get(key)}

    async def match(
        self,
        selections: Dict[str, Any],
        workflows: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        expected_names = self.expected_workflow_names(selections)
        candidates = self.strict_candidates(selections, workflows)

        if not candidates:
            return {
                "matched": False,
                "workflow_id": None,
                "confidence": 0.0,
                "reason": (
                    "No workflow_definition.workflow_name exactly matches "
                    f"the API selection ({', '.join(expected_names)})."
                ),
                "expected_workflow_names": expected_names,
                "strict_candidates": [],
            }

        compact = [
            {
                key: row.get(key)
                for key in (
                    "workflow_id",
                    "workflow_code",
                    "workflow_name",
                    "case_type",
                    "sub_case_type",
                    "jurisdiction_code",
                    "jurisdiction_name",
                )
                if row.get(key) is not None
            }
            for row in candidates
        ]
        prompt = format_llm_prompt(
            WORKFLOW_MATCH_PROMPT,
            selections_json=json.dumps(self._selection_context(selections)),
            expected_names_json=json.dumps(expected_names),
            candidates_json=json.dumps(compact, default=str),
        )

        try:
            output = await self.bedrock.invoke_structured_prompt(
                prompt, WorkflowMatchOutput
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Strict workflow LLM confirmation failed: %s", exc)
            return {
                "matched": False,
                "workflow_id": None,
                "confidence": 0.0,
                "reason": "The workflow confirmation service was unavailable.",
                "expected_workflow_names": expected_names,
                "strict_candidates": compact,
            }

        result = output.model_dump()
        workflow_id = str(result.get("workflow_id") or "")
        by_id = {str(row.get("workflow_id")): row for row in candidates}
        confidence = float(result.get("confidence") or 0.0)

        if (
            not result.get("matched")
            or workflow_id not in by_id
            or confidence < MIN_MATCH_CONFIDENCE
        ):
            return {
                **result,
                "matched": False,
                "workflow_id": None,
                "matched_workflow": None,
                "expected_workflow_names": expected_names,
                "strict_candidates": compact,
            }

        return {
            **result,
            "matched": True,
            "workflow_id": workflow_id,
            "matched_workflow": dict(by_id[workflow_id]),
            "expected_workflow_names": expected_names,
            "strict_candidates": compact,
        }
