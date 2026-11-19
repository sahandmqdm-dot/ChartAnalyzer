"""Read-only market-data hub with strict provenance and fail-closed normalization.

The Binance USD-M adapter is public-data only. This module never places orders,
uses account endpoints, interpolates candles, or merges provider histories.
"""
from __future__ import annotations

import json
import math
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class MarketDataError(RuntimeError):
    """Base exception for market-data acquisition and validation errors."""


class ProviderRateLimitError(MarketDataError):
    """Provider throttled the client after the configured retry budget."""


class ProviderUnavailableError(MarketDataError):
    """Provider could not return a valid response."""


class DataValidationError(MarketDataError):
    """Provider payload violated the normalized data contract."""


def _utc(value: datetime, name: str = "timestamp") -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise DataValidationError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _number(value: Any, name: str, *, positive: bool = False) -> float:
    if isinstance(value, bool):
        raise DataValidationError(f"{name} must be numeric")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise DataValidationError(f"{name} must be numeric") from exc
    if not math.isfinite(result) or (positive and result <= 0):
        raise DataValidationError(f"{name} must be finite" + (" and > 0" if positive else ""))
    return result


@dataclass(frozen=True, slots=True)
class OHLCVBar:
    instrument_id: str
    provider: str
    interval: str
    open_time: datetime
    close_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    quote_volume: float | None = None
    trades: int | None = None

    def __post_init__(self) -> None:
        if not self.instrument_id.strip() or not self.provider.strip() or not self.interval.strip():
            raise DataValidationError("instrument_id, provider and interval are required")
        opened, closed = _utc(self.open_time, "open_time"), _utc(self.close_time, "close_time")
        values = {key: _number(getattr(self, key), key, positive=True)
                  for key in ("open", "high", "low", "close")}
        volume = _number(self.volume, "volume")
        if volume < 0 or (self.quote_volume is not None and _number(self.quote_volume, "quote_volume") < 0):
            raise DataValidationError("volume values cannot be negative")
        if not (values["high"] >= max(values["open"], values["close"], values["low"])
                and values["low"] <= min(values["open"], values["close"])):
            raise DataValidationError("impossible OHLC geometry")
        if closed < opened:
            raise DataValidationError("close_time precedes open_time")
        if self.trades is not None and (isinstance(self.trades, bool) or int(self.trades) < 0):
            raise DataValidationError("trades must be a non-negative integer")
        object.__setattr__(self, "instrument_id", self.instrument_id.strip())
        object.__setattr__(self, "provider", self.provider.strip())
        object.__setattr__(self, "interval", self.interval.strip())
        object.__setattr__(self, "open_time", opened)
        object.__setattr__(self, "close_time", closed)
        for key, value in values.items():
            object.__setattr__(self, key, value)
        object.__setattr__(self, "volume", volume)
        if self.quote_volume is not None:
            object.__setattr__(self, "quote_volume", float(self.quote_volume))


@dataclass(frozen=True, slots=True)
class DataProvenance:
    provider: str
    endpoint: str
    instrument_id: str
    retrieved_at: datetime
    source_timestamp_semantics: str
    market_type: str
    raw_record_count: int
    normalized_record_count: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "retrieved_at", _utc(self.retrieved_at, "retrieved_at"))


@dataclass(frozen=True, slots=True)
class OHLCVBatch:
    bars: tuple[OHLCVBar, ...]
    provenance: DataProvenance
    warnings: tuple[str, ...] = ()

    def to_records(self) -> list[dict[str, Any]]:
        return [asdict(bar) for bar in self.bars]


@dataclass(frozen=True, slots=True)
class ProviderConfig:
    timeout_seconds: float = 10.0
    max_retries: int = 2
    backoff_seconds: float = 0.25
    max_limit: int = 1500

    def __post_init__(self) -> None:
        if not 1 <= self.timeout_seconds <= 60:
            raise ValueError("timeout_seconds must be between 1 and 60")
        if not 0 <= self.max_retries <= 5:
            raise ValueError("max_retries must be between 0 and 5")
        if not 0 <= self.backoff_seconds <= 10:
            raise ValueError("backoff_seconds must be between 0 and 10")
        if not 1 <= self.max_limit <= 1500:
            raise ValueError("max_limit must be between 1 and 1500")


