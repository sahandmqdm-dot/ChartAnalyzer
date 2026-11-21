from datetime import datetime
from typing import Protocol

from app.market_data.candle import Candle


class MarketDataProviderError(RuntimeError):
    """Raised when a market data provider request fails or returns invalid data."""


class MarketDataProvider(Protocol):
    """Provider-neutral contract for normalized OHLCV data."""

    name: str

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

    async def fetch_historical_ohlcv(
        self,
        symbol: str,
        interval: str = "1h",
        *,
        start_time: datetime,
        end_time: datetime,
        page_limit: int = 1000,
        max_candles: int = 10_000,
    ) -> list[Candle]:
        """Return a bounded historical range in chronological order."""
