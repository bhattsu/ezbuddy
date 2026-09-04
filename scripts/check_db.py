"""Quick RDS connectivity + schema check (uses shared RDSRepository factory)."""

from __future__ import annotations

import asyncio

from app.services.aim_factory import ephemeral_rds_repo
from app.utils.sql_queries import get_sql


async def main() -> None:
    async with ephemeral_rds_repo() as repo:
        if repo is None:
            print("RDS not configured (set RDS_* in .env)")
            return
        schemas = await repo.fetch(get_sql("health.list_schemas"))
        print("namespaces:", [r["nspname"] for r in schemas])
        cfg = await repo.fetch_val(
            get_sql("health.schema_exists"),
            "configuration",
        )
        print("configuration schema:", bool(cfg))


if __name__ == "__main__":
    asyncio.run(main())
