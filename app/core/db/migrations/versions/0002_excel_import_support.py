"""Add workflow_code, excel_metadata, and package_imports for Excel ingestion."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0002_excel_import_support"
down_revision: Union[str, None] = "0001_bootstrap"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "workflow_definitions",
        sa.Column("workflow_code", sa.String(length=100), nullable=True),
        schema="configuration",
    )
    op.add_column(
        "workflow_definitions",
        sa.Column("excel_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        schema="configuration",
    )
    op.create_index(
        "idx_workflow_def_code",
        "workflow_definitions",
        ["workflow_code"],
        unique=False,
        schema="configuration",
    )

    op.create_table(
        "package_imports",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("package_id", sa.String(length=100), nullable=False),
        sa.Column("package_version", sa.String(length=50), nullable=True),
        sa.Column("source_path", sa.Text(), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("imported_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        schema="configuration",
    )
    op.create_index(
        "idx_package_imports_package_id",
        "package_imports",
        ["package_id"],
        schema="configuration",
    )


def downgrade() -> None:
    op.drop_index("idx_package_imports_package_id", table_name="package_imports", schema="configuration")
    op.drop_table("package_imports", schema="configuration")
    op.drop_index("idx_workflow_def_code", table_name="workflow_definitions", schema="configuration")
    op.drop_column("workflow_definitions", "excel_metadata", schema="configuration")
    op.drop_column("workflow_definitions", "workflow_code", schema="configuration")
