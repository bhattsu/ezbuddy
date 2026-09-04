"""Extensions and schemas only (safe on existing RDS).

Tables already created from us_legal_pro_install_all.sql are NOT recreated here.
After this revision: ``alembic stamp head`` if tables exist, or ``alembic upgrade head`` on fresh DB
then run install SQL / autogenerate follow-ups as needed.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0001_bootstrap"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMAS = (
    "configuration",
    "operational",
    "integration",
    "workflow",
    "documents",
    "conversations",
    "knowledge",
)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    for schema in SCHEMAS:
        op.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")


def downgrade() -> None:
    pass
