from fastapi import APIRouter, HTTPException
import logging
from typing import Dict

logger = logging.getLogger(__name__)
router = APIRouter()

@router.get(
    "/",
    tags=["Health"],
    summary="Health check endpoint (alias for liveness)"
)
async def health_check():
    """Check if the service is running (liveness). Prefer /health/live and /health/ready for orchestration."""
    return {
        "status": "healthy",
        "service": "us-legal-pro-filing-chat",
        "version": "1.0.0"
    }

@router.get(
    "/live",
    tags=["Health"],
    summary="Liveness probe"
)
async def health_live():
    """Returns 200 if the process is up. No dependency checks."""
    return {"status": "ok"}

@router.get(
    "/ready",
    tags=["Health"],
    summary="Readiness probe"
)
async def health_ready():
    """Returns 200 if the app is ready to accept traffic (config valid, optional dependency checks)."""
    from app.config.settings import get_settings
    from app.adapters.file_extraction.factory import ExtractorFactory

    settings = get_settings()
    checks: Dict[str, str] = {"config": "ok", "extractor": "ok"}
    failed = []

    try:
        ExtractorFactory.create()
    except Exception as e:
        logger.warning(f"Readiness check failed: {e}")
        checks["extractor"] = str(e)
        failed.append("extractor")

    if settings.HEALTH_CHECK_S3 and settings.BUCKET_NAME:
        try:
            import boto3
            s3 = boto3.client("s3", region_name=settings.AWS_REGION)
            s3.head_bucket(Bucket=settings.BUCKET_NAME)
            checks["s3"] = "ok"
        except Exception as e:
            logger.warning(f"S3 health check failed: {e}")
            checks["s3"] = str(e)
            failed.append("s3")

    if settings.HEALTH_CHECK_BEDROCK:
        try:
            import boto3
            bedrock = boto3.client("bedrock", region_name=settings.AWS_REGION)
            bedrock.list_foundation_models(maxResults=1)
            checks["bedrock"] = "ok"
        except Exception as e:
            logger.warning(f"Bedrock health check failed: {e}")
            checks["bedrock"] = str(e)
            failed.append("bedrock")

    if failed:
        raise HTTPException(
            status_code=503,
            detail={"status": "not_ready", "checks": checks, "failed": failed},
        )
    return {"status": "ready", "checks": checks}
