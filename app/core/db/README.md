# Database layer

Source of truth: `us_legal_pro_install_all.sql` (32 tables, 7 schemas). Ingest adds `configuration.package_imports` (Alembic `0002` + ORM model).

## Connectivity (one path per use case — do not duplicate)

| Use case | Module | Notes |
|----------|--------|--------|
| API / chat reads (async SQL) | `AIM_rds.RDSRepository` + `app.services.aim_factory` | Same pool as `require_rds_repo`; use `LegalFilingRepository(rds=…)` |
| Excel package ingest (async SQL writes) | `AIM_rds.RDSRepository` via `LegalPackageRDSImporter` | Same pool as chat; one transaction per package |
| Local ORM bootstrap / seeds | `app.core.db.sync_ingestion` | `init_local_db`, `seed_all` only (create_all / sample data) |
| Legacy RAG async ORM | `app.config.database.get_db()` | Not used for filing chat or package ingest |
| Schema checks before ingest | `app.services.rds_schema` | Uses `RDSRepository`, not SQLAlchemy |

**Do not add** new `create_engine()` / `RDSRepository(...)` calls in `app/core/db/` — use the modules above.

## Overlap audit (what already exists)

| Area | Status |
|------|--------|
| `app/core/db/repositories/*` | Async SQLAlchemy CRUD helpers for `get_db()` — **not wired** to filing chat today. Chat uses `LegalFilingRepository` + raw SQL via AIM_rds. Do not re-implement the same queries here. |
| `app/config/database.py` vs `sync_ingestion.py` | Async ORM vs sync ORM for bootstrap/seeds. Package ingest uses AIM, not either. |
| `migrations/0001` vs `init_local_db.py` | Both create schemas/extensions. Local dev: prefer `init_local_db.py` (ORM tables + pgvector fallback). RDS with install SQL: `alembic stamp head`. |
| `migrations/0002` vs ORM `PackageImport` / `workflow_code` | Migration for Alembic upgrades; ORM must stay in sync so `create_all` matches. Register new models in `app/core/db/models/__init__.py`. |
| `LegalFilingRepository` vs `ConfigurationRepository` | Same domain reads; filing path is **LegalFilingRepository** only for product code. |
| pgvector `VectorStoreConfig` | **Single builder:** `app.config.pgvector_store` (used by RAG deps, `retrieval_service`, document `ingestion` endpoint). Legacy dict helper `settings.get_vector_store_config()` is unused — prefer deleting or delegating to `pgvector_store`. |
| Vector store adapter caches | Three separate caches (`dependencies.get_vector_store`, `ingestion._get_vector_store_adapter`, `RetrievalService._vector_store_cache`) — same connect logic, different lifetimes; optional future: one factory module. |

## Bootstrap (local)

```bash
python scripts/init_local_db.py
# If DB was bootstrapped before package_imports existed, re-run init or: alembic upgrade head
```

## Alembic (existing RDS)

1. `alembic upgrade head` — `0001` schemas/extensions; `0002` ingest columns/tables.
2. If tables already exist from SQL install: stamp at appropriate revision.

## Regenerate models

`python scripts/generate_db_models.py`

## Seeds

`python -m app.core.db.seeds.seed_all` (uses `sync_ingestion`, idempotent sample TX data).
