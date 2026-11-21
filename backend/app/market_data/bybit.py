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

BYBIT_KLINES_URL = "https://api.bybit.com/v5/market/kline"
BYBIT_INTERVALS = {
    Timeframe.M1: "1",
    Timeframe.M3: "3",
    Timeframe.M5: "5",
    Timeframe.M15: "15",
    Timeframe.M30: "30",
    Timeframe.H1: "60",
    Timeframe.H2: "120",
    Timeframe.H4: "240",
    Timeframe.H6: "360",
    Timeframe.H8: None,
    Timeframe.H12: "720",
    Timeframe.D1: "D",
    Timeframe.D3: None,
    Timeframe.W1: "W",
    Timeframe.MONTH_1: "M",
}
RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


def _candle_close_time(open_time: datetime, interval: Timeframe) -> datetime:
    seconds = interval.seconds
    if seconds is not None:
        return open_time + timedelta(seconds=seconds, milliseconds=-1)
    if interval is Timeframe.MONTH_1:
        year = open_time.year + (1 if open_time.month == 12 else 0)
        month = 1 if open_time.month == 12 else open_time.month + 1
        return datetime(year, month, 1, tzinfo=UTC) - timedelta(milliseconds=1)
    raise ValueError("unsupported interval for Bybit Spot")


class BybitSpotAdapter:
    """Public Bybit V5 Spot OHLCV adapter; market klines need no API key."""

    name = "bybit_spot"

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

    async def _get(self, client: httpx.AsyncClient, params: dict[str, Any]) -> httpx.Response:
        for attempt in range(self._max_retries + 1):
            try:
                response = await client.get(BYBIT_KLINES_URL, params=params)
            except httpx.TransportError:
                if attempt >= self._max_retries:
                    raise
                await self._sleep(min(self._retry_base_delay * (2**attempt), 5.0))
                continue
            if response.status_code not in RETRYABLE_STATUS_CODES or attempt >= self._max_retries:
                return response
            retry_after = response.headers.get("Retry-After")
            try:
                delay = min(max(float(retry_after), 0.0), 30.0) if retry_after else min(
                    self._retry_base_delay * (2**attempt), 5.0
                )
            except ValueError:
                delay = min(self._retry_base_delay * (2**attempt), 5.0)
            await self._sleep(delay)
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
            timeframe = Timeframe(interval)
            provider_interval = BYBIT_INTERVALS[timeframe]
        except (ValueError, KeyError) as exc:
            raise ValueError("unsupported Bybit Spot candle interval") from exc
        if provider_interval is None:
            raise ValueError(f"Bybit Spot does not support the {timeframe.value} interval")
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        if start_time and (start_time.tzinfo is None or start_time.utcoffset() is None):
            raise ValueError("start_time must be timezone-aware")
        if end_time and (end_time.tzinfo is None or end_time.utcoffset() is None):
            raise ValueError("end_time must be timezone-aware")
        if start_time and end_time and start_time > end_time:
            raise ValueError("start_time must be <= end_time")

        params: dict[str, Any] = {
            "category": "spot",
            "symbol": normalized_symbol,
            "interval": provider_interval,
            "limit": limit,
        }
        if start_time:
            params["start"] = int(start_time.astimezone(UTC).timestamp() * 1000)
        if end_time:
            params["end"] = int(end_time.astimezone(UTC).timestamp() * 1000)

        client = self._client
        own_client = client is None
        if own_client:
            client = httpx.AsyncClient(timeout=self._timeout)
        try:
            response = await self._get(client, params)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise MarketDataProviderError("Bybit request failed or returned invalid JSON") from exc
        finally:
            if own_client and client is not None:
                await client.aclose()

        if not isinstance(payload, dict) or payload.get("retCode") != 0:
            raise MarketDataProviderError("Bybit returned an unsuccessful market-data response")
        result = payload.get("result")
        rows = result.get("list") if isinstance(result, dict) else None
        if not isinstance(rows, list):
            raise MarketDataProviderError("Bybit returned an unexpected candle payload")

        candles: list[Candle] = []
        try:
            now = self._clock()
            if now.tzinfo is None or now.utcoffset() is None:
                raise ValueError("adapter clock must return a timezone-aware datetime")
            now = now.astimezone(UTC)
            for row in rows:
                if not isinstance(row, list) or len(row) < 6:
                    raise ValueError("invalid Bybit kline row")
                open_time = datetime.fromtimestamp(int(row[0]) / 1000, tz=UTC)
                close_time = _candle_close_time(open_time, timeframe)
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
            candles.sort(key=lambda candle: candle.timestamp)
            return validate_candle_series(candles) if candles else []
        except (ValueError, TypeError, ArithmeticError) as exc:
            raise MarketDataProviderError("Bybit returned invalid candle data") from exc

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

        range_start = start_time.astimezone(UTC)
        cursor_end = end_time.astimezone(UTC)
        collected: dict[datetime, Candle] = {}
        while cursor_end >= range_start and len(collected) < max_candles:
            remaining = max_candles - len(collected)
            page = await self.fetch_ohlcv(
                symbol,
                interval,
                limit=min(page_limit, remaining),
                start_time=range_start,
                end_time=cursor_end,
            )
            page = [candle for candle in page if range_start <= candle.timestamp <= cursor_end]
            if not page:
                break
            new_candles = [candle for candle in page if candle.timestamp not in collected]
            if not new_candles:
                raise MarketDataProviderError("Bybit historical pagination made no backward progress")
            for candle in new_candles:
                if len(collected) >= max_candles:
                    break
                collected[candle.timestamp] = candle
            oldest = min(candle.timestamp for candle in page)
            if len(page) < min(page_limit, remaining) or oldest <= range_start or len(collected) >= max_candles:
                break
            next_end = oldest - timedelta(milliseconds=1)
            if next_end >= cursor_end:
                raise MarketDataProviderError("Bybit historical pagination cursor did not move backward")
            cursor_end = next_end

        return validate_candle_series(sorted(collected.values(), key=lambda item: item.timestamp)) if collected else []
