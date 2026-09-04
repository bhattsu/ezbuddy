"""Expected workbook structure for legal configuration packages."""

from __future__ import annotations

from typing import Final

# Sheet names must match exactly (client renames break import).
EXPECTED_SHEETS: Final[frozenset[str]] = frozenset(
    {
        "Package",
        "Workflows",
        "Question Library",
        "Lookup Lists",
        "Workflow Mapping",
        "Documents",
        "Knowledge",
        "Local Overrides",
    }
)

SHEET_PACKAGE: Final[str] = "Package"
SHEET_WORKFLOWS: Final[str] = "Workflows"
SHEET_QUESTIONS: Final[str] = "Question Library"
SHEET_LOOKUPS: Final[str] = "Lookup Lists"
SHEET_WORKFLOW_MAPPING: Final[str] = "Workflow Mapping"
SHEET_DOCUMENTS: Final[str] = "Documents"
SHEET_KNOWLEDGE: Final[str] = "Knowledge"
SHEET_LOCAL_OVERRIDES: Final[str] = "Local Overrides"

# Excel header → parser field (see CONTRACT.md)
PACKAGE_COLUMNS: Final[dict[str, str]] = {
    "Package ID": "package_id",
    "Package Name": "package_name",
    "Practice Area": "practice_area",
    "Jurisdiction": "jurisdiction",
    "Version": "version",
    "Operation": "operation",
    "Effective Date": "effective_date",
    "Package Status": "package_status",
    "Prepared By": "prepared_by",
    "Last Updated": "last_updated",
    "Notes": "notes",
}

WORKFLOW_COLUMNS: Final[dict[str, str]] = {
    "Workflow Code": "workflow_code",
    "Workflow Name": "workflow_name",
    "Case Variant": "case_variant",
    "Parent Workflow": "parent_workflow",
    "Workflow Category": "workflow_category",
    "Reusable": "reusable",
    "Description": "description",
    "Version": "version",
    "Effective From": "effective_from",
    "Effective To": "effective_to",
    "Status": "status",
}

QUESTION_COLUMNS: Final[dict[str, str]] = {
    "Question Code": "question_code",
    "Question": "question",
    "Input Type": "input_type",
    "Data Type": "data_type",
    "Lookup Code": "lookup_code",
    "Validation": "validation",
    "Help Text": "help_text",
    "Tags": "tags",
    "Reusable": "reusable",
    "Default Value": "default_value",
    "Placeholder": "placeholder",
    "Version": "version",
    "Status": "status",
}

LOOKUP_COLUMNS: Final[dict[str, str]] = {
    "Lookup Code": "lookup_code",
    "Value": "value",
    "Display Label": "display_label",
    "Display Order": "display_order",
    "Active": "active",
}

WORKFLOW_MAPPING_COLUMNS: Final[dict[str, str]] = {
    "Workflow Code": "workflow_code",
    "Question Code": "question_code",
    "Display Order": "display_order",
    "Parent Question": "parent_question",
    "Section": "section",
    "Page": "page",
    "Display Condition": "display_condition",
    "Required": "required",
    "Default Value": "default_value",
    "Is Editable": "is_editable",
    "Status": "status",
}

DOCUMENT_COLUMNS: Final[dict[str, str]] = {
    "Document Code": "document_code",
    "Workflow Code": "workflow_code",
    "Document Name": "document_name",
    "File Name": "file_name",
    "Required": "required",
    "Display Condition": "display_condition",
    "Version": "version",
    "Template Version": "template_version",
    "Placeholder Mapping": "placeholder_mapping",
    "Output Type": "output_type",
    "Status": "status",
}

KNOWLEDGE_COLUMNS: Final[dict[str, str]] = {
    "Knowledge Code": "knowledge_code",
    "Category": "category",
    "Jurisdiction": "jurisdiction",
    "Workflow Code": "workflow_code",
    "Title": "title",
    "File Name": "file_name",
    "Content": "content",
    "Source": "source",
    "Tags": "tags",
    "Keywords": "keywords",
    "Effective Date": "effective_date",
    "Last Verified": "last_verified",
    "Version": "version",
    "Status": "status",
}

LOCAL_OVERRIDE_COLUMNS: Final[dict[str, str]] = {
    "Jurisdiction": "jurisdiction",
    "County": "county",
    "Court": "court",
    "Workflow Code": "workflow_code",
    "Override Type": "override_type",
    "Override Value": "override_value",
    "Override Priority": "override_priority",
    "Source": "source",
    "Effective Date": "effective_date",
    "Status": "status",
}

SHEET_COLUMN_MAP: Final[dict[str, dict[str, str]]] = {
    SHEET_PACKAGE: PACKAGE_COLUMNS,
    SHEET_WORKFLOWS: WORKFLOW_COLUMNS,
    SHEET_QUESTIONS: QUESTION_COLUMNS,
    SHEET_LOOKUPS: LOOKUP_COLUMNS,
    SHEET_WORKFLOW_MAPPING: WORKFLOW_MAPPING_COLUMNS,
    SHEET_DOCUMENTS: DOCUMENT_COLUMNS,
    SHEET_KNOWLEDGE: KNOWLEDGE_COLUMNS,
    SHEET_LOCAL_OVERRIDES: LOCAL_OVERRIDE_COLUMNS,
}
