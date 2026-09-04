"""Pydantic models for parsed legal configuration Excel workbooks."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class PackageRow(BaseModel):
    model_config = ConfigDict(extra="ignore")

    package_id: str
    package_name: str
    practice_area: str
    jurisdiction: str
    version: Optional[str] = None
    operation: Optional[str] = None
    effective_date: Optional[date | datetime] = None
    package_status: Optional[str] = None
    prepared_by: Optional[str] = None
    last_updated: Optional[date | datetime | str] = None
    notes: Optional[str] = None


class WorkflowRow(BaseModel):
    model_config = ConfigDict(extra="ignore")

    workflow_code: str
    workflow_name: str
    case_variant: Optional[str] = None
    parent_workflow: Optional[str] = None
    workflow_category: Optional[str] = None
    reusable: Optional[bool] = None
    description: Optional[str] = None
    version: Optional[str] = None
    effective_from: Optional[date | datetime] = None
    effective_to: Optional[date | datetime] = None
    status: Optional[str] = None


class QuestionRow(BaseModel):
    model_config = ConfigDict(extra="ignore")

    question_code: str
    question: str
    input_type: str
    data_type: str
    lookup_code: Optional[str] = None
    validation: Optional[str] = None
    help_text: Optional[str] = None
    tags: Optional[str] = None
    reusable: Optional[bool] = None
    default_value: Optional[str] = None
    placeholder: Optional[str] = None
    version: Optional[str | int | float] = None
    status: Optional[str] = None


class LookupValueRow(BaseModel):
    model_config = ConfigDict(extra="ignore")

    lookup_code: str
    value: str
    display_label: Optional[str] = None
    display_order: Optional[int] = None
    active: Optional[bool] = None


class WorkflowMappingRow(BaseModel):
    model_config = ConfigDict(extra="ignore")

    workflow_code: str
    question_code: str
    display_order: int
    parent_question: Optional[str] = None
    section: Optional[str] = None
    page: Optional[int] = None
    display_condition: Optional[str] = None
    required: Optional[bool] = None
    default_value: Optional[str] = None
    is_editable: Optional[bool] = None
    status: Optional[str] = None


class DocumentRow(BaseModel):
    model_config = ConfigDict(extra="ignore")

    document_code: str
    workflow_code: str
    document_name: str
    file_name: Optional[str] = None
    required: Optional[bool] = None
    display_condition: Optional[str] = None
    version: Optional[str] = None
    template_version: Optional[str] = None
    placeholder_mapping: Optional[str] = None
    output_type: Optional[str] = None
    status: Optional[str] = None


class KnowledgeRow(BaseModel):
    model_config = ConfigDict(extra="ignore")

    knowledge_code: str
    category: Optional[str] = None
    jurisdiction: Optional[str] = None
    workflow_code: Optional[str] = None
    title: Optional[str] = None
    file_name: Optional[str] = None
    content: Optional[str] = None
    source: Optional[str] = None
    tags: Optional[str] = None
    keywords: Optional[str] = None
    effective_date: Optional[date | datetime] = None
    last_verified: Optional[date | datetime] = None
    version: Optional[str] = None
    status: Optional[str] = None


class LocalOverrideRow(BaseModel):
    model_config = ConfigDict(extra="ignore")

    jurisdiction: Optional[str] = None
    county: Optional[str] = None
    court: Optional[str] = None
    workflow_code: Optional[str] = None
    override_type: Optional[str] = None
    override_value: Optional[str] = None
    override_priority: Optional[int | str] = None
    source: Optional[str] = None
    effective_date: Optional[date | datetime] = None
    status: Optional[str] = None


class ExcelPackage(BaseModel):
    """Full parsed workbook — ready for a separate import/load step."""

    model_config = ConfigDict(extra="ignore")

    package: PackageRow
    workflows: list[WorkflowRow] = Field(default_factory=list)
    questions: list[QuestionRow] = Field(default_factory=list)
    lookup_values: list[LookupValueRow] = Field(default_factory=list)
    workflow_mappings: list[WorkflowMappingRow] = Field(default_factory=list)
    documents: list[DocumentRow] = Field(default_factory=list)
    knowledge: list[KnowledgeRow] = Field(default_factory=list)
    local_overrides: list[LocalOverrideRow] = Field(default_factory=list)
    source_path: Optional[str] = None
    parse_warnings: list[str] = Field(default_factory=list)
    ignored_columns: dict[str, list[str]] = Field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        return {
            "package_id": self.package.package_id,
            "practice_area": self.package.practice_area,
            "jurisdiction": self.package.jurisdiction,
            "workflows": len(self.workflows),
            "questions": len(self.questions),
            "lookup_values": len(self.lookup_values),
            "workflow_mappings": len(self.workflow_mappings),
            "documents": len(self.documents),
            "knowledge": len(self.knowledge),
            "local_overrides": len(self.local_overrides),
        }
