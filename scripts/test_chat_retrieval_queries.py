"""Smoke-test chat retrieval SQL against SYNC_DATABASE_URI."""

from __future__ import annotations

import asyncio

from sqlalchemy import create_engine, text

from app.config.settings import settings
from app.services import legal_filing_queries as q
from app.services.aim_factory import make_rds_repo


def _run_sync(name: str, sql: str, *params: object) -> None:
    engine = create_engine(settings.SYNC_DATABASE_URI)
    bind = tuple(params) if params else None
    with engine.connect() as conn:
        rows = conn.execute(text(sql), bind).mappings().all()
    print(f"{name}: {len(rows)} row(s)")
    if rows:
        print("  sample:", dict(rows[0]))


def main_sync() -> None:
    _run_sync("1 case types", q.SQL_FETCH_CASE_TYPES)
    _run_sync("2 states", q.SQL_FETCH_STATES)
    _run_sync("3 counties TX", q.SQL_FETCH_COUNTIES_BY_STATE, "TX")
    _run_sync("4 jurisdictions", q.SQL_FETCH_JURISDICTIONS, None)
    _run_sync(
        "5 workflow",
        q.SQL_FETCH_ACTIVE_WORKFLOW,
        "Divorce",
        "With Children",
        None,
    )
    _run_sync(
        "6 questions",
        q.SQL_FETCH_WORKFLOW_QUESTIONS,
        "Divorce",
        "With Children",
        None,
    )
    _run_sync(
        "7 templates",
        q.SQL_FETCH_DOCUMENT_TEMPLATES_FOR_WORKFLOW,
        "Divorce",
        "With Children",
        None,
    )
    _run_sync(
        "8 template s3",
        q.SQL_FETCH_DOCUMENT_TEMPLATE,
        "Divorce",
        "With Children",
        "petition",
        None,
    )


async def main_async_repo() -> None:
    rds = make_rds_repo()
    if rds is None:
        print("RDS not configured")
        return
    from app.services.legal_filing_repository import LegalFilingRepository

    repo = LegalFilingRepository(rds)
    types = await repo.get_case_types()
    print("repo case types:", len(types))
    j = await repo.get_jurisdictions()
    print("repo jurisdictions:", len(j))
    schema = await repo.get_user_detail_schema("Divorce", "With Children")
    print("repo fields:", len(schema.get("_fields") or []))
    opts = await repo.get_document_type_options("Divorce", "With Children")
    print("repo doc options:", opts)
    tpl = await repo.get_document_template("Divorce", "With Children", "petition")
    print("repo template s3:", tpl.get("s3_key") if tpl else None)
    await rds.close()


if __name__ == "__main__":
    print("=== async repo (asyncpg $1 params) ===")
    asyncio.run(main_async_repo())
