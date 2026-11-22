from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx

from app.market_data.candle import Candle
from app.market_data.provider import MarketDataProviderError
from app.market_data.quality import validate_candle_series
from app.market_data.symbol import Symbol
from app.market_data.timeframe import Timeframe

BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"
SUPPORTED_INTERVALS = frozenset(item.value for item in Timeframe)
RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


class BinanceSpotAdapter:
    """Public Binance Spot OHLCV adapter; no API key is required for klines."""

    name = "binance_spot"

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        timeout: float = 10.0,
        max_retries: int = 2,
        retry_base_delay: float = 0.25,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ):
        if max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        if retry_base_delay < 0:
            raise ValueError("retry_base_delay must be >= 0")
        self._client = client
        self._timeout = timeout
        self._max_retries = max_retries
        self._retry_base_delay = retry_base_delay
        self._sleep = sleep
        self._clock = clock

    def _retry_delay(self, response: httpx.Response, attempt: int) -> float:
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return min(max(float(retry_after), 0.0), 30.0)
            except ValueError:
                pass
        return min(self._retry_base_delay * (2**attempt), 5.0)

    async def _get_with_retries(
        self,
        client: httpx.AsyncClient,
        params: dict[str, Any],
    ) -> httpx.Response:
        for attempt in range(self._max_retries + 1):
            try:
                response = await client.get(BINANCE_KLINES_URL, params=params)
            except httpx.TransportError:
                if attempt >= self._max_retries:
                    raise
                await self._sleep(min(self._retry_base_delay * (2**attempt), 5.0))
                continue

            if response.status_code not in RETRYABLE_STATUS_CODES or attempt >= self._max_retries:
                return response
            await self._sleep(self._retry_delay(response, attempt))
        raise RuntimeError("unreachable retry state")

    async def fetch_ohlcv(
        self,
        symbol: str,
        interval: str = "1h",
        *,
        limit: int = 500,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> list[Candle]:
        normalized_symbol = str(Symbol(symbol))
        try:
            normalized_interval = Timeframe(interval).value
        except ValueError as exc:
            raise ValueError("unsupported Binance candle interval") from exc
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        if start_time and (start_time.tzinfo is None or start_time.utcoffset() is None):
            raise ValueError("start_time must be timezone-aware")
        if end_time and (end_time.tzinfo is None or end_time.utcoffset() is None):
            raise ValueError("end_time must be timezone-aware")
        if start_time and end_time and start_time > end_time:
            raise ValueError("start_time must be <= end_time")

        params: dict[str, Any] = {"symbol": normalized_symbol, "interval": normalized_interval, "limit": limit}
        if start_time:
            params["startTime"] = int(start_time.astimezone(UTC).timestamp() * 1000)
        if end_time:
            params["endTime"] = int(end_time.astimezone(UTC).timestamp() * 1000)

        client = self._client
        own_client = client is None
        if own_client:
            client = httpx.AsyncClient(timeout=self._timeout)
        try:
            response = await self._get_with_retries(client, params)
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
            now = self._clock()
            if now.tzinfo is None or now.utcoffset() is None:
                raise ValueError("adapter clock must return a timezone-aware datetime")
            now = now.astimezone(UTC)
            for row in payload:
                if not isinstance(row, list) or len(row) < 7:
                    raise ValueError("invalid kline row")
                open_time = datetime.fromtimestamp(int(row[0]) / 1000, tz=UTC)
                close_time = datetime.fromtimestamp(int(row[6]) / 1000, tz=UTC)
                candles.append(
                    Candle(
                        timestamp=open_time,
                        close_time=close_time,
                        is_closed=close_time < now,
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
        """Fetch a bounded historical range across sequential Binance Kline pages."""
        if start_time.tzinfo is None or start_time.utcoffset() is None:
            raise ValueError("start_time must be timezone-aware")
        if end_time.tzinfo is None or end_time.utcoffset() is None:
            raise ValueError("end_time must be timezone-aware")
        if start_time > end_time:
            raise ValueError("start_time must be <= end_time")
        if not 1 <= page_limit <= 1000:
            raise ValueError("page_limit must be between 1 and 1000")
        if max_candles < 1:
            raise ValueError("max_candles must be >= 1")

        range_end = end_time.astimezone(UTC)
        cursor = start_time.astimezone(UTC)
        candles: list[Candle] = []
        last_timestamp: datetime | None = None

        while cursor <= range_end and len(candles) < max_candles:
            remaining = max_candles - len(candles)
            page = await self.fetch_ohlcv(
                symbol,
                interval,
                limit=min(page_limit, remaining),
                start_time=cursor,
                end_time=range_end,
            )
            if not page:
                break

            range_start = start_time.astimezone(UTC)
            new_candles = [
                candle
                for candle in page
                if candle.timestamp >= range_start
                and (last_timestamp is None or candle.timestamp > last_timestamp)
                and candle.timestamp <= range_end
            ]
            if not new_candles:
                raise MarketDataProviderError(
                    "Binance historical pagination made no forward progress"
                )

            candles.extend(new_candles[:remaining])
            last_timestamp = candles[-1].timestamp

            if len(page) < min(page_limit, remaining) or len(candles) >= max_candles:
                break

            if last_timestamp is None or candles[-1].close_time is None:
                raise MarketDataProviderError(
                    "Binance candle close time is required for historical pagination"
                )
            next_cursor = candles[-1].close_time + timedelta(milliseconds=1)
            if next_cursor <= cursor:
                raise MarketDataProviderError(
                    "Binance historical pagination cursor did not advance"
                )
            cursor = next_cursor

        return validate_candle_series(candles) if candles else []
