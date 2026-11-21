from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx

from app.market_data.candle import Candle
from app.market_data.quality import validate_candle_series

BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"
SUPPORTED_INTERVALS = frozenset({"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d", "3d", "1w", "1M"})


class MarketDataProviderError(RuntimeError):
    """Raised when a market data provider request fails or returns invalid data."""


class BinanceSpotAdapter:
    """Public Binance Spot OHLCV adapter; no API key is required for klines."""

    def __init__(self, client: httpx.AsyncClient | None = None, *, timeout: float = 10.0):
        self._client = client
        self._timeout = timeout

    async def fetch_ohlcv(
        self,
        symbol: str,
        interval: str = "1h",
        *,
        limit: int = 500,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> list[Candle]:
        normalized_symbol = symbol.strip().upper()
        if not normalized_symbol or not normalized_symbol.isalnum():
            raise ValueError("symbol must contain only letters and numbers")
        if interval not in SUPPORTED_INTERVALS:
            raise ValueError("unsupported Binance candle interval")
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        if start_time and (start_time.tzinfo is None or start_time.utcoffset() is None):
            raise ValueError("start_time must be timezone-aware")
        if end_time and (end_time.tzinfo is None or end_time.utcoffset() is None):
            raise ValueError("end_time must be timezone-aware")
        if start_time and end_time and start_time > end_time:
            raise ValueError("start_time must be <= end_time")

        params: dict[str, Any] = {"symbol": normalized_symbol, "interval": interval, "limit": limit}
        if start_time:
            params["startTime"] = int(start_time.astimezone(UTC).timestamp() * 1000)
        if end_time:
            params["endTime"] = int(end_time.astimezone(UTC).timestamp() * 1000)

        client = self._client
        own_client = client is None
        if own_client:
            client = httpx.AsyncClient(timeout=self._timeout)
        try:
            response = await client.get(BINANCE_KLINES_URL, params=params)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise MarketDataProviderError("Binance request failed or returned invalid JSON") from exc
        finally:
            if own_client and client is not None:
                await client.aclose()

        if not isinstance(payload, list):
            raise MarketDataProviderError("Binance returned an unexpected payload")
        candles: list[Candle] = []
        try:
            for row in payload:
                if not isinstance(row, list) or len(row) < 6:
                    raise ValueError("invalid kline row")
                candles.append(
                    Candle(
                        timestamp=datetime.fromtimestamp(int(row[0]) / 1000, tz=UTC),
                        open=Decimal(str(row[1])),
                        high=Decimal(str(row[2])),
                        low=Decimal(str(row[3])),
                        close=Decimal(str(row[4])),
                        volume=Decimal(str(row[5])),
                    )
                )
            return validate_candle_series(candles) if candles else []
        except (ValueError, TypeError, ArithmeticError) as exc:
            raise MarketDataProviderError("Binance returned invalid candle data") from exc
