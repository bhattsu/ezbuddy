"""Bootstrap local Postgres: extensions, schemas, ORM tables, Alembic stamp."""

from __future__ import annotations

from sqlalchemy import text

from app.core.db.base import Base
from app.core.db.sync_ingestion import get_sync_ingestion_engine
import app.core.db.models  # noqa: F401


def _create_kb_vectors_without_pgvector(engine) -> None:
    """Dev fallback when pgvector is not installed (embedding stored as REAL[])."""
    with engine.begin() as conn:
        exists = conn.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'knowledge' AND table_name = 'kb_vectors'"
            )
        ).fetchone()
        if exists:
            return
        conn.execute(
            text(
                """
                CREATE TABLE knowledge.kb_vectors (
                    content_id TEXT PRIMARY KEY,
                    content_type TEXT NOT NULL,
                    matter_type TEXT NOT NULL REFERENCES knowledge.matter_types(matter_type_id),
                    jurisdiction_id TEXT NOT NULL REFERENCES knowledge.jurisdictions(jurisdiction_id),
                    state TEXT NOT NULL,
                    county_id TEXT REFERENCES knowledge.counties(county_id),
                    case_variant TEXT,
                    scope TEXT,
                    topics TEXT[],
                    requires_local_verification BOOLEAN DEFAULT FALSE,
                    text TEXT NOT NULL,
                    embedding REAL[],
                    embedding_model TEXT NOT NULL,
                    last_verified DATE,
                    created_at TIMESTAMPTZ DEFAULT now(),
                    text_search TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', text)) STORED
                )
                """
            )
        )
        conn.execute(
            text(
                "CREATE INDEX idx_kb_vectors_filters ON knowledge.kb_vectors "
                "(matter_type, jurisdiction_id, county_id, content_type)"
            )
        )
        conn.execute(
            text("CREATE INDEX idx_kb_vectors_fts ON knowledge.kb_vectors USING GIN (text_search)")
        )
    vs = Base.metadata.tables["knowledge.vector_sources"]
    vs.create(bind=engine, checkfirst=True)


def main() -> None:
    engine = get_sync_ingestion_engine()
    has_vector = False

    with engine.connect() as conn:
        ac = conn.execution_options(isolation_level="AUTOCOMMIT")
        ac.execute(text("CREATE SCHEMA IF NOT EXISTS public"))
        ac.execute(text("GRANT ALL ON SCHEMA public TO public"))
        ac.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
        avail = ac.execute(
            text("SELECT 1 FROM pg_available_extensions WHERE name = 'vector'")
        ).fetchone()
        if avail:
            ac.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            has_vector = True
        else:
            print("WARN: pgvector extension is not installed on this PostgreSQL server.")

    with engine.begin() as conn:
        for schema in (
            "configuration",
            "operational",
            "integration",
            "workflow",
            "documents",
            "conversations",
            "knowledge",
        ):
            conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))

    if not has_vector:
        print(
            "Knowledge tables need pgvector. Install it, then re-run this script, "
            "or run us_legal_pro_install_all.sql in DBeaver after enabling pgvector."
        )
        tables = [
            t
            for t in Base.metadata.sorted_tables
            if not (
                t.schema == "knowledge"
                and t.name in ("kb_vectors", "vector_sources")
            )
        ]
        Base.metadata.create_all(bind=engine, tables=tables)
        _create_kb_vectors_without_pgvector(engine)
    else:
        Base.metadata.create_all(bind=engine)
    print("Tables created from ORM metadata.")

    with engine.connect() as conn:
        n = conn.execute(
            text(
                "SELECT count(1) FROM information_schema.tables "
                "WHERE table_schema NOT IN ('pg_catalog', 'information_schema')"
            )
        ).scalar()
        print("Total user tables:", n)


if __name__ == "__main__":
    main()
