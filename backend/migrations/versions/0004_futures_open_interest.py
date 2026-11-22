"""Create durable USD-M futures open-interest observations.

Revision ID: 0004_futures_open_interest
Revises: 0003_futures_mark_snapshots
"""
import sqlalchemy as sa
from alembic import op

revision = "0004_futures_open_interest"
down_revision = "0003_futures_mark_snapshots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "futures_open_interest",
        sa.Column("observation_id", sa.String(length=64), primary_key=True),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("symbol", sa.String(length=30), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("open_interest", sa.Numeric(38, 18), nullable=False),
    )
    op.create_index(
        "ix_futures_open_interest_symbol_observed",
        "futures_open_interest",
        ["symbol", "observed_at"],
    )
    op.create_index(
        "ix_futures_open_interest_provider_symbol_time",
        "futures_open_interest",
        ["provider", "symbol", "observed_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_futures_open_interest_provider_symbol_time",
        table_name="futures_open_interest",
    )
    op.drop_index(
        "ix_futures_open_interest_symbol_observed",
        table_name="futures_open_interest",
    )
    op.drop_table("futures_open_interest")
