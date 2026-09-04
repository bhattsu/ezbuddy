"""Legal configuration package ingestion (Excel ZIP → PostgreSQL)."""

import logging
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile

from app.api.dependencies.auth import get_optional_auth
from app.api.dependencies.rate_limit import limiter, rate_limit_string
from app.api.dependencies.rds import require_rds_repo
from app.api.schemas.legal_package_ingestion import (
    LegalPackageIngestionResponse,
    LegalPackageValidationResponse,
)
from app.api.schemas.rag import ErrorResponse
from app.adapters.data_sources.AIM_rds import RDSRepository
from app.core.ingestion.excel.import_context import ImportGeography
from app.services.legal_package_ingestion_service import LegalPackageIngestionService
from app.utils.file_utils import FileValidator

logger = logging.getLogger(__name__)

router = APIRouter()


def get_legal_package_ingestion_service() -> LegalPackageIngestionService:
    return LegalPackageIngestionService()


def _geography_from_form(
    state_code: Optional[str],
    state_name: Optional[str],
    county_name: Optional[str],
    county_code: Optional[str],
    jurisdiction_code: Optional[str],
    jurisdiction_name: Optional[str],
) -> ImportGeography | None:
    if not state_code and not jurisdiction_code:
        return None
    return ImportGeography(
        state_code=(state_code or "TX").upper(),
        state_name=state_name or state_code or "Texas",
        county_name=county_name or "Travis",
        county_code=county_code,
        jurisdiction_code=jurisdiction_code or f"{state_code or 'TX'}-DEFAULT",
        jurisdiction_name=jurisdiction_name or jurisdiction_code or "Default Court",
    )


async def _read_zip_upload(file: UploadFile) -> tuple[bytes, str]:
    await FileValidator.validate_file_size(file)
    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise ValueError("Upload must be a .zip archive")
    zip_bytes = await file.read()
    if not zip_bytes:
        raise ValueError("Uploaded zip is empty")
    return zip_bytes, file.filename


@router.post(
    "/validate",
    response_model=LegalPackageValidationResponse,
    responses={400: {"model": ErrorResponse}},
    summary="Validate legal package ZIP (no database write)",
    description=(
        "Extract ZIP, locate the configuration workbook and folders (documents, knowledge, assets), "
        "parse all sheets, and return cross-sheet validation warnings without importing."
    ),
)
@limiter.limit(rate_limit_string)
async def validate_legal_package_zip(
    request: Request,
    file: Annotated[
        UploadFile,
        File(
            description=(
                "Required .zip package. In Swagger: click Choose file (do not use the default text)."
            ),
        ),
    ],
    _auth: Optional[Any] = Depends(get_optional_auth),
    service: LegalPackageIngestionService = Depends(get_legal_package_ingestion_service),
):
    try:
        zip_bytes, filename = await _read_zip_upload(file)

        result = await service.validate_zip(zip_bytes, filename)
        errors = result.get("errors") or []
        warnings = result.get("warnings") or []
        valid = len(errors) == 0
        return LegalPackageValidationResponse(
            valid=valid,
            message="Package is valid" if valid else "Package validation failed",
            package_id=result.get("package_id"),
            parse_summary=result.get("parse_summary") or {},
            warnings=warnings,
            errors=errors,
            archive_manifest=result.get("archive_manifest") or {},
        )
    except ValueError as exc:
        logger.warning("Legal package validation rejected: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Legal package validation failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post(
    "/zip",
    response_model=LegalPackageIngestionResponse,
    responses={
        400: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
    summary="Ingest legal package ZIP into RDS",
    description=(
        "Upload a state configuration ZIP. Extracts the archive, uploads documents/ and "
        "knowledge/ assets to S3 (documents-repo/ and knowledge/), validates sheets, and "
        "upserts into PostgreSQL with real s3_bucket/s3_key paths."
    ),
)
@limiter.limit(rate_limit_string)
async def ingest_legal_package_zip(
    request: Request,
    file: Annotated[
        UploadFile,
        File(
            description=(
                "Required .zip package. In Swagger: click Choose file and pick Texas_Divorce_Package.zip "
                "(clear any default text in this field)."
            ),
        ),
    ],
    validate_only: bool = Form(
        False,
        description="If true, parse and validate only (no DB commit / S3 upload)",
    ),
    state_code: Optional[str] = Form(None, description="Optional bootstrap state code (e.g. TX)"),
    state_name: Optional[str] = Form(None),
    county_name: Optional[str] = Form(None),
    county_code: Optional[str] = Form(None),
    jurisdiction_code: Optional[str] = Form(None, description="configuration.jurisdictions.code"),
    jurisdiction_name: Optional[str] = Form(None),
    _auth: Optional[Any] = Depends(get_optional_auth),
    service: LegalPackageIngestionService = Depends(get_legal_package_ingestion_service),
    rds: RDSRepository = Depends(require_rds_repo),
):
    try:
        zip_bytes, filename = await _read_zip_upload(file)

        geography = _geography_from_form(
            state_code,
            state_name,
            county_name,
            county_code,
            jurisdiction_code,
            jurisdiction_name,
        )

        logger.info(
            "Legal package zip upload: filename=%s validate_only=%s geography=%s",
            filename,
            validate_only,
            geography.jurisdiction_code if geography else "default",
        )

        result = await service.ingest_zip(
            zip_bytes,
            filename,
            geography=geography,
            validate_only=validate_only,
            rds=rds,
        )

        if validate_only:
            return LegalPackageIngestionResponse(
                success=True,
                message="Validation successful (no database changes)",
                package_id=result.package_id,
                package_version=result.package_version,
                practice_area=result.practice_area,
                jurisdiction=result.jurisdiction,
                parse_summary=result.parse_summary,
                import_counts={},
                warnings=result.warnings,
                archive_manifest=result.layout.manifest(),
                metadata={"validate_only": True},
            )

        return LegalPackageIngestionResponse(
            success=True,
            message=(
                f"Imported package {result.package_id or 'unknown'} "
                f"({result.import_counts.get('workflow_mappings', 0)} workflow mappings, "
                f"{result.s3_uploads} S3 uploads)"
            ),
            package_id=result.package_id,
            package_version=result.package_version,
            practice_area=result.practice_area,
            jurisdiction=result.jurisdiction,
            parse_summary=result.parse_summary,
            import_counts=result.import_counts,
            package_import_id=str(result.package_import_id) if result.package_import_id else None,
            warnings=result.warnings,
            archive_manifest=result.layout.manifest(),
            metadata={"upsert": True, "s3_uploads": result.s3_uploads},
        )
    except ValueError as exc:
        logger.warning("Legal package ingestion rejected: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Legal package ingestion failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
