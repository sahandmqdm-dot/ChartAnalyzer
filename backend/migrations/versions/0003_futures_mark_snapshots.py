"""Create durable futures mark-price and funding snapshots.

Revision ID: 0003_futures_mark_snapshots
Revises: 0002_liquidation_query_indexes
"""
import sqlalchemy as sa
from alembic import op

revision = "0003_futures_mark_snapshots"
down_revision = "0002_liquidation_query_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "futures_mark_snapshots",
        sa.Column("snapshot_id", sa.String(length=64), primary_key=True),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("symbol", sa.String(length=30), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("mark_price", sa.Numeric(38, 18), nullable=False),
        sa.Column("index_price", sa.Numeric(38, 18), nullable=False),
        sa.Column("funding_rate", sa.Numeric(30, 18), nullable=False),
        sa.Column("next_funding_time", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_futures_mark_snapshots_symbol_observed",
        "futures_mark_snapshots",
        ["symbol", "observed_at"],
    )
    op.create_index(
        "ix_futures_mark_snapshots_provider_symbol_time",
        "futures_mark_snapshots",
        ["provider", "symbol", "observed_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_futures_mark_snapshots_provider_symbol_time",
        table_name="futures_mark_snapshots",
    )
    op.drop_index(
        "ix_futures_mark_snapshots_symbol_observed",
        table_name="futures_mark_snapshots",
    )
    op.drop_table("futures_mark_snapshots")
