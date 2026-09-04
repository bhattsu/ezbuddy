"""Upload legal configuration ZIP packages and load into PostgreSQL (RDS)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID

from app.adapters.data_sources.AIM_rds import RDSRepository
from app.core.ingestion.excel.api_options_config import validate_question_options_config
from app.core.ingestion.excel.import_context import ImportGeography
from app.core.ingestion.excel.mappers import validate_package_graph
from app.core.ingestion.excel.parser import LegalPackageExcelParser
from app.core.ingestion.package_archive import (
    LegalPackageArchiveLayout,
    cleanup_extract_dir,
    discover_package_layout,
    extract_zip_to_temp,
)
from app.core.ingestion.pipeline import LegalPackageIngestionPipeline
from app.services.rds_schema import assert_package_ingest_schema

logger = logging.getLogger(__name__)


@dataclass
class LegalPackageZipResult:
    layout: LegalPackageArchiveLayout
    parse_summary: dict[str, Any]
    import_counts: dict[str, int]
    package_import_id: UUID | None
    warnings: list[str]
    package_id: str | None
    package_version: str | None
    practice_area: str | None
    jurisdiction: str | None
    ignored_columns: dict[str, list[str]] = field(default_factory=dict)
    s3_uploads: int = 0


class LegalPackageIngestionService:
    """Extract client ZIP, validate workbook, upsert via ingestion pipeline."""

    async def validate_zip(self, zip_bytes: bytes, filename: str) -> dict[str, Any]:
        extract_root: Path | None = None
        try:
            extract_root = extract_zip_to_temp(zip_bytes, filename)
            layout = discover_package_layout(extract_root, filename)
            return self._validate_workbook(layout)
        finally:
            cleanup_extract_dir(extract_root)

    async def ingest_zip(
        self,
        zip_bytes: bytes,
        filename: str,
        *,
        geography: ImportGeography | None = None,
        validate_only: bool = False,
        rds: RDSRepository | None = None,
    ) -> LegalPackageZipResult:
        extract_root: Path | None = None
        try:
            extract_root = extract_zip_to_temp(zip_bytes, filename)
            layout = discover_package_layout(extract_root, filename)
            validation = self._validate_workbook(layout)
            if validation["errors"]:
                raise ValueError("; ".join(validation["errors"]))

            if validate_only:
                return LegalPackageZipResult(
                    layout=layout,
                    parse_summary=validation["parse_summary"],
                    import_counts={},
                    package_import_id=None,
                    warnings=validation["warnings"],
                    package_id=validation.get("package_id"),
                    package_version=validation.get("package_version"),
                    practice_area=validation.get("practice_area"),
                    jurisdiction=validation.get("jurisdiction"),
                    ignored_columns=validation.get("ignored_columns") or {},
                )

            if rds is None:
                raise ValueError("RDS connection is required for package ingest")
            await assert_package_ingest_schema(rds)

            pipeline = LegalPackageIngestionPipeline(geography=geography)
            result = await pipeline.run(
                layout.excel_path,
                rds=rds,
                layout=layout,
                package=validation.get("package"),
            )
            pkg = result.import_result
            return LegalPackageZipResult(
                layout=layout,
                parse_summary=result.parse_summary,
                import_counts=pkg.counts,
                package_import_id=pkg.package_import_id,
                warnings=[*validation["warnings"], *pkg.warnings],
                package_id=validation.get("package_id"),
                package_version=validation.get("package_version"),
                practice_area=validation.get("practice_area"),
                jurisdiction=validation.get("jurisdiction"),
                ignored_columns=validation.get("ignored_columns") or {},
                s3_uploads=result.s3_uploads,
            )
        finally:
            cleanup_extract_dir(extract_root)

    def _validate_workbook(self, layout: LegalPackageArchiveLayout) -> dict[str, Any]:
        errors: list[str] = []
        warnings: list[str] = []
        parser = LegalPackageExcelParser()
        try:
            package = parser.parse(layout.excel_path)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"Workbook parse failed: {exc}")
            return {
                "errors": errors,
                "warnings": warnings,
                "parse_summary": {},
                "archive_manifest": layout.manifest(),
                "ignored_columns": {},
            }

        warnings.extend(package.parse_warnings)
        warnings.extend(validate_package_graph(package))
        api_errors, api_warnings = validate_question_options_config(package)
        errors.extend(api_errors)
        warnings.extend(api_warnings)
        for sheet, cols in sorted(package.ignored_columns.items()):
            warnings.append(
                f"{sheet}: ignored column(s) not imported — {', '.join(cols)}"
            )
        pkg_row = package.package
        return {
            "errors": errors,
            "warnings": warnings,
            "parse_summary": package.summary(),
            "package": package,
            "package_id": pkg_row.package_id,
            "package_version": str(pkg_row.version) if pkg_row.version else None,
            "practice_area": pkg_row.practice_area,
            "jurisdiction": pkg_row.jurisdiction,
            "archive_manifest": layout.manifest(),
            "ignored_columns": package.ignored_columns,
        }
