"""Pydantic models for legal package (Excel ZIP) RDS ingestion API."""

from typing import Any, Optional

from pydantic import BaseModel, Field


class LegalPackageIngestionResponse(BaseModel):
    success: bool = Field(..., description="Whether ingestion completed")
    message: str = Field(..., description="Human-readable outcome")
    package_id: Optional[str] = None
    package_version: Optional[str] = None
    practice_area: Optional[str] = None
    jurisdiction: Optional[str] = None
    parse_summary: dict[str, Any] = Field(default_factory=dict)
    import_counts: dict[str, int] = Field(default_factory=dict)
    package_import_id: Optional[str] = None
    warnings: list[str] = Field(default_factory=list)
    archive_manifest: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Includes upsert flag and s3_uploads count when applicable",
    )


class LegalPackageValidationResponse(BaseModel):
    valid: bool
    message: str
    package_id: Optional[str] = None
    parse_summary: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    archive_manifest: dict[str, Any] = Field(default_factory=dict)
