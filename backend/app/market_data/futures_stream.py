from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict
from websockets.asyncio.client import connect

from app.auth import csrf_check, current_user
from app.market_data.symbol import Symbol
from app.settings import settings


class FuturesStreamSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider: str = "binance_usdm_futures"
    market_type: str = "linear_perpetual"
    symbol: str
    state: Literal["connecting", "connected", "reconnecting", "stopped"]
    mark_price: Decimal | None = None
    index_price: Decimal | None = None
    funding_rate: Decimal | None = None
    next_funding_time: datetime | None = None
    open_interest: Decimal | None = None
    open_interest_value: Decimal | None = None
    best_bid: Decimal | None = None
    best_bid_quantity: Decimal | None = None
    best_ask: Decimal | None = None
    best_ask_quantity: Decimal | None = None
    last_trade_price: Decimal | None = None
    last_trade_quantity: Decimal | None = None
    last_trade_time: datetime | None = None
    observed_at: datetime | None = None
    last_error: str | None = None
    persistence_state: str = "disabled"
    persistence_error: str | None = None


def _positive_decimal(value: object) -> Decimal:
    result = Decimal(str(value))
    if not result.is_finite() or result <= 0:
        raise ValueError("market price and quantity values must be finite and positive")
    return result


def _event_time(value: object) -> datetime:
    return datetime.fromtimestamp(int(value) / 1000, tz=UTC)


def normalize_binance_market_event(payload: dict, expected_symbol: str) -> dict | None:
    """Normalize Binance USD-M mark-price, book-ticker and aggregate-trade events."""
    if isinstance(payload.get("data"), dict):
        payload = payload["data"]
    event_type = payload.get("e")
    if event_type not in {"markPriceUpdate", "bookTicker", "aggTrade"}:
        return None
    try:
        symbol = str(Symbol(str(payload["s"])))
        if symbol != expected_symbol:
            return None
        if event_type == "markPriceUpdate":
            return {
                "kind": "mark",
                "symbol": symbol,
                "mark_price": _positive_decimal(payload["p"]),
                "index_price": _positive_decimal(payload["i"]),
                "funding_rate": Decimal(str(payload["r"])),
                "next_funding_time": _event_time(payload["T"]),
                "observed_at": _event_time(payload["E"]),
            }
        if event_type == "bookTicker":
            return {
                "kind": "book",
                "symbol": symbol,
                "best_bid": _positive_decimal(payload["b"]),
                "best_bid_quantity": _positive_decimal(payload["B"]),
                "best_ask": _positive_decimal(payload["a"]),
                "best_ask_quantity": _positive_decimal(payload["A"]),
                "observed_at": datetime.now(UTC),
            }
        return {
            "kind": "trade",
            "symbol": symbol,
            "last_trade_price": _positive_decimal(payload["p"]),
            "last_trade_quantity": _positive_decimal(payload["q"]),
            "last_trade_time": _event_time(payload["T"]),
            "observed_at": _event_time(payload["E"]),
        }
    except (KeyError, ValueError, TypeError, InvalidOperation, ArithmeticError, OverflowError):
        return None


def _nonnegative_decimal(value: object) -> Decimal:
    result = Decimal(str(value))
    if not result.is_finite() or result < 0:
        raise ValueError("market size values must be finite and non-negative")
    return result


def normalize_bybit_market_event(payload: dict, expected_symbol: str) -> dict | None:
    """Normalize Bybit V5 linear ticker and public-trade messages."""
    topic = payload.get("topic")
    try:
        if topic == f"tickers.{expected_symbol}":
            rows = payload.get("data")
            if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
                return None
            row = rows[0]
            symbol = str(Symbol(str(row.get("symbol", expected_symbol))))
            if symbol != expected_symbol:
                return None
            normalized = {
                "kind": "ticker",
                "symbol": symbol,
                "observed_at": _event_time(payload["ts"]),
            }
            positive_fields = {
                "markPrice": "mark_price",
                "indexPrice": "index_price",
                "bid1Price": "best_bid",
                "ask1Price": "best_ask",
            }
            size_fields = {
                "openInterest": "open_interest",
                "openInterestValue": "open_interest_value",
                "bid1Size": "best_bid_quantity",
                "ask1Size": "best_ask_quantity",
            }
            for source, target in positive_fields.items():
                if source in row and row[source] not in (None, ""):
                    normalized[target] = _positive_decimal(row[source])
            for source, target in size_fields.items():
                if source in row and row[source] not in (None, ""):
                    normalized[target] = _nonnegative_decimal(row[source])
            if "fundingRate" in row and row["fundingRate"] not in (None, ""):
                rate = Decimal(str(row["fundingRate"]))
                if not rate.is_finite():
                    return None
                normalized["funding_rate"] = rate
            if "nextFundingTime" in row and row["nextFundingTime"] not in (None, ""):
                normalized["next_funding_time"] = _event_time(row["nextFundingTime"])
            return normalized if len(normalized) > 3 else None

        if topic == f"publicTrade.{expected_symbol}":
            rows = payload.get("data")
            if not isinstance(rows, list) or not rows:
                return None
            row = rows[-1]
            if not isinstance(row, dict):
                return None
            symbol = str(Symbol(str(row["s"])))
            if symbol != expected_symbol:
                return None
            return {
                "kind": "trade",
                "symbol": symbol,
                "last_trade_price": _positive_decimal(row["p"]),
                "last_trade_quantity": _positive_decimal(row["v"]),
                "last_trade_time": _event_time(row["T"]),
                "observed_at": _event_time(payload["ts"]),
            }
    except (KeyError, ValueError, TypeError, InvalidOperation, ArithmeticError, OverflowError):
        return None
    return None


