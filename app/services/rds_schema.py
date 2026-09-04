"""RDS schema checks for legal package ingest (asyncpg via AIM_rds)."""

from __future__ import annotations

from app.adapters.data_sources.AIM_rds import RDSRepository


async def configuration_table_exists(repo: RDSRepository, table: str) -> bool:
    row = await repo.fetch_one(
        """
        SELECT EXISTS (
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = 'configuration'
              AND table_name = $1
        ) AS ok
        """,
        table,
    )
    return bool(row and row.get("ok"))


async def assert_package_ingest_schema(repo: RDSRepository) -> None:
    """Raise if tables required for Excel/ZIP package ingest are missing."""
    required = (
        "package_imports",
        "workflow_definitions",
        "questions",
        "document_templates",
    )
    missing: list[str] = []
    for table in required:
        if not await configuration_table_exists(repo, table):
            missing.append(f"configuration.{table}")
    if missing:
        raise ValueError(
            "Database schema not ready for package ingest. Missing: "
            + ", ".join(missing)
            + ". Run scripts/init_local_db.py or alembic upgrade head."
        )
