from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx

from app.market_data.candle import Candle
from app.market_data.provider import MarketDataProviderError
from app.market_data.quality import validate_candle_series
from app.market_data.symbol import Symbol
from app.market_data.timeframe import Timeframe

NOBITEX_OHLC_URL = "https://apiv2.nobitex.ir/market/udf/history"
NOBITEX_RESOLUTIONS = {
    Timeframe.M1: "1",
    Timeframe.M5: "5",
    Timeframe.M15: "15",
    Timeframe.M30: "30",
    Timeframe.H1: "60",
    Timeframe.H4: "240",
    Timeframe.H6: "360",
    Timeframe.H12: "720",
    Timeframe.D1: "D",
    Timeframe.D3: "3D",
}
MAX_PAGE_SIZE = 500


def _close_time(open_time: datetime, timeframe: Timeframe) -> datetime:
    seconds = timeframe.seconds
    if seconds is None:
        raise ValueError("unsupported Nobitex interval")
    return open_time + timedelta(seconds=seconds, milliseconds=-1)


class NobitexSpotAdapter:
    """Public Nobitex Spot OHLCV adapter using the documented TradingView UDF endpoint."""

    name = "nobitex_spot"

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        timeout: float = 10.0,
        clock=lambda: datetime.now(UTC),
    ):
        self._client = client
        self._timeout = timeout
        self._clock = clock

    async def _fetch_page(
        self,
        client: httpx.AsyncClient,
        symbol: str,
        timeframe: Timeframe,
        *,
        start_time: datetime | None,
        end_time: datetime,
        limit: int,
        page: int,
    ) -> list[Candle]:
        params: dict[str, Any] = {
            "symbol": symbol,
            "resolution": NOBITEX_RESOLUTIONS[timeframe],
            "to": int(end_time.astimezone(UTC).timestamp()),
            "page": page,
        }
        if start_time is not None:
            params["from"] = int(start_time.astimezone(UTC).timestamp())
        else:
            params["countback"] = limit

        try:
            response = await client.get(NOBITEX_OHLC_URL, params=params)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise MarketDataProviderError("Nobitex request failed or returned invalid JSON") from exc

        if not isinstance(payload, dict):
            raise MarketDataProviderError("Nobitex returned an unexpected OHLC payload")
        status = payload.get("s")
        if status == "no_data":
            return []
        if status != "ok":
            raise MarketDataProviderError("Nobitex returned an unsuccessful OHLC response")

        try:
            fields = [payload[key] for key in ("t", "o", "h", "l", "c", "v")]
            if any(not isinstance(values, list) for values in fields):
                raise ValueError("OHLC fields must be arrays")
            if len({len(values) for values in fields}) != 1:
                raise ValueError("OHLC arrays have inconsistent lengths")
            now = self._clock()
            if now.tzinfo is None or now.utcoffset() is None:
                raise ValueError("adapter clock must return a timezone-aware datetime")
            now = now.astimezone(UTC)
            candles = []
            for timestamp, open_, high, low, close, volume in zip(*fields, strict=True):
                opened = datetime.fromtimestamp(int(timestamp), tz=UTC)
                closed = _close_time(opened, timeframe)
                candles.append(
                    Candle(
                        timestamp=opened,
                        close_time=closed,
                        is_closed=closed < now,
                        open=Decimal(str(open_)),
                        high=Decimal(str(high)),
                        low=Decimal(str(low)),
                        close=Decimal(str(close)),
                        volume=Decimal(str(volume)),
                    )
                )
            candles.sort(key=lambda item: item.timestamp)
            return validate_candle_series(candles) if candles else []
        except (KeyError, ValueError, TypeError, ArithmeticError) as exc:
            raise MarketDataProviderError("Nobitex returned invalid OHLC candle data") from exc

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
            if timeframe not in NOBITEX_RESOLUTIONS:
                raise ValueError
        except ValueError as exc:
            raise ValueError("unsupported Nobitex Spot candle interval") from exc
        if not 1 <= limit <= MAX_PAGE_SIZE:
            raise ValueError(f"limit must be between 1 and {MAX_PAGE_SIZE}")
        for name, value in (("start_time", start_time), ("end_time", end_time)):
            if value is not None and (value.tzinfo is None or value.utcoffset() is None):
                raise ValueError(f"{name} must be timezone-aware")
        if start_time and end_time and start_time > end_time:
            raise ValueError("start_time must be <= end_time")

        end = end_time or self._clock()
        if end.tzinfo is None or end.utcoffset() is None:
            raise ValueError("adapter clock must return a timezone-aware datetime")
        client = self._client
        own_client = client is None
        if own_client:
            client = httpx.AsyncClient(timeout=self._timeout)
        try:
            return await self._fetch_page(
                client, normalized_symbol, timeframe,
                start_time=start_time, end_time=end,
                limit=limit, page=1,
            )
        finally:
            if own_client and client is not None:
                await client.aclose()

    async def fetch_historical_ohlcv(
        self,
        symbol: str,
        interval: str = "1h",
        *,
        start_time: datetime,
        end_time: datetime,
        page_limit: int = MAX_PAGE_SIZE,
        max_candles: int = 10_000,
    ) -> list[Candle]:
        if start_time.tzinfo is None or start_time.utcoffset() is None:
            raise ValueError("start_time must be timezone-aware")
        if end_time.tzinfo is None or end_time.utcoffset() is None:
            raise ValueError("end_time must be timezone-aware")
        if start_time > end_time:
            raise ValueError("start_time must be <= end_time")
        if not 1 <= page_limit <= MAX_PAGE_SIZE:
            raise ValueError(f"page_limit must be between 1 and {MAX_PAGE_SIZE}")
        if max_candles < 1:
            raise ValueError("max_candles must be >= 1")
        normalized_symbol = str(Symbol(symbol))
        try:
            timeframe = Timeframe(interval)
            if timeframe not in NOBITEX_RESOLUTIONS:
                raise ValueError
        except ValueError as exc:
            raise ValueError("unsupported Nobitex Spot candle interval") from exc

        client = self._client
        own_client = client is None
        if own_client:
            client = httpx.AsyncClient(timeout=self._timeout)
        collected: dict[datetime, Candle] = {}
        try:
            for page_number in range(1, (max_candles + page_limit - 1) // page_limit + 1):
                remaining = max_candles - len(collected)
                if remaining <= 0:
                    break
                page = await self._fetch_page(
                    client, normalized_symbol, timeframe,
                    start_time=start_time, end_time=end_time,
                    limit=min(page_limit, remaining), page=page_number,
                )
                if not page:
                    break
                new_items = [
                    item for item in page
                    if start_time.astimezone(UTC) <= item.timestamp <= end_time.astimezone(UTC)
                    and item.timestamp not in collected
                ]
                if not new_items:
                    raise MarketDataProviderError("Nobitex historical pagination made no forward progress")
                for item in new_items:
                    if len(collected) >= max_candles:
                        break
                    collected[item.timestamp] = item
                if len(page) < min(page_limit, remaining):
                    break
        finally:
            if own_client and client is not None:
                await client.aclose()
        return validate_candle_series(sorted(collected.values(), key=lambda item: item.timestamp)) if collected else []
