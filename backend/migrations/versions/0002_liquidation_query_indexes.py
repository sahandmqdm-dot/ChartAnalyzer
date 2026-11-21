"""Add indexes for heatmap queries and retention cleanup.

Revision ID: 0002_liquidation_query_indexes
Revises: 0001_liquidation_events
"""
from alembic import op

revision = "0002_liquidation_query_indexes"
down_revision = "0001_liquidation_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_liquidation_events_symbol_time_side",
        "liquidation_events",
        ["symbol", "event_time", "liquidation_side"],
    )
    op.create_index(
        "ix_liquidation_events_event_time",
        "liquidation_events",
        ["event_time"],
    )


def downgrade() -> None:
    op.drop_index("ix_liquidation_events_event_time", table_name="liquidation_events")
    op.drop_index("ix_liquidation_events_symbol_time_side", table_name="liquidation_events")
