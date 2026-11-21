"""Explicit maintenance commands for durable market-data records."""

import argparse
import asyncio

from app.database import close_database
from app.market_data.liquidation_repository import delete_expired_liquidation_events
from app.settings import settings


async def prune_liquidations() -> int:
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is required for liquidation retention cleanup")
    deleted = await delete_expired_liquidation_events(settings.liquidation_retention_days)
    await close_database()
    return int(deleted or 0)


def main() -> None:
    parser = argparse.ArgumentParser(description="ChartAnalyzer database maintenance")
    parser.add_argument(
        "command",
        choices=["prune-liquidations"],
        help="Explicitly remove liquidation events older than LIQUIDATION_RETENTION_DAYS",
    )
    args = parser.parse_args()
    if args.command == "prune-liquidations":
        deleted = asyncio.run(prune_liquidations())
        print(f"Deleted {deleted} expired liquidation event(s).")


if __name__ == "__main__":
    main()
