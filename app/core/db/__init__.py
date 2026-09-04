"""ORM models, repositories, migrations, seeds.

Connectivity: see ``app/core/db/README.md``. Runtime reads + package import → AIM_rds; bootstrap/seeds → ``sync_ingestion``.
"""

from app.core.db.base import Base

__all__ = ["Base"]
