from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict

from app.market_data.futures import FuturesMarketDataError, _HttpFuturesAdapter
from app.market_data.symbol import Symbol


class MarkPriceCandle(BaseModel):
    model_config = ConfigDict(frozen=True)

    timestamp: datetime
    close_time: datetime
    is_closed: bool
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal


class MarkPriceHistory(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider: str
    market_type: str
    symbol: str
    period: str
    candles: list[MarkPriceCandle]
    observed_at: datetime


class _MarkPriceAdapter(_HttpFuturesAdapter):
    name = "mark_price"
    _period_ms: ClassVar[dict[str, int]] = {
        "5m": 300_000,
        "15m": 900_000,
        "30m": 1_800_000,
        "1h": 3_600_000,
        "4h": 14_400_000,
        "1d": 86_400_000,
    }

    @staticmethod
    def _decimal(value: Any) -> Decimal:
        result = Decimal(str(value))
        if not result.is_finite() or result <= 0:
            raise ValueError("mark price values must be finite and positive")
        return result

    def _normalize(
        self, rows: list[Any], symbol: str, period: str, now: datetime
    ) -> MarkPriceHistory:
        duration = self._period_ms[period]
        candles: list[MarkPriceCandle] = []
        try:
            for row in rows:
                if not isinstance(row, (list, tuple)) or len(row) < 5:
                    raise ValueError("invalid mark price kline row")
                timestamp = datetime.fromtimestamp(int(row[0]) / 1000, tz=UTC)
                close_time = timestamp + timedelta(milliseconds=duration - 1)
                opening, high, low, close = (self._decimal(value) for value in row[1:5])
                if high < max(opening, close, low) or low > min(opening, close, high):
                    raise ValueError("invalid mark price OHLC relationship")
                candles.append(
                    MarkPriceCandle(
                        timestamp=timestamp,
                        close_time=close_time,
                        is_closed=close_time < now,
                        open=opening,
                        high=high,
                        low=low,
                        close=close,
                    )
                )
            candles.sort(key=lambda item: item.timestamp)
            if len({item.timestamp for item in candles}) != len(candles):
                raise ValueError("duplicate mark price candle timestamps")
            return MarkPriceHistory(
                provider=self.name,
                market_type="linear_perpetual",
                symbol=symbol,
                period=period,
                candles=candles,
                observed_at=now,
            )
        except (KeyError, ValueError, TypeError, ArithmeticError, OverflowError) as exc:
            raise FuturesMarketDataError("Exchange returned invalid mark price history") from exc


class BinanceUsdmMarkPriceAdapter(_MarkPriceAdapter):
    name = "binance_usdm_futures"

    async def fetch_mark_price_history(
        self, symbol: str, period: str, limit: int
    ) -> MarkPriceHistory:
        normalized = str(Symbol(symbol))
        if period not in self._period_ms:
            raise ValueError("unsupported mark price period")
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        payload = await self._get_json(
            "https://fapi.binance.com/fapi/v1/markPriceKlines",
            {"symbol": normalized, "interval": period, "limit": str(limit)},
        )
        if not isinstance(payload, list):
            raise FuturesMarketDataError("Binance returned an unexpected mark price payload")
        return self._normalize(payload, normalized, period, datetime.now(UTC))


class BybitLinearMarkPriceAdapter(_MarkPriceAdapter):
    name = "bybit_linear_futures"
    _intervals: ClassVar[dict[str, str]] = {
        "5m": "5",
        "15m": "15",
        "30m": "30",
        "1h": "60",
        "4h": "240",
        "1d": "D",
    }

    async def fetch_mark_price_history(
        self, symbol: str, period: str, limit: int
    ) -> MarkPriceHistory:
        normalized = str(Symbol(symbol))
        if period not in self._intervals:
            raise ValueError("unsupported mark price period")
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        payload = await self._get_json(
            "https://api.bybit.com/v5/market/mark-price-kline",
            {
                "category": "linear",
                "symbol": normalized,
                "interval": self._intervals[period],
                "limit": str(limit),
            },
        )
        if not isinstance(payload, dict) or payload.get("retCode") != 0:
            raise FuturesMarketDataError("Bybit returned an unsuccessful mark price response")
        result = payload.get("result")
        rows = result.get("list") if isinstance(result, dict) else None
        if not isinstance(rows, list):
            raise FuturesMarketDataError("Bybit returned an unexpected mark price payload")
        return self._normalize(rows, normalized, period, datetime.now(UTC))
