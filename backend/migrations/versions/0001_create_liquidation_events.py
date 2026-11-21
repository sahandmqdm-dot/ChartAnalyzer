"""Create durable liquidation event storage.

Revision ID: 0001_liquidation_events
Revises:
"""
import sqlalchemy as sa
from alembic import op

revision = "0001_liquidation_events"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "liquidation_events",
        sa.Column("event_id", sa.String(length=24), primary_key=True),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("symbol", sa.String(length=30), nullable=False),
        sa.Column("event_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("liquidation_side", sa.String(length=8), nullable=False),
        sa.Column("order_side", sa.String(length=4), nullable=False),
        sa.Column("quantity", sa.Numeric(38, 18), nullable=False),
        sa.Column("price", sa.Numeric(38, 18), nullable=False),
        sa.Column("notional", sa.Numeric(38, 18), nullable=False),
        sa.Column("price_kind", sa.String(length=24), nullable=False),
    )
    op.create_index(
        "ix_liquidation_events_provider_symbol_time",
        "liquidation_events",
        ["provider", "symbol", "event_time"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_liquidation_events_provider_symbol_time", table_name="liquidation_events"
    )
    op.drop_table("liquidation_events")