class BinanceFuturesMarketStream:
    """Read-only public market stream with bounded state and automatic reconnect."""

    provider = "binance_usdm_futures"

    def __init__(self, symbol: str, *, reconnect_delay: float = 1.0, max_reconnect_delay: float = 30.0):
        self.symbol = str(Symbol(symbol))
        self.reconnect_delay = reconnect_delay
        self.max_reconnect_delay = max_reconnect_delay
        self.state: Literal["connecting", "connected", "reconnecting", "stopped"] = "stopped"
        self.last_error: str | None = None
        self.persistence_state = "disabled"
        self.persistence_error: str | None = None
        self._values: dict = {}
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self.state = "connecting"
            self._task = asyncio.create_task(self._run(), name=f"futures-market-{self.symbol}")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self.state = "stopped"

    def ingest(self, payload: dict) -> bool:
        normalized = normalize_binance_market_event(payload, self.symbol)
        if normalized is None:
            return False
        kind = normalized.pop("kind")
        normalized.pop("symbol", None)
        self._values.update(normalized)
        return kind in {"mark", "book", "trade"}

    def snapshot(self) -> dict:
        return FuturesStreamSnapshot(
            provider=self.provider,
            symbol=self.symbol,
            state=self.state,
            last_error=self.last_error,
            persistence_state=self.persistence_state,
            persistence_error=self.persistence_error,
            **self._values,
        ).model_dump(mode="json")


    async def _persist_mark_snapshot(self) -> None:
        try:
            from app.market_data.futures_snapshot_repository import save_futures_mark_snapshot

            await save_futures_mark_snapshot(
                provider=self.provider,
                symbol=self.symbol,
                observed_at=self._values["observed_at"],
                mark_price=self._values["mark_price"],
                index_price=self._values["index_price"],
                funding_rate=self._values["funding_rate"],
                next_funding_time=self._values["next_funding_time"],
            )
            from app.database import database_enabled

            self.persistence_state = "available" if database_enabled() else "disabled"
            self.persistence_error = None
        except Exception as exc:  # noqa: BLE001 - database failures must not stop market feed
            self.persistence_state = "unavailable"
            self.persistence_error = type(exc).__name__

    async def _run(self) -> None:
        streams = "/".join((
            f"{self.symbol.lower()}@markPrice@1s",
            f"{self.symbol.lower()}@bookTicker",
            f"{self.symbol.lower()}@aggTrade",
        ))
        url = f"wss://fstream.binance.com/market/stream?streams={quote(streams, safe='@/')}"
        delay = self.reconnect_delay
        while True:
            try:
                async with connect(url, ping_interval=20, ping_timeout=20, open_timeout=10) as socket:
                    self.state = "connected"
                    self.last_error = None
                    delay = self.reconnect_delay
                    async for raw in socket:
                        payload = json.loads(raw)
                        if isinstance(payload, dict):
                            accepted = self.ingest(payload)
                            event = payload.get("data", payload)
                            if (
                                accepted
                                and isinstance(event, dict)
                                and event.get("e") == "markPriceUpdate"
                            ):
                                await self._persist_mark_snapshot()
                    self.last_error = "Connection closed by exchange"
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - keep the read-only feed resilient
                self.last_error = type(exc).__name__
            self.state = "reconnecting"
            await asyncio.sleep(delay)
            delay = min(delay * 2, self.max_reconnect_delay)


