"""Post-ingest row counts via RDSRepository (asyncpg)."""

from __future__ import annotations

import asyncio

from app.services.aim_factory import make_rds_repo

QUERIES = {
    "package_imports": "SELECT count(1)::int AS n FROM configuration.package_imports",
    "questions": "SELECT count(1)::int AS n FROM configuration.questions",
    "workflow_definitions": "SELECT count(1)::int AS n FROM configuration.workflow_definitions",
    "workflow_questions": "SELECT count(1)::int AS n FROM configuration.workflow_questions",
    "document_templates": "SELECT count(1)::int AS n FROM configuration.document_templates",
    "document_rules": "SELECT count(1)::int AS n FROM configuration.document_rules",
    "kb_vectors": "SELECT count(1)::int AS n FROM knowledge.kb_vectors",
    "states (TX)": ("SELECT code FROM configuration.states WHERE code = $1", "TX"),
}


async def main() -> None:
    repo = make_rds_repo()
    if repo is None:
        print("RDS not configured")
        return
    try:
        for label, sql in QUERIES.items():
            if isinstance(sql, tuple):
                query, *params = sql
                rows = await repo.fetch(query, *params)
                print(label, ":", rows)
            else:
                n = await repo.fetch_val(sql)
                print(label, ":", n)
    finally:
        await repo.close()


if __name__ == "__main__":
    asyncio.run(main())
