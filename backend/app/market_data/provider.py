from datetime import datetime
from typing import Protocol

from app.market_data.candle import Candle


class MarketDataProvider(Protocol):
    """Provider-neutral contract for normalized OHLCV data."""

    async def fetch_ohlcv(
        self,
        symbol: str,
        interval: str = "1h",
        *,
        limit: int = 500,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> list[Candle]:
        """Return candles in chronological order using the shared Candle model."""
