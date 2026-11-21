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
from types import MappingProxyType
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
    klines_endpoint = "/fapi/v1/klines"
    supports_derivatives = True
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
        allowed = {
            "https://fapi.binance.com": ("/fapi/v1/", "/futures/data/"),
            "https://api.binance.com": ("/api/v3/",),
        }
        prefixes = allowed.get(self.base_url)
        if prefixes is None or not path.startswith(prefixes):
            raise ValueError("endpoint or host is not in the pinned public market-data allowlist")
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

    def fetch_contract_metadata(
        self,
        symbol: str,
        *,
        risk_brackets: Sequence[Mapping[str, Any]] | None = None,
        risk_bracket_source: str | None = None,
        risk_bracket_version: str | None = None,
    ) -> dict[str, Any]:
        """Normalize public exchangeInfo into a temporal contract snapshot candidate.

        Binance exchangeInfo publishes symbol filters but not the complete
        account-specific leverage/risk bracket table. Without separately
        supplied, provenance-labelled risk brackets this method returns
        INCOMPLETE and must not be registered as a complete contract snapshot.
        No signed/account endpoint is called here.
        """
        symbol = self._symbol(symbol)
        raw, retrieved = self._get("/fapi/v1/exchangeInfo", {})
        if not isinstance(raw, Mapping) or not isinstance(raw.get("symbols"), list):
            raise DataValidationError("exchangeInfo payload must contain a symbols list")
        matches = [row for row in raw["symbols"]
                   if isinstance(row, Mapping) and str(row.get("symbol", "")).upper() == symbol]
        if len(matches) != 1:
            raise DataValidationError(f"exchangeInfo must contain exactly one record for {symbol}")
        row = matches[0]
        filters = row.get("filters")
        if not isinstance(filters, list):
            raise DataValidationError("exchangeInfo symbol filters are missing")
        by_type = {str(item.get("filterType")): item for item in filters if isinstance(item, Mapping)}
        required_filters = ("PRICE_FILTER", "LOT_SIZE", "MIN_NOTIONAL")
        missing = [name for name in required_filters if name not in by_type]
        if missing:
            raise DataValidationError("exchangeInfo missing filters: " + ", ".join(missing))
        price_filter, lot_filter, notional_filter = (
            by_type["PRICE_FILTER"], by_type["LOT_SIZE"], by_type["MIN_NOTIONAL"]
        )
        try:
            tick = _number(price_filter["tickSize"], "tickSize", positive=True)
            step = _number(lot_filter["stepSize"], "stepSize", positive=True)
            min_qty = _number(lot_filter["minQty"], "minQty", positive=True)
            max_qty = _number(lot_filter["maxQty"], "maxQty", positive=True)
            min_notional = _number(notional_filter.get("notional", notional_filter.get("minNotional")),
                                   "minNotional", positive=True)
            onboard_ms = int(row["onboardDate"])
            contract_status = str(row["contractStatus"]).upper()
            contract_type = str(row["contractType"]).upper()
            quote_asset = str(row["quoteAsset"]).strip()
            margin_asset = str(row["marginAsset"]).strip()
            base_asset = str(row["baseAsset"]).strip()
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise DataValidationError("exchangeInfo contract fields are malformed") from exc
        if contract_type != "PERPETUAL" or not quote_asset or not margin_asset or not base_asset:
            raise DataValidationError("only fully identified USD-M perpetual contracts are supported")
        if contract_status not in {"TRADING", "PENDING_TRADING", "SETTLING", "CLOSE", "PRE_DELIVERING", "DELIVERING", "DELIVERED"}:
            raise DataValidationError(f"unsupported Binance contract status: {contract_status}")

        base = {
            "exchange": "binance", "symbol": symbol, "market_type": self.market_type,
            "contract_type": "linear", "exchange_contract_type": contract_type,
            "status": (
                "TRADING" if contract_status == "TRADING"
                else "REDUCE_ONLY" if contract_status == "REDUCE_ONLY"
                else "DELISTED" if contract_status in {"DELIVERED", "DELISTED"}
                else "HALT"
            ),
            "base_asset": base_asset, "quote_asset": quote_asset,
            "margin_asset": margin_asset, "settlement_asset": quote_asset,
            "contract_multiplier": "1", "tick_size": str(price_filter["tickSize"]),
            "step_size": str(lot_filter["stepSize"]), "min_qty": str(lot_filter["minQty"]),
            "max_qty": str(lot_filter["maxQty"]),
            "min_notional": str(notional_filter.get("notional", notional_filter.get("minNotional"))),
            "source": "binance:/fapi/v1/exchangeInfo",
            "version": str(row.get("onboardDate", "exchangeInfo-current")),
            "observed_at": retrieved,
            "effective_from": datetime.fromtimestamp(onboard_ms / 1000, UTC),
            "effective_to": None,
            "exchange_status": contract_status,
            "market_lot_size": dict(by_type["MARKET_LOT_SIZE"]) if "MARKET_LOT_SIZE" in by_type else None,
            "price_filter": dict(price_filter),
            "lot_size_filter": dict(lot_filter),
            "min_notional_filter": dict(notional_filter),
        }

        if not risk_brackets:
            return {
                "status": "CONTRACT_METADATA_INCOMPLETE",
                "registration_ready": False,
                "contract": base,
                "missing_components": [
                    "maintenance_margin_tiers", "max_leverage",
                    "risk_bracket_source", "risk_bracket_version",
                ],
                "warnings": [
                    "Public exchangeInfo does not provide the complete leverage/risk bracket table.",
                    "No leverage or maintenance tier was inferred or fabricated.",
                ],
                "retrieved_at": retrieved,
                "endpoint": "/fapi/v1/exchangeInfo",
            }
        if not risk_bracket_source or not risk_bracket_version:
            raise DataValidationError("risk bracket provenance source and version are required")

        normalized_tiers = []
        for index, bracket in enumerate(risk_brackets):
            if not isinstance(bracket, Mapping):
                raise DataValidationError(f"risk bracket {index} must be an object")
            try:
                floor = _number(bracket["notionalFloor"], f"bracket[{index}].notionalFloor")
                cap = _number(bracket["notionalCap"], f"bracket[{index}].notionalCap", positive=True)
                mmr = _number(bracket["maintMarginRatio"], f"bracket[{index}].maintMarginRatio")
                deduction = _number(bracket["cum"], f"bracket[{index}].cum")
                leverage = _number(bracket["initialLeverage"], f"bracket[{index}].initialLeverage", positive=True)
            except (KeyError, TypeError, ValueError) as exc:
                raise DataValidationError(f"risk bracket {index} is incomplete") from exc
            if floor < 0 or cap <= floor or not 0 <= mmr < 1 or deduction < 0 or leverage < 1:
                raise DataValidationError(f"risk bracket {index} contains invalid bounds or rates")
            normalized_tiers.append({
                "notional_floor": floor, "notional_cap": cap,
                "maintenance_margin_rate": mmr,
                "maintenance_amount": deduction, "max_leverage": leverage,
            })
        normalized_tiers.sort(key=lambda item: item["notional_floor"])
        previous_cap = 0.0
        previous_mmr = 0.0
        previous_deduction = 0.0
        previous_leverage = float("inf")
        for index, tier in enumerate(normalized_tiers):
            if not math.isclose(tier["notional_floor"], previous_cap, rel_tol=1e-9, abs_tol=1e-8):
                raise DataValidationError(f"risk bracket {index} has a gap or overlap")
            if tier["maintenance_margin_rate"] < previous_mmr:
                raise DataValidationError("risk bracket maintenance rates must be non-decreasing")
            if tier["max_leverage"] > previous_leverage:
                raise DataValidationError("risk bracket max leverage must be non-increasing")
            expected_deduction = previous_deduction + previous_cap * (
                tier["maintenance_margin_rate"] - previous_mmr
            )
            if not math.isclose(tier["maintenance_amount"], expected_deduction,
                                rel_tol=1e-6, abs_tol=1e-6):
                raise DataValidationError(f"risk bracket {index} cumulative maintenance deduction is inconsistent")
            previous_cap = tier["notional_cap"]
            previous_mmr = tier["maintenance_margin_rate"]
            previous_deduction = tier["maintenance_amount"]
            previous_leverage = tier["max_leverage"]
        base.update({
            "max_leverage": max(item["max_leverage"] for item in normalized_tiers),
            "maintenance_margin_tiers": normalized_tiers,
            "source": base["source"] + "+" + str(risk_bracket_source),
            "version": base["version"] + "+" + str(risk_bracket_version),
            "risk_bracket_source": str(risk_bracket_source),
            "risk_bracket_version": str(risk_bracket_version),
        })
        return {
            "status": "CONTRACT_METADATA_COMPLETE",
            "registration_ready": True,
            "contract": base,
            "retrieved_at": retrieved,
            "endpoint": "/fapi/v1/exchangeInfo",
            "risk_bracket_provenance": {
                "source": str(risk_bracket_source), "version": str(risk_bracket_version),
                "record_count": len(normalized_tiers),
            },
        }

    def fetch_ohlcv(self, symbol: str, interval: str = "1h", limit: int = 500) -> OHLCVBatch:
        symbol = self._symbol(symbol)
        if interval not in self.intervals:
            raise ValueError(f"unsupported Binance interval: {interval}")
        if isinstance(limit, bool) or not 1 <= int(limit) <= self.config.max_limit:
            raise ValueError(f"limit must be between 1 and {self.config.max_limit}")
        raw, retrieved = self._get(self.klines_endpoint, {"symbol": symbol, "interval": interval, "limit": int(limit)})
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
            provider=self.provider, endpoint=self.klines_endpoint, instrument_id=symbol,
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

    def fetch_premium_index(self, symbol: str) -> dict[str, Any]:
        """Fetch current mark/index and indicative next funding from public USD-M."""
        symbol = self._symbol(symbol)
        raw, retrieved = self._get("/fapi/v1/premiumIndex", {"symbol": symbol})
        if not isinstance(raw, Mapping):
            raise DataValidationError("premium-index payload must be an object")
        try:
            funding_rate = _number(raw["lastFundingRate"], "lastFundingRate")
            mark_price = _number(raw["markPrice"], "markPrice", positive=True)
            index_price = _number(raw["indexPrice"], "indexPrice", positive=True)
            next_funding_at = datetime.fromtimestamp(int(raw["nextFundingTime"]) / 1000, UTC)
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise DataValidationError("malformed premium-index payload") from exc
        return {
            "instrument_id": symbol, "provider": self.provider,
            "market_type": self.market_type, "retrieved_at": retrieved,
            "observed_at": retrieved, "funding_rate": funding_rate,
            "funding_rate_semantics": "latest_exchange_indicative_funding_rate",
            "mark_price": mark_price, "index_price": index_price,
            "next_funding_at": next_funding_at,
            "mark_index_basis_pct": (mark_price / index_price - 1.0) * 100.0,
            "endpoint": "/fapi/v1/premiumIndex",
        }

    def fetch_order_book(self, symbol: str, limit: int = 100) -> dict[str, Any]:
        """Fetch one public L2 snapshot; this is not a sequenced diff stream."""
        symbol = self._symbol(symbol)
        allowed_limits = {5, 10, 20, 50, 100, 500, 1000}
        if isinstance(limit, bool) or int(limit) not in allowed_limits:
            raise ValueError("order-book limit must be one of 5, 10, 20, 50, 100, 500 or 1000")
        raw, retrieved = self._get("/fapi/v1/depth", {"symbol": symbol, "limit": int(limit)})
        if not isinstance(raw, Mapping) or not isinstance(raw.get("bids"), list) or not isinstance(raw.get("asks"), list):
            raise DataValidationError("malformed order-book snapshot")
        def levels(name: str) -> list[dict[str, float]]:
            out = []
            for row in raw[name]:
                if not isinstance(row, Sequence) or len(row) < 2:
                    raise DataValidationError(f"malformed {name} level")
                price = _number(row[0], f"{name}.price", positive=True)
                quantity = _number(row[1], f"{name}.quantity", positive=True)
                out.append({"price": price, "quantity": quantity})
            return out
        bids, asks = levels("bids"), levels("asks")
        if not bids or not asks or max(x["price"] for x in bids) >= min(x["price"] for x in asks):
            raise DataValidationError("order-book snapshot is empty or crossed")
        bid_qty = sum(x["quantity"] for x in bids[:20])
        ask_qty = sum(x["quantity"] for x in asks[:20])
        total = bid_qty + ask_qty
        return {
            "instrument_id": symbol, "provider": self.provider,
            "market_type": self.market_type, "retrieved_at": retrieved,
            "last_update_id": int(raw.get("lastUpdateId", 0)),
            "bids": bids, "asks": asks, "limit": int(limit),
            "top20_quantity_imbalance": (bid_qty - ask_qty) / total if total else None,
            "best_bid": bids[0]["price"], "best_ask": asks[0]["price"],
            "mid_price": (bids[0]["price"] + asks[0]["price"]) / 2.0,
            "spread_bps": (asks[0]["price"] / bids[0]["price"] - 1.0) * 10000.0,
            "sequence_semantics": "single_rest_snapshot_not_continuous_diff_stream",
            "endpoint": "/fapi/v1/depth",
        }

    def fetch_aggregate_trades(self, symbol: str, limit: int = 1000) -> dict[str, Any]:
        """Fetch a bounded recent aggregate-trade sample, not complete tick history."""
        symbol = self._symbol(symbol)
        if isinstance(limit, bool) or not 1 <= int(limit) <= 1000:
            raise ValueError("aggregate-trade limit must be between 1 and 1000")
        raw, retrieved = self._get("/fapi/v1/aggTrades", {"symbol": symbol, "limit": int(limit)})
        if not isinstance(raw, list):
            raise DataValidationError("aggregate-trades payload must be a list")
        records = []
        buy_qty = sell_qty = 0.0
        for row in raw:
            if not isinstance(row, Mapping) or not {"p", "q", "T", "m"}.issubset(row):
                raise DataValidationError("malformed aggregate-trade record")
            price = _number(row["p"], "trade_price", positive=True)
            quantity = _number(row["q"], "trade_quantity", positive=True)
            buyer_is_maker = row["m"]
            if not isinstance(buyer_is_maker, bool):
                raise DataValidationError("aggregate trade maker flag must be boolean")
            if buyer_is_maker:
                sell_qty += quantity
            else:
                buy_qty += quantity
            records.append({
                "price": price, "quantity": quantity,
                "event_time": datetime.fromtimestamp(int(row["T"]) / 1000, UTC),
                "buyer_is_maker": buyer_is_maker,
            })
        records.sort(key=lambda item: item["event_time"])
        return {
            "instrument_id": symbol, "provider": self.provider,
            "market_type": self.market_type, "retrieved_at": retrieved,
            "records": records, "record_count": len(records),
            "taker_buy_base_quantity": buy_qty,
            "taker_sell_base_quantity": sell_qty,
            "sampled_net_taker_flow_base_quantity": buy_qty - sell_qty,
            "sample_semantics": "bounded_recent_aggregate_trade_sample_not_complete_tick_history",
            "endpoint": "/fapi/v1/aggTrades",
        }

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


