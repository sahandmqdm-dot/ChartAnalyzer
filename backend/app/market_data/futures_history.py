from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, field_validator

from app.market_data.futures import (
    FuturesMarketDataError,
    _HttpFuturesAdapter,
)
from app.market_data.symbol import Symbol


class FuturesSeriesPoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    timestamp: datetime
    value: Decimal
    secondary_value: Decimal | None = None


class FuturesHistory(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider: str
    market_type: str
    symbol: str
    period: str
    funding_rate: list[FuturesSeriesPoint]
    open_interest: list[FuturesSeriesPoint]
    long_short_ratio: list[FuturesSeriesPoint]
    observed_at: datetime

    @field_validator("funding_rate", "open_interest", "long_short_ratio")
    @classmethod
    def chronological(cls, points: list[FuturesSeriesPoint]) -> list[FuturesSeriesPoint]:
        return sorted(points, key=lambda point: point.timestamp)


def _decimal(value: Any) -> Decimal:
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("non-finite market data")
    return result


def _timestamp(value: Any) -> datetime:
    return datetime.fromtimestamp(int(value) / 1000, tz=UTC)


def _range_milliseconds(start_time: datetime, end_time: datetime) -> tuple[int, int]:
    if (
        start_time.tzinfo is None
        or start_time.utcoffset() is None
        or end_time.tzinfo is None
        or end_time.utcoffset() is None
        or start_time > end_time
    ):
        raise ValueError("funding history range must use ordered timezone-aware timestamps")
    return int(start_time.timestamp() * 1000), int(end_time.timestamp() * 1000)


class _HistoryAdapter(_HttpFuturesAdapter):
    name = "futures_history"

    async def _rows(self, url: str, params: dict[str, str]) -> list[dict[str, Any]]:
        payload = await self._get_json(url, params)
        if isinstance(payload, list):
            rows = payload
        elif isinstance(payload, dict):
            if payload.get("retCode", 0) != 0:
                raise FuturesMarketDataError("Futures provider returned an unsuccessful response")
            result = payload.get("result")
            rows = result.get("list") if isinstance(result, dict) else None
        else:
            rows = None
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise FuturesMarketDataError("Futures provider returned an unexpected history payload")
        return rows


class BinanceUsdmFuturesHistoryAdapter(_HistoryAdapter):
    name = "binance_usdm_futures"

    async def fetch_funding_rates(
        self, symbol: str, start_time: datetime, end_time: datetime
    ) -> list[FuturesSeriesPoint]:
        normalized = str(Symbol(symbol))
        start_ms, end_ms = _range_milliseconds(start_time, end_time)
        cursor_start = start_ms
        points: dict[datetime, FuturesSeriesPoint] = {}
        for _ in range(10):
            rows = await self._rows(
                "https://fapi.binance.com/fapi/v1/fundingRate",
                {
                    "symbol": normalized,
                    "startTime": str(cursor_start),
                    "endTime": str(end_ms),
                    "limit": "1000",
                },
            )
            if not rows:
                break
            try:
                for row in rows:
                    point = FuturesSeriesPoint(
                        timestamp=_timestamp(row["fundingTime"]),
                        value=_decimal(row["fundingRate"]),
                        secondary_value=_decimal(row["markPrice"]) if row.get("markPrice") else None,
                    )
                    if start_time <= point.timestamp <= end_time:
                        points[point.timestamp] = point
                last_ms = max(int(row["fundingTime"]) for row in rows)
            except (KeyError, ValueError, TypeError, ArithmeticError, OverflowError) as exc:
                raise FuturesMarketDataError("Binance returned invalid funding history") from exc
            if len(rows) < 1000 or last_ms >= end_ms:
                break
            next_start = last_ms + 1
            if next_start <= cursor_start:
                raise FuturesMarketDataError("Binance funding history pagination did not advance")
            cursor_start = next_start
        else:
            raise FuturesMarketDataError("Binance funding history exceeded the pagination safety limit")
        return sorted(points.values(), key=lambda point: point.timestamp)

    async def fetch_history(self, symbol: str, period: str, limit: int) -> FuturesHistory:
        normalized = str(Symbol(symbol))
        base = {"symbol": normalized, "period": period, "limit": str(limit)}
        funding, interest, ratio = await asyncio.gather(
            self._rows(
                "https://fapi.binance.com/fapi/v1/fundingRate",
                {"symbol": normalized, "limit": str(min(limit, 1000))},
            ),
            self._rows("https://fapi.binance.com/futures/data/openInterestHist", base),
            self._rows("https://fapi.binance.com/futures/data/globalLongShortAccountRatio", base),
        )
        try:
            return FuturesHistory(
                provider=self.name,
                market_type="linear_perpetual",
                symbol=normalized,
                period=period,
                funding_rate=[
                    FuturesSeriesPoint(
                        timestamp=_timestamp(row["fundingTime"]),
                        value=_decimal(row["fundingRate"]),
                        secondary_value=_decimal(row["markPrice"]) if row.get("markPrice") else None,
                    )
                    for row in funding
                ],
                open_interest=[
                    FuturesSeriesPoint(
                        timestamp=_timestamp(row["timestamp"]),
                        value=_decimal(row["sumOpenInterest"]),
                        secondary_value=_decimal(row["sumOpenInterestValue"]),
                    )
                    for row in interest
                ],
                long_short_ratio=[
                    FuturesSeriesPoint(
                        timestamp=_timestamp(row["timestamp"]),
                        value=_decimal(row["longShortRatio"]),
                        secondary_value=_decimal(row["longAccount"]),
                    )
                    for row in ratio
                ],
                observed_at=datetime.now(UTC),
            )
        except (KeyError, ValueError, TypeError, ArithmeticError, OverflowError) as exc:
            raise FuturesMarketDataError("Binance returned invalid derivatives history") from exc


class BybitLinearFuturesHistoryAdapter(_HistoryAdapter):
    name = "bybit_linear_futures"

    async def fetch_funding_rates(
        self, symbol: str, start_time: datetime, end_time: datetime
    ) -> list[FuturesSeriesPoint]:
        normalized = str(Symbol(symbol))
        start_ms, end_ms = _range_milliseconds(start_time, end_time)
        cursor_end = end_ms
        points: dict[datetime, FuturesSeriesPoint] = {}
        for _ in range(25):
            rows = await self._rows(
                "https://api.bybit.com/v5/market/funding/history",
                {
                    "category": "linear",
                    "symbol": normalized,
                    "endTime": str(cursor_end),
                    "limit": "200",
                },
            )
            if not rows:
                break
            try:
                timestamps = [int(row["fundingRateTimestamp"]) for row in rows]
                for row in rows:
                    point = FuturesSeriesPoint(
                        timestamp=_timestamp(row["fundingRateTimestamp"]),
                        value=_decimal(row["fundingRate"]),
                    )
                    if start_time <= point.timestamp <= end_time:
                        points[point.timestamp] = point
            except (KeyError, ValueError, TypeError, ArithmeticError, OverflowError) as exc:
                raise FuturesMarketDataError("Bybit returned invalid funding history") from exc
            earliest_ms = min(timestamps)
            if earliest_ms <= start_ms or len(rows) < 200:
                break
            next_end = earliest_ms - 1
            if next_end >= cursor_end:
                raise FuturesMarketDataError("Bybit funding history pagination did not advance")
            cursor_end = next_end
        else:
            raise FuturesMarketDataError("Bybit funding history exceeded the pagination safety limit")
        return sorted(points.values(), key=lambda point: point.timestamp)

    _period_map: ClassVar[dict[str, str]] = {
        "5m": "5min",
        "15m": "15min",
        "30m": "30min",
        "1h": "1h",
        "4h": "4h",
        "1d": "1d",
    }

    async def fetch_history(self, symbol: str, period: str, limit: int) -> FuturesHistory:
        normalized = str(Symbol(symbol))
        bybit_period = self._period_map[period]
        category_symbol = {"category": "linear", "symbol": normalized}
        funding, interest, ratio = await asyncio.gather(
            self._rows(
                "https://api.bybit.com/v5/market/funding/history",
                {**category_symbol, "limit": str(min(limit, 200))},
            ),
            self._rows(
                "https://api.bybit.com/v5/market/open-interest",
                {**category_symbol, "intervalTime": bybit_period, "limit": str(min(limit, 200))},
            ),
            self._rows(
                "https://api.bybit.com/v5/market/account-ratio",
                {**category_symbol, "period": bybit_period, "limit": str(min(limit, 200))},
            ),
        )
        try:
            return FuturesHistory(
                provider=self.name,
                market_type="linear_perpetual",
                symbol=normalized,
                period=period,
                funding_rate=[
                    FuturesSeriesPoint(
                        timestamp=_timestamp(row["fundingRateTimestamp"]),
                        value=_decimal(row["fundingRate"]),
                    )
                    for row in funding
                ],
                open_interest=[
                    FuturesSeriesPoint(
                        timestamp=_timestamp(row["timestamp"]),
                        value=_decimal(row["openInterest"]),
                    )
                    for row in interest
                ],
                long_short_ratio=[
                    FuturesSeriesPoint(
                        timestamp=_timestamp(row["timestamp"]),
                        value=_decimal(row["buyRatio"]) / _decimal(row["sellRatio"]),
                        secondary_value=_decimal(row["buyRatio"]),
                    )
                    for row in ratio
                ],
                observed_at=datetime.now(UTC),
            )
        except (KeyError, ValueError, TypeError, ArithmeticError, OverflowError, ZeroDivisionError) as exc:
            raise FuturesMarketDataError("Bybit returned invalid derivatives history") from exc