class BybitLinearFuturesMarketStream(BinanceFuturesMarketStream):
    """Read-only Bybit V5 linear ticker/trade stream with reconnect and persistence."""

    provider = "bybit_linear_futures"

    def __init__(self, symbol: str, *, reconnect_delay: float = 1.0, max_reconnect_delay: float = 30.0):
        super().__init__(
            symbol,
            reconnect_delay=reconnect_delay,
            max_reconnect_delay=max_reconnect_delay,
        )
        self._last_persisted_at: datetime | None = None

    def ingest(self, payload: dict) -> bool:
        normalized = normalize_bybit_market_event(payload, self.symbol)
        if normalized is None:
            return False
        kind = normalized.pop("kind")
        normalized.pop("symbol", None)
        self._values.update(normalized)
        return kind in {"ticker", "trade"}

    async def _heartbeat(self, socket) -> None:
        while True:
            await asyncio.sleep(20)
            await socket.send(json.dumps({"op": "ping"}))

    async def _run(self) -> None:
        url = "wss://stream.bybit.com/v5/public/linear"
        topics = [f"tickers.{self.symbol}", f"publicTrade.{self.symbol}"]
        delay = self.reconnect_delay
        while True:
            try:
                async with connect(
                    url,
                    ping_interval=None,
                    open_timeout=10,
                ) as socket:
                    await socket.send(json.dumps({"op": "subscribe", "args": topics}))
                    self.state = "connected"
                    self.last_error = None
                    delay = self.reconnect_delay
                    heartbeat = asyncio.create_task(self._heartbeat(socket))
                    try:
                        async for raw in socket:
                            payload = json.loads(raw)
                            if isinstance(payload, dict):
                                if payload.get("op") == "subscribe" and payload.get("success") is False:
                                    raise RuntimeError("Bybit public market stream subscription rejected")
                                accepted = self.ingest(payload)
                                if (
                                    accepted
                                    and payload.get("topic") == f"tickers.{self.symbol}"
                                    and all(
                                        key in self._values
                                        for key in (
                                            "mark_price",
                                            "index_price",
                                            "funding_rate",
                                            "next_funding_time",
                                            "observed_at",
                                        )
                                    )
                                ):
                                    observed_at = self._values["observed_at"]
                                    if (
                                        self._last_persisted_at is None
                                        or (observed_at - self._last_persisted_at).total_seconds() >= 1
                                    ):
                                        await self._persist_mark_snapshot()
                                        self._last_persisted_at = observed_at
                    finally:
                        heartbeat.cancel()
                        await asyncio.gather(heartbeat, return_exceptions=True)
                    self.last_error = "Connection closed by exchange"
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - keep the read-only feed resilient
                self.last_error = type(exc).__name__
            self.state = "reconnecting"
            await asyncio.sleep(delay)
            delay = min(delay * 2, self.max_reconnect_delay)


_streams: dict[tuple[str, str], BinanceFuturesMarketStream] = {}


def get_futures_market_stream(symbol: str) -> BinanceFuturesMarketStream:
    normalized = str(Symbol(symbol))
    provider = settings.futures_market_data_provider
    key = (provider, normalized)
    stream = _streams.get(key)
    if stream is None:
        providers = {
            "binance_usdm_futures": BinanceFuturesMarketStream,
            "bybit_linear_futures": BybitLinearFuturesMarketStream,
        }
        stream_type = providers.get(provider)
        if stream_type is None:
            raise RuntimeError("No supported futures market stream is configured")
        stream = stream_type(normalized)
        _streams[key] = stream
    return stream


def active_futures_market_stream_count(provider: str) -> int:
    return sum(
        stream.provider == provider and stream.state != "stopped"
        for stream in _streams.values()
    )


async def stop_all_futures_market_streams() -> None:
    streams = list(_streams.values())
    await asyncio.gather(*(stream.stop() for stream in streams))
    _streams.clear()

router = APIRouter(prefix="/api/v1/derivatives", tags=["derivatives"])


@router.get("/live-market")
async def live_futures_market(
    request: Request,
    symbol: str = Query(min_length=2, max_length=30),
):
    """Return the latest live-market snapshot without starting a collector."""
    current_user(request)
    try:
        stream = get_futures_market_stream(symbol)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return stream.snapshot()


@router.post("/live-market")
async def start_live_futures_market(
    request: Request,
    symbol: str = Query(min_length=2, max_length=30),
):
    """Start the configured public market stream after CSRF validation."""
    current_user(request)
    csrf_check(request)
    try:
        stream = get_futures_market_stream(symbol)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if (
        stream.state == "stopped"
        and active_futures_market_stream_count(stream.provider)
        >= settings.max_active_futures_market_streams
    ):
        raise HTTPException(
            status_code=429,
            detail="Maximum active Futures market streams reached for this provider",
        )
    stream.start()
    return stream.snapshot()


@router.delete("/live-market")
async def stop_live_futures_market(
    request: Request,
    symbol: str = Query(min_length=2, max_length=30),
):
    """Stop the selected public market stream while retaining its last snapshot."""
    current_user(request)
    csrf_check(request)
    try:
        stream = get_futures_market_stream(symbol)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await stream.stop()
    return stream.snapshot()


@router.get("/market-history")
async def futures_market_history(
    request: Request,
    symbol: str = Query(min_length=2, max_length=30),
    hours: int = Query(default=24, ge=1, le=720),
    limit: int = Query(default=1000, ge=1, le=5000),
):
    """Return persisted mark-price and funding observations for charting/backtests."""
    current_user(request)
    try:
        normalized = str(Symbol(symbol))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    from app.market_data.futures_snapshot_repository import list_futures_mark_snapshots

    try:
        rows = await list_futures_mark_snapshots(
            normalized,
            datetime.now(UTC) - timedelta(hours=hours),
            limit=limit,
            provider=settings.futures_market_data_provider,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if rows is None:
        raise HTTPException(status_code=503, detail="Futures history persistence is not configured")
    return {
        "provider": settings.futures_market_data_provider,
        "market_type": "linear_perpetual",
        "symbol": normalized,
        "hours": hours,
        "count": len(rows),
        "snapshots": rows,
    }
