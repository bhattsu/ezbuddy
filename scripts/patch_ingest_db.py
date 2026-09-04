"""Apply Excel-ingest DDL missing from an older init_local_db run (package_imports, workflow columns)."""

from __future__ import annotations

from sqlalchemy import create_engine, inspect, text

from app.config.settings import settings
from app.core.db.base import Base
import app.core.db.models  # noqa: F401
from app.core.db.models.configuration.package_imports import PackageImport


def main() -> None:
    engine = create_engine(settings.SYNC_DATABASE_URI)
    insp = inspect(engine)

    if not insp.has_table("package_imports", schema="configuration"):
        PackageImport.__table__.create(bind=engine, checkfirst=True)
        print("Created configuration.package_imports")
    else:
        print("configuration.package_imports already exists")

    cols = {c["name"] for c in insp.get_columns("workflow_definitions", schema="configuration")}
    with engine.begin() as conn:
        if "workflow_code" not in cols:
            conn.execute(
                text(
                    "ALTER TABLE configuration.workflow_definitions "
                    "ADD COLUMN workflow_code VARCHAR(100)"
                )
            )
            print("Added workflow_definitions.workflow_code")
        if "excel_metadata" not in cols:
            conn.execute(
                text(
                    "ALTER TABLE configuration.workflow_definitions "
                    "ADD COLUMN excel_metadata JSONB"
                )
            )
            print("Added workflow_definitions.excel_metadata")

    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS idx_workflow_def_code "
                "ON configuration.workflow_definitions (workflow_code)"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS idx_package_imports_package_id "
                "ON configuration.package_imports (package_id)"
            )
        )
    print("Ingest DDL patch complete.")


if __name__ == "__main__":
    main()