class BinanceSpotPublicAdapter(BinanceUSDMPublicAdapter):
    """Public Binance spot candles; separate identity from USD-M futures."""

    provider = "binance_spot"
    market_type = "spot"
    base_url = "https://api.binance.com"
    klines_endpoint = "/api/v3/klines"
    supports_derivatives = False


class CoinbaseExchangeSpotAdapter:
    """Public Coinbase Exchange spot candles; no credentials or account scope."""

    provider = "coinbase_exchange_spot"
    market_type = "spot"
    base_url = "https://api.exchange.coinbase.com"
    supports_derivatives = False
    intervals = MappingProxyType({
        "1m": 60, "5m": 300, "15m": 900, "1h": 3600,
        "6h": 21600, "1d": 86400,
    })

    def __init__(
        self,
        config: ProviderConfig | None = None,
        *,
        transport: Callable[[str, float], tuple[int, Mapping[str, str], bytes]] | None = None,
        clock: Callable[[], float] = time.time,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config or ProviderConfig(max_limit=300)
        self._transport = transport or BinanceUSDMPublicAdapter._http_get
        self._clock = clock
        self._sleep = sleeper

    @staticmethod
    def _product(value: str) -> str:
        product = str(value).strip().upper()
        parts = product.split("-")
        if len(parts) != 2 or not all(part.isalnum() for part in parts):
            raise ValueError("Coinbase product must use BASE-QUOTE format, e.g. BTC-USD")
        return product

    def _get(self, path: str, params: Mapping[str, Any]) -> tuple[Any, datetime]:
        if self.base_url != "https://api.exchange.coinbase.com" or not path.startswith("/products/"):
            raise ValueError("Coinbase adapter is pinned to the public products endpoint")
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
                    raise DataValidationError("Coinbase returned invalid JSON") from exc
            if status == 429:
                if attempt >= self.config.max_retries:
                    raise ProviderRateLimitError("Coinbase rate limit retry budget exhausted")
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
            raise ProviderUnavailableError(f"Coinbase returned HTTP {status}")
        raise ProviderUnavailableError("Coinbase retry budget exhausted")

    def fetch_ohlcv(self, symbol: str, interval: str = "1h", limit: int = 300) -> OHLCVBatch:
        product = self._product(symbol)
        if interval not in self.intervals:
            raise ValueError(f"unsupported Coinbase candle interval: {interval}")
        if isinstance(limit, bool) or not 1 <= int(limit) <= min(300, self.config.max_limit):
            raise ValueError(f"limit must be between 1 and {min(300, self.config.max_limit)}")
        path = f"/products/{product}/candles"
        raw, retrieved = self._get(path, {"granularity": self.intervals[interval]})
        if not isinstance(raw, list):
            raise DataValidationError("Coinbase candles payload must be a list")
        bars = []
        for row in raw:
            if not isinstance(row, Sequence) or len(row) < 6:
                raise DataValidationError("malformed Coinbase candle row")
            opened = datetime.fromtimestamp(int(row[0]), UTC)
            bars.append(OHLCVBar(
                instrument_id=product, provider=self.provider, interval=interval,
                open_time=opened,
                close_time=datetime.fromtimestamp(int(row[0]) + self.intervals[interval] - 1, UTC),
                low=row[1], high=row[2], open=row[3], close=row[4], volume=row[5],
            ))
        bars.sort(key=lambda bar: bar.open_time)
        if len(bars) > int(limit):
            bars = bars[-int(limit):]
        warnings = []
        if len(bars) > 1:
            deltas = [(bars[i].open_time - bars[i-1].open_time).total_seconds()
                      for i in range(1, len(bars))]
            if any(delta > self.intervals[interval] * 1.5 for delta in deltas):
                warnings.append("candle history contains gaps; no interpolation was performed")
        if bars and bars[-1].close_time >= retrieved:
            warnings.append("latest candle may still be forming; downstream closed-bar policy must exclude it")
        provenance = DataProvenance(
            provider=self.provider, endpoint=path, instrument_id=product,
            retrieved_at=retrieved, source_timestamp_semantics="coinbase_exchange_candle_start_time_utc",
            market_type=self.market_type, raw_record_count=len(raw), normalized_record_count=len(bars),
        )
        return OHLCVBatch(tuple(bars), provenance, tuple(warnings))


class MarketDataHub:
    """Small provider registry. Data is returned with source provenance, never blended."""

    def __init__(self, adapters: Mapping[str, Any] | None = None) -> None:
        self._adapters = dict(adapters or {})

    def register(self, name: str, adapter: Any) -> None:
        key = str(name).strip().lower()
        if not key or key in self._adapters:
            raise ValueError("provider name must be non-empty and unique")
        if not callable(getattr(adapter, "fetch_ohlcv", None)):
            raise TypeError("adapter is missing fetch_ohlcv")
        self._adapters[key] = adapter

    def provider(self, name: str) -> Any:
        try:
            return self._adapters[str(name).strip().lower()]
        except KeyError as exc:
            raise MarketDataError(f"unknown market-data provider: {name}") from exc

    def fetch_ohlcv(self, provider: str, symbol: str, interval: str = "1h", limit: int = 500) -> OHLCVBatch:
        adapter = self.provider(provider)
        batch = adapter.fetch_ohlcv(symbol, interval, limit)
        if not isinstance(batch, OHLCVBatch):
            raise DataValidationError("provider returned a non-normalized OHLCV result")
        expected_providers = {str(provider).strip().lower()}
        adapter_provider = getattr(adapter, "provider", None)
        if adapter_provider:
            expected_providers.add(str(adapter_provider).strip().lower())
        provenance_provider = str(getattr(batch.provenance, "provider", "")).strip().lower()
        if provenance_provider not in expected_providers:
            raise DataValidationError("provider identity mismatch in OHLCV provenance")
        if any(str(bar.provider).strip().lower() not in expected_providers for bar in batch.bars):
            raise DataValidationError("provider identity mismatch in OHLCV records")
        return batch

    def fetch_derivatives(self, provider: str, symbol: str, *, oi_period: str = "1h",
                          funding_limit: int = 100, oi_limit: int = 30) -> dict[str, Any]:
        adapter = self.provider(provider)
        if not getattr(adapter, "supports_derivatives", False):
            raise MarketDataError(f"provider {provider} does not expose derivatives history")
        if not callable(getattr(adapter, "fetch_funding_history", None)) or not callable(getattr(adapter, "fetch_open_interest_history", None)):
            raise MarketDataError(f"provider {provider} is missing derivatives capabilities")
        funding = adapter.fetch_funding_history(symbol, funding_limit)
        oi = adapter.fetch_open_interest_history(symbol, oi_period, oi_limit)
        if funding["provider"] != oi["provider"] or funding["market_type"] != oi["market_type"]:
            raise DataValidationError("derivatives provider or market type mismatch")
        for section in (funding, oi):
            if not isinstance(section.get("records"), list):
                raise DataValidationError("derivatives provider records must be a list")
            for record in section["records"]:
                if not isinstance(record, Mapping):
                    raise DataValidationError("derivatives observations must be mappings")
                if record.get("provider") != funding["provider"] or record.get("instrument_id") != str(symbol).strip().upper():
                    raise DataValidationError("derivatives observation identity mismatch")
        return {"provider": funding["provider"], "market_type": funding["market_type"],
                "funding": funding, "open_interest": oi, "actionable": False,
                "policy": "Independent provider observations; no synthetic merge or position inference."}


__all__ = [
    "BinanceSpotPublicAdapter", "BinanceUSDMPublicAdapter", "CoinbaseExchangeSpotAdapter",
    "DataProvenance", "DataValidationError",
    "MarketDataError", "MarketDataHub", "OHLCVBar", "OHLCVBatch",
    "ProviderConfig", "ProviderRateLimitError", "ProviderUnavailableError",
]
