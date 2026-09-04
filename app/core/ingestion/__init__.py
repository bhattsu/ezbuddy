"""Configuration package ingestion (Excel → typed models → RDS)."""

from app.core.ingestion.pipeline import LegalPackageIngestionPipeline

__all__ = ["LegalPackageIngestionPipeline"]
