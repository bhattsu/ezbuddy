"""
Bootstrap schema (if needed) and ingest Excel package into RDS (local or AWS).

Uses RDS_* from environment. Load a separate env file for AWS:

  copy .env.aws.example .env.aws   # fill in values
  python scripts/deploy_package_to_rds.py --env-file .env.aws --excel "path\\Texas_Divorce 1.xlsx"
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_env(env_file: str | None) -> None:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    if env_file:
        path = Path(env_file)
        if not path.is_absolute():
            path = ROOT / path
        if path.is_file():
            load_dotenv(path, override=True)
            print(f"Loaded env from {path}")
        else:
            print(f"WARN: env file not found: {path}")

    from app.config.settings import get_settings

    get_settings.cache_clear()


def _connection_info() -> tuple[str, int, str, str]:
    from app.config.settings import settings

    if not all(
        [settings.rds_host, settings.rds_database, settings.rds_username, settings.rds_password]
    ):
        print("ERROR: Set RDS_HOST, RDS_DATABASE, RDS_USERNAME, RDS_PASSWORD in .env or --env-file")
        sys.exit(1)
    return (
        settings.rds_host,
        settings.rds_port,
        settings.rds_database,
        settings.rds_username,
    )


def _schema_has_configuration_tables() -> bool:
    from sqlalchemy import create_engine, text

    from app.config.settings import settings

    engine = create_engine(settings.SYNC_DATABASE_URI)
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'configuration' AND table_name = 'case_types' LIMIT 1"
            )
        ).fetchone()
        return row is not None


def _test_async_connection() -> bool:
    import asyncio

    from app.services.aim_factory import make_rds_repo

    async def _check() -> bool:
        repo = make_rds_repo()
        if repo is None:
            return False
        try:
            return await repo.check_connection()
        except Exception as exc:  # noqa: BLE001
            print(f"Connection failed: {exc}")
            return False
        finally:
            await repo.close()

    return asyncio.run(_check())


def main() -> None:
    parser = argparse.ArgumentParser(description="Deploy Excel package to RDS PostgreSQL")
    parser.add_argument(
        "--env-file",
        default=".env.aws",
        help="Env file with RDS_* overrides (default: .env.aws)",
    )
    parser.add_argument(
        "--excel",
        default=r"c:\Users\Harih\Downloads\Texas_Divorce 1.xlsx",
        help="Path to legal package .xlsx",
    )
    parser.add_argument(
        "--skip-bootstrap",
        action="store_true",
        help="Skip schema bootstrap (DB already has tables from install_all.sql)",
    )
    parser.add_argument(
        "--skip-alembic",
        action="store_true",
        help="Skip alembic upgrade head",
    )
    parser.add_argument(
        "--connection-only",
        action="store_true",
        help="Only test RDS connectivity",
    )
    args = parser.parse_args()

    _load_env(args.env_file if Path(ROOT / args.env_file).exists() or Path(args.env_file).exists() else None)
    host, port, database, user = _connection_info()
    print(f"Target RDS: {user}@{host}:{port}/{database}")

    if "localhost" in host or host in ("127.0.0.1", "::1"):
        print("WARN: RDS_HOST looks local; use .env.aws for AWS RDS")

    if not _test_async_connection():
        sys.exit(1)
    print("RDS connection OK")

    if args.connection_only:
        return

    excel = Path(args.excel)
    if not excel.is_file():
        print(f"ERROR: Excel not found: {excel}")
        sys.exit(1)

    ran_bootstrap = False
    if not args.skip_bootstrap and not _schema_has_configuration_tables():
        print("Bootstrapping schema (extensions, schemas, ORM tables)...")
        subprocess.check_call(
            [sys.executable, str(ROOT / "scripts" / "init_local_db.py")],
            cwd=str(ROOT),
            env={**dict(**__import__("os").environ), "PYTHONPATH": str(ROOT)},
        )
        ran_bootstrap = True
    elif _schema_has_configuration_tables():
        print("configuration.case_types exists — skipping full bootstrap")
    else:
        print("Skipping bootstrap (--skip-bootstrap)")

    if not args.skip_alembic:
        if ran_bootstrap:
            print("Stamping alembic head (ORM bootstrap includes excel-import columns)...")
            subprocess.check_call(
                ["alembic", "stamp", "head"],
                cwd=str(ROOT),
                env={**dict(**__import__("os").environ), "PYTHONPATH": str(ROOT)},
            )
        else:
            print("Running alembic upgrade head...")
            subprocess.check_call(
                ["alembic", "upgrade", "head"],
                cwd=str(ROOT),
                env={**dict(**__import__("os").environ), "PYTHONPATH": str(ROOT)},
            )

    print(f"Ingesting {excel}...")
    subprocess.check_call(
        [
            sys.executable,
            "-m",
            "app.core.ingestion.pipeline",
            str(excel),
        ],
        cwd=str(ROOT),
        env={**dict(**__import__("os").environ), "PYTHONPATH": str(ROOT)},
    )
    print("Deploy complete.")


if __name__ == "__main__":
    main()
