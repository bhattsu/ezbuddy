"""Map parsed Excel rows to RDS-ready structures (JSONB-friendly)."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Any

from app.core.ingestion.excel.schemas import (
    DocumentRow,
    ExcelPackage,
    LookupValueRow,
    QuestionRow,
    WorkflowMappingRow,
    WorkflowRow,
)

INPUT_TYPE_MAP = {
    "dropdown": "SELECT",
    "select": "SELECT",
    "radio": "SELECT",
    "checkbox": "BOOLEAN",
    "text": "TEXT",
    "textarea": "TEXT",
    "number": "NUMBER",
    "date": "DATE",
    "boolean": "BOOLEAN",
    "yes/no": "BOOLEAN",
}


def normalize_status(value: str | None) -> str:
    if not value:
        return "ACTIVE"
    upper = value.strip().upper()
    if upper in ("ACTIVE", "DRAFT", "INACTIVE", "DEPRECATED"):
        return "ACTIVE" if upper == "ACTIVE" else ("INACTIVE" if upper != "DRAFT" else "ACTIVE")
    return upper


def parse_version_int(value: str | int | float | None) -> int:
    if value is None:
        return 1
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    if not text:
        return 1
    match = re.match(r"^(\d+)", text)
    return int(match.group(1)) if match else 1


def build_lookup_index(rows: list[LookupValueRow]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row.lookup_code].append(
            {
                "value": row.value,
                "label": row.display_label or row.value,
                "order": row.display_order,
                "active": row.active,
            }
        )
    for code in grouped:
        grouped[code].sort(key=lambda x: (x["order"] is None, x["order"] or 0))
    return dict(grouped)


def map_question_type(input_type: str) -> str:
    key = input_type.strip().lower()
    return INPUT_TYPE_MAP.get(key, input_type.strip().upper()[:30])


def build_question_validation_rules(
    row: QuestionRow,
    lookup_index: dict[str, list[dict[str, Any]]],
    package_id: str,
) -> dict[str, Any]:
    rules: dict[str, Any] = {
        "data_type": row.data_type,
        "source_package_id": package_id,
    }
    if row.validation:
        try:
            parsed = json.loads(row.validation)
            if isinstance(parsed, dict):
                rules.update(parsed)
            else:
                rules["validation"] = row.validation
        except json.JSONDecodeError:
            rules["validation"] = row.validation
    if row.lookup_code:
        rules["lookup_code"] = row.lookup_code
        rules["options"] = lookup_index.get(row.lookup_code, [])
    if row.tags:
        rules["tags"] = [t.strip() for t in row.tags.split(",") if t.strip()]
    if row.default_value is not None:
        rules["default_value"] = row.default_value
    if row.reusable is not None:
        rules["reusable"] = row.reusable
    if row.version is not None:
        rules["excel_version"] = row.version
    return rules


def build_visibility_condition(mapping: WorkflowMappingRow) -> dict[str, Any] | None:
    meta: dict[str, Any] = {}
    if mapping.section:
        meta["section"] = mapping.section
    if mapping.page is not None:
        meta["page"] = mapping.page
    if mapping.parent_question:
        meta["parent_question"] = mapping.parent_question
    if mapping.default_value is not None:
        meta["default_value"] = mapping.default_value
    if mapping.is_editable is not None:
        meta["is_editable"] = mapping.is_editable
    if mapping.status:
        meta["status"] = mapping.status
    meta["display_order"] = mapping.display_order

    condition: dict[str, Any] | None = None
    if mapping.display_condition:
        raw = mapping.display_condition.strip()
        try:
            parsed = json.loads(raw)
            condition = parsed if isinstance(parsed, dict) else {"expression": raw}
        except json.JSONDecodeError:
            condition = {"expression": raw}

    if meta:
        if condition is None:
            condition = {}
        condition["_excel"] = meta
    return condition


def build_document_rule_condition(doc: DocumentRow) -> dict[str, Any] | None:
    payload: dict[str, Any] = {}
    if doc.display_condition:
        raw = doc.display_condition.strip()
        try:
            parsed = json.loads(raw)
            payload["display_condition"] = parsed if isinstance(parsed, dict) else {"expression": raw}
        except json.JSONDecodeError:
            payload["display_condition"] = {"expression": raw}
    if doc.placeholder_mapping:
        payload["placeholder_mapping"] = doc.placeholder_mapping
    if doc.output_type:
        payload["output_type"] = doc.output_type
    if doc.version:
        payload["excel_version"] = doc.version
    return payload or None


def workflow_excel_metadata(row: WorkflowRow, package_id: str) -> dict[str, Any]:
    return {
        "package_id": package_id,
        "workflow_code": row.workflow_code,
        "case_variant": row.case_variant,
        "parent_workflow": row.parent_workflow,
        "workflow_category": row.workflow_category,
        "reusable": row.reusable,
        "description": row.description,
        "excel_version": row.version,
        "excel_status": row.status,
    }


def subtype_code_from_variant(case_variant: str | None, practice_area: str) -> str:
    if case_variant:
        return re.sub(r"[^A-Z0-9_]", "_", case_variant.upper())
    area = re.sub(r"[^A-Z0-9_]", "_", practice_area.upper())
    return f"{area}_DEFAULT"


def subtype_name_from_variant(case_variant: str | None, practice_area: str) -> str:
    if case_variant:
        return case_variant.replace("_", " ").title()
    return practice_area


def case_type_code(practice_area: str) -> str:
    return re.sub(r"[^A-Z0-9_]", "_", practice_area.upper())[:30]


def knowledge_content_type(category: str | None) -> str:
    if not category:
        return "faq"
    cat = category.strip().lower()
    mapping = {
        "court rule": "court_rule_or_requirement",
        "faq": "faq",
        "procedural step": "procedural_step",
        "rejection reason": "common_rejection_reason",
    }
    return mapping.get(cat, cat.replace(" ", "_"))


def matter_type_id(practice_area: str) -> str:
    return practice_area.strip().lower().replace(" ", "_")


def knowledge_jurisdiction_id(state_name: str) -> str:
    abbr = _state_to_abbr(state_name)
    return f"us_{abbr.lower()}"


def _state_to_abbr(state_name: str) -> str:
    known = {"texas": "TX", "california": "CA", "florida": "FL", "new york": "NY"}
    key = state_name.strip().lower()
    if len(key) == 2:
        return key.upper()
    return known.get(key, key[:2].upper())


def dedupe_workflow_mappings(
    mappings: list[WorkflowMappingRow],
    warnings: list[str],
) -> list[WorkflowMappingRow]:
    """Drop exact duplicate workflow+question rows (common Excel copy/paste errors)."""
    seen: set[tuple[str, str]] = set()
    out: list[WorkflowMappingRow] = []
    for m in mappings:
        key = (m.workflow_code, m.question_code)
        if key in seen:
            warnings.append(
                f"Workflow Mapping: duplicate {m.workflow_code}/{m.question_code} "
                f"(display_order={m.display_order}); keeping first row only"
            )
            continue
        seen.add(key)
        out.append(m)
    return out


def assign_sequential_sort_orders(
    mappings: list[WorkflowMappingRow],
) -> dict[tuple[str, str], tuple[int, int]]:
    """
    Per workflow, order by Excel display_order then question_code; assign unique
    sort_order 1..N for chat. Returns (workflow_code, question_code) -> (sort_order, excel_display_order).
    """
    by_workflow: dict[str, list[WorkflowMappingRow]] = defaultdict(list)
    for m in mappings:
        by_workflow[m.workflow_code].append(m)

    result: dict[tuple[str, str], tuple[int, int]] = {}
    for wf_code, rows in by_workflow.items():
        rows_sorted = sorted(rows, key=lambda r: (r.display_order, r.question_code))
        for idx, row in enumerate(rows_sorted, start=1):
            result[(wf_code, row.question_code)] = (idx, row.display_order)
    return result


def validate_package_graph(pkg: ExcelPackage) -> list[str]:
    """Cross-sheet integrity warnings before import."""
    warnings: list[str] = []
    workflow_codes = {w.workflow_code for w in pkg.workflows}
    question_codes = {q.question_code for q in pkg.questions}

    for m in pkg.workflow_mappings:
        if m.workflow_code not in workflow_codes:
            warnings.append(f"Mapping references unknown workflow_code {m.workflow_code!r}")
        if m.question_code not in question_codes:
            warnings.append(f"Mapping references unknown question_code {m.question_code!r}")

    by_wf_order: dict[tuple[str, int], list[str]] = defaultdict(list)
    for m in pkg.workflow_mappings:
        by_wf_order[(m.workflow_code, m.display_order)].append(m.question_code)
    for (wf, order), codes in sorted(by_wf_order.items()):
        if len(codes) > 1:
            warnings.append(
                f"Workflow Mapping: {wf} display_order={order} shared by "
                f"{len(codes)} questions {codes}; chat uses sequential sort_order instead"
            )

    for d in pkg.documents:
        if d.workflow_code not in workflow_codes:
            warnings.append(f"Document {d.document_code!r} references unknown workflow {d.workflow_code!r}")

    return warnings
