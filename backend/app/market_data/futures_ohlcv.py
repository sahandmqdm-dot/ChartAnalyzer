from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, ClassVar

from app.market_data.candle import Candle
from app.market_data.futures import FuturesMarketDataError, _HttpFuturesAdapter
from app.market_data.quality import validate_candle_series
from app.market_data.symbol import Symbol


class _LinearFuturesOHLCVAdapter(_HttpFuturesAdapter):
    """Shared normalization for public linear-perpetual contract klines."""

    _period_ms: ClassVar[dict[str, int]] = {
        "5m": 300_000,
        "15m": 900_000,
        "30m": 1_800_000,
        "1h": 3_600_000,
        "4h": 14_400_000,
        "1d": 86_400_000,
    }

    @staticmethod
    def _positive_decimal(value: Any, *, allow_zero: bool = False) -> Decimal:
        result = Decimal(str(value))
        if not result.is_finite() or result < 0 or (not allow_zero and result == 0):
            raise ValueError("futures candle values must be finite and non-negative")
        return result

    def _normalize(self, rows: list[Any], symbol: str, period: str) -> list[Candle]:
        duration = self._period_ms[period]
        now = datetime.now(UTC)
        candles: list[Candle] = []
        try:
            for row in rows:
                if not isinstance(row, (list, tuple)) or len(row) < 6:
                    raise ValueError("invalid futures kline row")
                timestamp = datetime.fromtimestamp(int(row[0]) / 1000, tz=UTC)
                close_time = timestamp + timedelta(milliseconds=duration - 1)
                candles.append(
                    Candle(
                        timestamp=timestamp,
                        close_time=close_time,
                        is_closed=close_time < now,
                        open=self._positive_decimal(row[1]),
                        high=self._positive_decimal(row[2]),
                        low=self._positive_decimal(row[3]),
                        close=self._positive_decimal(row[4]),
                        volume=self._positive_decimal(row[5], allow_zero=True),
                    )
                )
            candles.sort(key=lambda item: item.timestamp)
            return validate_candle_series(candles) if candles else []
        except (ValueError, TypeError, ArithmeticError, OverflowError) as exc:
            raise FuturesMarketDataError(
                f"{self.name} returned invalid futures OHLCV data"
            ) from exc


class BinanceUsdmFuturesOHLCVAdapter(_LinearFuturesOHLCVAdapter):
    """Binance USD-M perpetual contract OHLCV (not Spot klines)."""

    name = "binance_usdm_futures"

    async def fetch_ohlcv(self, symbol: str, interval: str = "1h", *, limit: int = 500) -> list[Candle]:
        normalized = str(Symbol(symbol))
        if interval not in self._period_ms:
            raise ValueError("unsupported Binance USD-M futures interval")
        if not 1 <= limit <= 1500:
            raise ValueError("limit must be between 1 and 1500")
        payload = await self._get_json(
            "https://fapi.binance.com/fapi/v1/klines",
            {"symbol": normalized, "interval": interval, "limit": str(limit)},
        )
        if not isinstance(payload, list):
            raise FuturesMarketDataError("Binance returned an unexpected futures kline payload")
        return self._normalize(payload, normalized, interval)


class BybitLinearFuturesOHLCVAdapter(_LinearFuturesOHLCVAdapter):
    """Bybit V5 linear perpetual contract OHLCV (not Spot klines)."""

    name = "bybit_linear_futures"
    _intervals: ClassVar[dict[str, str]] = {
        "5m": "5",
        "15m": "15",
        "30m": "30",
        "1h": "60",
        "4h": "240",
        "1d": "D",
    }

    async def fetch_ohlcv(self, symbol: str, interval: str = "1h", *, limit: int = 500) -> list[Candle]:
        normalized = str(Symbol(symbol))
        provider_interval = self._intervals.get(interval)
        if provider_interval is None:
            raise ValueError("unsupported Bybit linear futures interval")
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        payload = await self._get_json(
            "https://api.bybit.com/v5/market/kline",
            {
                "category": "linear",
                "symbol": normalized,
                "interval": provider_interval,
                "limit": str(limit),
            },
        )
        if not isinstance(payload, dict) or payload.get("retCode") != 0:
            raise FuturesMarketDataError("Bybit returned an unsuccessful futures kline response")
        result = payload.get("result")
        rows = result.get("list") if isinstance(result, dict) else None
        if not isinstance(rows, list):
            raise FuturesMarketDataError("Bybit returned an unexpected futures kline payload")
        return self._normalize(rows, normalized, interval)
