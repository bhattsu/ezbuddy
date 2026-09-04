"""End-to-end legal package ingestion: Excel → S3 assets → RDS (AIM asyncpg)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from app.adapters.data_sources.AIM_rds import RDSRepository
from app.core.ingestion.excel.import_context import ImportGeography
from app.core.ingestion.excel.importer import ImportResult, LegalPackageRDSImporter
from app.core.ingestion.excel.mappers import validate_package_graph
from app.core.ingestion.excel.parser import LegalPackageExcelParser
from app.core.ingestion.excel.schemas import ExcelPackage
from app.core.ingestion.package_archive import LegalPackageArchiveLayout
from app.core.ingestion.package_s3 import PackageS3UploadResult, upload_package_assets
from app.services.aim_factory import make_rds_repo


@dataclass
class IngestionPipelineResult:
    parse_summary: dict
    import_result: ImportResult
    archive_manifest: dict | None = None
    s3_uploads: int = 0


class LegalPackageIngestionPipeline:
    """Parse workbook, upload ZIP assets to S3, load into PostgreSQL."""

    def __init__(self, geography: ImportGeography | None = None) -> None:
        self.geography = geography

    async def run(
        self,
        excel_path: str | Path,
        *,
        rds: RDSRepository,
        layout: LegalPackageArchiveLayout | None = None,
        package: ExcelPackage | None = None,
        upload_s3: bool = True,
    ) -> IngestionPipelineResult:
        path = Path(excel_path)
        parsed_here = package is None
        if package is None:
            package = LegalPackageExcelParser().parse(path)
        if layout is not None:
            package.source_path = (
                f"zip://{layout.archive_filename}/{layout.excel_relative_path}"
            )
        elif not package.source_path:
            package.source_path = str(path.resolve())

        s3_assets = PackageS3UploadResult()
        if upload_s3 and layout is not None:
            from app.config.settings import settings

            strict = bool((settings.BUCKET_NAME or "").strip())
            s3_assets = await upload_package_assets(layout, package, strict=strict)
            if s3_assets.errors and strict:
                raise ValueError(
                    "Package S3 upload incomplete:\n- " + "\n- ".join(s3_assets.errors[:20])
                )

        importer = LegalPackageRDSImporter(
            rds,
            self.geography,
            bundle_root=layout.root_dir if layout else None,
            s3_assets=s3_assets,
        )
        import_result = await importer.import_package(package)
        if parsed_here:
            import_result.warnings = validate_package_graph(package) + import_result.warnings

        return IngestionPipelineResult(
            parse_summary=package.summary(),
            import_result=import_result,
            archive_manifest=layout.manifest() if layout else None,
            s3_uploads=s3_assets.uploaded,
        )


def main() -> None:
    import sys

    if len(sys.argv) < 2:
        print("Usage: python -m app.core.ingestion.pipeline <path-to.xlsx-or-extracted-root>")
        raise SystemExit(1)

    async def _run() -> None:
        rds = make_rds_repo()
        if rds is None:
            print("RDS not configured (set RDS_* in .env)")
            raise SystemExit(1)
        try:
            result = await LegalPackageIngestionPipeline().run(sys.argv[1], rds=rds)
            print("Parse:", result.parse_summary)
            print("Import counts:", result.import_result.counts)
            print("S3 uploads:", result.s3_uploads)
            if result.import_result.warnings:
                print("Warnings:", *result.import_result.warnings[:20], sep="\n  ")
        finally:
            await rds.close()

    asyncio.run(_run())


if __name__ == "__main__":
    main()
