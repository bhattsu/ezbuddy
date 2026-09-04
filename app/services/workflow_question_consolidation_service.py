"""Deduplicate and cluster DB + document workflow questions via LLM."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Set

from pydantic import BaseModel, Field

from app.adapters.llm.bedrock import Bedrock, get_bedrock
from app.agents.utils.json_utils import parse_llm_json
from app.core.prompts.context import format_llm_prompt
from app.core.prompts.workflow_question_consolidation import (
    WORKFLOW_QUESTION_CONSOLIDATION_PROMPT,
)

logger = logging.getLogger(__name__)

MIN_QUESTIONS_FOR_LLM = 3


class ConsolidationCluster(BaseModel):
    cluster_id: str = ""
    question: str = ""
    canonical_field_name: str = ""
    cluster_fields: List[str] = Field(default_factory=list)
    duplicate_fields: List[str] = Field(default_factory=list)
    required: bool = True
    sort_order: int = 0


class ConsolidationLLMOutput(BaseModel):
    clusters: List[ConsolidationCluster] = Field(default_factory=list)
    removed_duplicates: List[Dict[str, str]] = Field(default_factory=list)
    summary: str = ""


@dataclass
class WorkflowQuestionConsolidationResult:
    """Consolidated questions for Q&A plus metadata for answer propagation."""

    questions: List[Dict[str, Any]] = field(default_factory=list)
    full_questions: List[Dict[str, Any]] = field(default_factory=list)
    field_aliases: Dict[str, str] = field(default_factory=dict)
    original_count: int = 0
    consolidated_count: int = 0
    summary: str = ""
    used_llm: bool = False


def _normalize_label(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()


def _question_label(item: Dict[str, Any]) -> str:
    return str(
        item.get("field_label") or item.get("question") or item.get("field_name") or ""
    ).strip()


def _tag_questions(
    items: Iterable[Dict[str, Any]], source: str
) -> List[Dict[str, Any]]:
    tagged: List[Dict[str, Any]] = []
    for item in items or []:
        row = dict(item)
        row["source"] = source
        tagged.append(row)
    return tagged


def _merge_question_metadata(
    canonical_name: str,
    all_questions: Dict[str, Dict[str, Any]],
    cluster_fields: List[str],
) -> Dict[str, Any]:
    base = dict(all_questions.get(canonical_name) or {})
    if not base and cluster_fields:
        base = dict(all_questions.get(cluster_fields[0]) or {})
    base["field_name"] = canonical_name
    label = base.get("field_label") or base.get("question") or canonical_name
    base.setdefault("field_label", label)
    base.setdefault("question", label)
    base.setdefault("question_type", "TEXT")
    base.setdefault("required", True)
    base.setdefault("sort_order", 0)
    return base


def _deterministic_consolidate(
    db_questions: List[Dict[str, Any]],
    document_questions: List[Dict[str, Any]],
) -> ConsolidationLLMOutput:
    """Label-based dedupe when LLM is unavailable."""
    combined = _tag_questions(db_questions, "db") + _tag_questions(
        document_questions, "document"
    )
    if not combined:
        return ConsolidationLLMOutput()

    by_label: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    removed: List[Dict[str, str]] = []

    for item in combined:
        name = str(item.get("field_name") or "").strip()
        if not name:
            continue
        norm = _normalize_label(_question_label(item))
        if not norm:
            norm = _normalize_label(name)
        if norm in by_label:
            kept = by_label[norm]["field_name"]
            removed.append(
                {
                    "removed_field": name,
                    "kept_field": kept,
                    "reason": "matching label",
                }
            )
            continue
        by_label[norm] = item
        order.append(norm)

    clusters: List[ConsolidationCluster] = []
    for index, norm in enumerate(order, start=1):
        item = by_label[norm]
        name = str(item["field_name"])
        clusters.append(
            ConsolidationCluster(
                cluster_id=f"cluster_{index}",
                question=_question_label(item),
                canonical_field_name=name,
                cluster_fields=[name],
                duplicate_fields=[],
                required=bool(item.get("required", True)),
                sort_order=int(item.get("sort_order") or index),
            )
        )

    return ConsolidationLLMOutput(
        clusters=clusters,
        removed_duplicates=removed,
        summary=f"Merged {len(removed)} duplicate(s) by label matching.",
    )


def _build_from_clusters(
    llm_out: ConsolidationLLMOutput,
    all_questions: Dict[str, Dict[str, Any]],
) -> WorkflowQuestionConsolidationResult:
    aliases: Dict[str, str] = {}
    consolidated: List[Dict[str, Any]] = []
    seen_canonical: Set[str] = set()

    for cluster in llm_out.clusters:
        canonical = str(cluster.canonical_field_name or "").strip()
        if not canonical:
            continue
        cluster_fields = [
            str(name).strip()
            for name in (cluster.cluster_fields or [])
            if str(name).strip()
        ]
        if not cluster_fields:
            cluster_fields = [canonical]
        if canonical not in cluster_fields:
            cluster_fields.insert(0, canonical)

        for dup in cluster.duplicate_fields or []:
            dup_name = str(dup).strip()
            if dup_name and dup_name != canonical:
                aliases[dup_name] = canonical

        for removed in llm_out.removed_duplicates or []:
            removed_field = str(removed.get("removed_field") or "").strip()
            kept_field = str(
                removed.get("kept_field") or removed.get("kept") or canonical
            ).strip()
            if removed_field and kept_field:
                aliases[removed_field] = kept_field

        meta = _merge_question_metadata(canonical, all_questions, cluster_fields)
        question_text = str(cluster.question or meta.get("field_label") or canonical).strip()
        meta["field_label"] = question_text
        meta["question"] = question_text
        meta["required"] = bool(cluster.required)
        meta["sort_order"] = int(cluster.sort_order or meta.get("sort_order") or 0)
        meta["cluster_id"] = cluster.cluster_id or canonical
        meta["cluster_fields"] = list(dict.fromkeys(cluster_fields))
        meta["duplicate_fields"] = [
            str(d).strip()
            for d in (cluster.duplicate_fields or [])
            if str(d).strip() and str(d).strip() != canonical
        ]
        meta["consolidated"] = True

        if canonical in seen_canonical:
            continue
        seen_canonical.add(canonical)
        consolidated.append(meta)

    consolidated.sort(key=lambda q: (int(q.get("sort_order") or 0), q["field_name"]))

    full_list = list(all_questions.values())
    full_list.sort(key=lambda q: (int(q.get("sort_order") or 0), q.get("field_name", "")))

    return WorkflowQuestionConsolidationResult(
        questions=consolidated,
        full_questions=full_list,
        field_aliases=aliases,
        original_count=len(all_questions),
        consolidated_count=len(consolidated),
        summary=str(llm_out.summary or "").strip(),
        used_llm=True,
    )


class WorkflowQuestionConsolidationService:
    """Merge DB workflow questions with document-extracted form fields."""

    def __init__(self, bedrock: Optional[Bedrock] = None):
        self.bedrock = bedrock or get_bedrock()

    async def consolidate(
        self,
        *,
        db_questions: List[Dict[str, Any]],
        document_questions: List[Dict[str, Any]],
        selections: Optional[Dict[str, Any]] = None,
    ) -> WorkflowQuestionConsolidationResult:
        db = _tag_questions(db_questions, "db")
        doc = _tag_questions(document_questions, "document")
        combined = db + doc
        if not combined:
            return WorkflowQuestionConsolidationResult()

        all_by_name: Dict[str, Dict[str, Any]] = {}
        for item in combined:
            name = str(item.get("field_name") or "").strip()
            if name:
                all_by_name[name] = item

        original_count = len(all_by_name)
        both_sources = bool(db) and bool(doc)
        if original_count < MIN_QUESTIONS_FOR_LLM and not both_sources:
            ordered = sorted(
                all_by_name.values(),
                key=lambda q: (int(q.get("sort_order") or 0), q.get("field_name", "")),
            )
            return WorkflowQuestionConsolidationResult(
                questions=ordered,
                full_questions=ordered,
                original_count=original_count,
                consolidated_count=original_count,
            )

        llm_out = await self._ask_llm(db, doc, selections or {})
        if not llm_out.clusters:
            llm_out = _deterministic_consolidate(db_questions, document_questions)

        result = _build_from_clusters(llm_out, all_by_name)
        result.used_llm = bool(llm_out.clusters)
        if not result.summary:
            dropped = result.original_count - result.consolidated_count
            if dropped > 0:
                result.summary = (
                    f"Grouped {result.original_count} fields into "
                    f"{result.consolidated_count} question(s)."
                )
        return result

    async def _ask_llm(
        self,
        db_questions: List[Dict[str, Any]],
        document_questions: List[Dict[str, Any]],
        selections: Dict[str, Any],
    ) -> ConsolidationLLMOutput:
        compact_db = [
            {
                "field_name": q.get("field_name"),
                "field_label": _question_label(q),
                "question_type": q.get("question_type"),
                "required": q.get("required"),
                "sort_order": q.get("sort_order"),
            }
            for q in db_questions
        ]
        compact_doc = [
            {
                "field_name": q.get("field_name"),
                "field_label": _question_label(q),
                "pdf_field": q.get("pdf_field"),
                "required": q.get("required"),
                "sort_order": q.get("sort_order"),
                "page": q.get("page"),
            }
            for q in document_questions
        ]
        prompt = format_llm_prompt(
            WORKFLOW_QUESTION_CONSOLIDATION_PROMPT,
            selections_json=json.dumps(selections, default=str)[:8000],
            db_questions_json=json.dumps(compact_db, default=str)[:12000],
            document_questions_json=json.dumps(compact_doc, default=str)[:12000],
        )
        try:
            result = await self.bedrock.invoke_structured_prompt(
                prompt, ConsolidationLLMOutput
            )
            return result
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Structured workflow consolidation failed, falling back: %s", exc
            )
            try:
                body = Bedrock.build_text_prompt_body(prompt)
                raw = await self.bedrock.invoke_prompt_with_timeout(body)
                parsed = parse_llm_json(raw)
                return ConsolidationLLMOutput.model_validate(parsed)
            except Exception as inner:  # noqa: BLE001
                logger.warning("Workflow consolidation LLM failed: %s", inner)
                return ConsolidationLLMOutput()


def propagate_cluster_answers(session: Any) -> None:
    """Copy canonical answers to duplicate / aliased workflow fields."""
    answers = session.collected_answers
    aliases: Dict[str, str] = (
        session.metadata.get("workflow_field_aliases") or {}
    )

    for question in session.workflow_questions or []:
        canonical = str(question.get("field_name") or "").strip()
        if not canonical or canonical not in answers:
            continue
        value = answers[canonical]
        for dup in question.get("duplicate_fields") or []:
            dup_name = str(dup).strip()
            if dup_name:
                answers.setdefault(dup_name, value)

    for alias, canonical in aliases.items():
        if canonical in answers:
            answers.setdefault(alias, answers[canonical])
