"""Add configuration.case_type_filing_costs for payment authorization amounts."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0003_case_type_filing_costs"
down_revision: Union[str, None] = "0002_excel_import_support"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "case_type_filing_costs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("case_type", sa.String(length=100), nullable=False),
        sa.Column("state_code", sa.String(length=10), nullable=True),
        sa.Column("cost", sa.Numeric(10, 2), nullable=False),
        sa.Column("currency", sa.String(length=3), server_default=sa.text("'USD'"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint("cost >= 0", name="case_type_filing_costs_cost_check"),
        sa.UniqueConstraint(
            "case_type",
            "state_code",
            name="uq_case_type_filing_costs",
        ),
        schema="configuration",
    )
    op.create_index(
        "idx_case_type_filing_costs_active",
        "case_type_filing_costs",
        ["is_active", "state_code", "case_type"],
        schema="configuration",
    )


def downgrade() -> None:
    op.drop_index(
        "idx_case_type_filing_costs_active",
        table_name="case_type_filing_costs",
        schema="configuration",
    )
    op.drop_table("case_type_filing_costs", schema="configuration")