class BinanceUSDMPublicAdapter:
    """Public Binance USD-M futures REST adapter; read-only and account-free."""

    provider = "binance_usdm"
    market_type = "linear_perpetual"
    base_url = "https://fapi.binance.com"
    intervals = frozenset({"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h",
                           "6h", "8h", "12h", "1d", "3d", "1w", "1M"})
    oi_periods = frozenset({"5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d"})

    def __init__(
        self,
        config: ProviderConfig | None = None,
        *,
        transport: Callable[[str, float], tuple[int, Mapping[str, str], bytes]] | None = None,
        clock: Callable[[], float] = time.time,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config or ProviderConfig()
        self._transport = transport or self._http_get
        self._clock = clock
        self._sleep = sleeper

    @staticmethod
    def _http_get(url: str, timeout: float) -> tuple[int, Mapping[str, str], bytes]:
        request = Request(url, headers={"Accept": "application/json", "User-Agent": "ChartAnalyzer/MarketDataHub"})
        try:
            with urlopen(request, timeout=timeout) as response:  # nosec B310: fixed HTTPS Binance public-data host
                return response.status, dict(response.headers.items()), response.read()
        except HTTPError as exc:
            return exc.code, dict(exc.headers.items()) if exc.headers else {}, exc.read()
        except (URLError, TimeoutError, OSError) as exc:
            raise ProviderUnavailableError(f"Binance transport failed: {type(exc).__name__}") from exc

    def _get(self, path: str, params: Mapping[str, Any]) -> tuple[Any, datetime]:
        if not path.startswith("/fapi/v1/") and not path.startswith("/futures/data/"):
            raise ValueError("endpoint is not in the public market-data allowlist")
        query = urlencode({key: value for key, value in params.items() if value is not None})
        url = f"{self.base_url}{path}?{query}"
        for attempt in range(self.config.max_retries + 1):
            try:
                status, headers, body = self._transport(url, self.config.timeout_seconds)
            except ProviderUnavailableError:
                if attempt >= self.config.max_retries:
                    raise
                self._sleep(self.config.backoff_seconds * (2 ** attempt))
                continue
            if status == 200:
                try:
                    return json.loads(body), datetime.fromtimestamp(self._clock(), UTC)
                except (ValueError, TypeError) as exc:
                    raise DataValidationError("provider returned invalid JSON") from exc
            if status in (418, 429):
                if attempt >= self.config.max_retries:
                    raise ProviderRateLimitError(f"Binance rate limit response HTTP {status}")
                retry_after = headers.get("Retry-After")
                try:
                    delay = min(30.0, max(0.0, float(retry_after))) if retry_after else self.config.backoff_seconds * (2 ** attempt)
                except (TypeError, ValueError):
                    delay = self.config.backoff_seconds * (2 ** attempt)
                self._sleep(delay)
                continue
            if status >= 500 and attempt < self.config.max_retries:
                self._sleep(self.config.backoff_seconds * (2 ** attempt))
                continue
            raise ProviderUnavailableError(f"Binance returned HTTP {status}")
        raise ProviderUnavailableError("provider retry budget exhausted")

    @staticmethod
    def _symbol(value: str) -> str:
        symbol = str(value).strip().upper()
        if not symbol or not symbol.isalnum() or len(symbol) > 30:
            raise ValueError("symbol must be a canonical alphanumeric exchange symbol")
        return symbol

    def fetch_ohlcv(self, symbol: str, interval: str = "1h", limit: int = 500) -> OHLCVBatch:
        symbol = self._symbol(symbol)
        if interval not in self.intervals:
            raise ValueError(f"unsupported Binance interval: {interval}")
        if isinstance(limit, bool) or not 1 <= int(limit) <= self.config.max_limit:
            raise ValueError(f"limit must be between 1 and {self.config.max_limit}")
        raw, retrieved = self._get("/fapi/v1/klines", {"symbol": symbol, "interval": interval, "limit": int(limit)})
        if not isinstance(raw, list):
            raise DataValidationError("klines payload must be a list")
        bars: list[OHLCVBar] = []
        for row in raw:
            if not isinstance(row, Sequence) or len(row) < 8:
                raise DataValidationError("malformed kline row")
            bars.append(OHLCVBar(
                instrument_id=symbol, provider=self.provider, interval=interval,
                open_time=datetime.fromtimestamp(int(row[0]) / 1000, UTC),
                close_time=datetime.fromtimestamp(int(row[6]) / 1000, UTC),
                open=row[1], high=row[2], low=row[3], close=row[4], volume=row[5],
                quote_volume=row[7], trades=int(row[8]) if len(row) > 8 else None,
            ))
        bars.sort(key=lambda bar: bar.open_time)
        unique: dict[datetime, OHLCVBar] = {}
        duplicate_count = 0
        for bar in bars:
            if bar.open_time in unique:
                duplicate_count += 1
            unique[bar.open_time] = bar
        bars = list(unique.values())
        warnings: list[str] = []
        if duplicate_count:
            warnings.append(f"{duplicate_count} duplicate candle timestamp(s) discarded")
        if bars and bars[-1].close_time.timestamp() >= retrieved.timestamp() * 1000 / 1000:
            warnings.append("latest candle may still be forming; it is retained but must be excluded by downstream closed-bar policy")
        if len(bars) > 1:
            durations = [(bars[i].open_time - bars[i-1].open_time).total_seconds() for i in range(1, len(bars))]
            if any(delta <= 0 for delta in durations):
                raise DataValidationError("normalized candle timestamps are not strictly increasing")
            if any(delta > _interval_seconds(interval) * 1.5 for delta in durations):
                warnings.append("candle history contains gaps; no interpolation was performed")
        provenance = DataProvenance(
            provider=self.provider, endpoint="/fapi/v1/klines", instrument_id=symbol,
            retrieved_at=retrieved, source_timestamp_semantics="exchange_kline_open_time_utc",
            market_type=self.market_type, raw_record_count=len(raw), normalized_record_count=len(bars),
        )
        return OHLCVBatch(tuple(bars), provenance, tuple(warnings))

    def fetch_funding_history(self, symbol: str, limit: int = 100) -> dict[str, Any]:
        symbol = self._symbol(symbol)
        if isinstance(limit, bool) or not 1 <= int(limit) <= 1000:
            raise ValueError("funding limit must be between 1 and 1000")
        raw, retrieved = self._get("/fapi/v1/fundingRate", {"symbol": symbol, "limit": int(limit)})
        if not isinstance(raw, list):
            raise DataValidationError("funding payload must be a list")
        records = []
        for row in raw:
            if not isinstance(row, Mapping) or "fundingTime" not in row or "fundingRate" not in row:
                raise DataValidationError("malformed funding record")
            records.append({
                "instrument_id": symbol, "provider": self.provider,
                "observed_at": datetime.fromtimestamp(int(row["fundingTime"]) / 1000, UTC),
                "funding_rate": _number(row["fundingRate"], "fundingRate"),
                "funding_interval_hours": 8.0,
                "source_timestamp_semantics": "exchange_funding_event_time",
            })
        records.sort(key=lambda item: item["observed_at"])
        return {"records": records, "retrieved_at": retrieved, "provider": self.provider,
                "endpoint": "/fapi/v1/fundingRate", "market_type": self.market_type}

    def fetch_open_interest_history(self, symbol: str, period: str = "1h", limit: int = 30) -> dict[str, Any]:
        symbol = self._symbol(symbol)
        if period not in self.oi_periods:
            raise ValueError(f"unsupported open-interest period: {period}")
        if isinstance(limit, bool) or not 1 <= int(limit) <= 500:
            raise ValueError("open-interest limit must be between 1 and 500")
        raw, retrieved = self._get("/futures/data/openInterestHist", {
            "symbol": symbol, "period": period, "limit": int(limit),
        })
        if not isinstance(raw, list):
            raise DataValidationError("open-interest payload must be a list")
        records = []
        for row in raw:
            if not isinstance(row, Mapping) or "timestamp" not in row or "sumOpenInterest" not in row:
                raise DataValidationError("malformed open-interest record")
            records.append({
                "instrument_id": symbol, "provider": self.provider,
                "observed_at": datetime.fromtimestamp(int(row["timestamp"]) / 1000, UTC),
                "open_interest": _number(row["sumOpenInterest"], "sumOpenInterest"),
                "unit": "contracts",
                "quote_notional": _number(row["sumOpenInterestValue"], "sumOpenInterestValue")
                    if row.get("sumOpenInterestValue") is not None else None,
                "source_timestamp_semantics": "exchange_aggregate_snapshot_time",
            })
        records.sort(key=lambda item: item["observed_at"])
        return {"records": records, "retrieved_at": retrieved, "provider": self.provider,
                "endpoint": "/futures/data/openInterestHist", "market_type": self.market_type,
                "period": period}


def _interval_seconds(interval: str) -> int:
    units = {"m": 60, "h": 3600, "d": 86400, "w": 604800}
    if interval.endswith("M"):
        return 30 * 86400
    return int(interval[:-1]) * units[interval[-1]]


class MarketDataHub:
    """Small provider registry. Data is returned with source provenance, never blended."""

    def __init__(self, adapters: Mapping[str, Any] | None = None) -> None:
        self._adapters = dict(adapters or {})

    def register(self, name: str, adapter: Any) -> None:
        key = str(name).strip().lower()
        if not key or key in self._adapters:
            raise ValueError("provider name must be non-empty and unique")
        for method in ("fetch_ohlcv", "fetch_funding_history", "fetch_open_interest_history"):
            if not callable(getattr(adapter, method, None)):
                raise TypeError(f"adapter is missing {method}")
        self._adapters[key] = adapter

    def provider(self, name: str) -> Any:
        try:
            return self._adapters[str(name).strip().lower()]
        except KeyError as exc:
            raise MarketDataError(f"unknown market-data provider: {name}") from exc

    def fetch_ohlcv(self, provider: str, symbol: str, interval: str = "1h", limit: int = 500) -> OHLCVBatch:
        batch = self.provider(provider).fetch_ohlcv(symbol, interval, limit)
        if not isinstance(batch, OHLCVBatch):
            raise DataValidationError("provider returned a non-normalized OHLCV result")
        if any(bar.provider != provider for bar in batch.bars):
            raise DataValidationError("provider identity mismatch in OHLCV records")
        return batch

    def fetch_derivatives(self, provider: str, symbol: str, *, oi_period: str = "1h",
                          funding_limit: int = 100, oi_limit: int = 30) -> dict[str, Any]:
        adapter = self.provider(provider)
        funding = adapter.fetch_funding_history(symbol, funding_limit)
        oi = adapter.fetch_open_interest_history(symbol, oi_period, oi_limit)
        if funding["provider"] != oi["provider"] or funding["market_type"] != oi["market_type"]:
            raise DataValidationError("derivatives provider or market type mismatch")
        return {"provider": funding["provider"], "market_type": funding["market_type"],
                "funding": funding, "open_interest": oi, "actionable": False,
                "policy": "Independent provider observations; no synthetic merge or position inference."}


__all__ = [
    "BinanceUSDMPublicAdapter", "DataProvenance", "DataValidationError",
    "MarketDataError", "MarketDataHub", "OHLCVBar", "OHLCVBatch",
    "ProviderConfig", "ProviderRateLimitError", "ProviderUnavailableError",
]
