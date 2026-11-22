from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Literal
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict
from websockets.asyncio.client import connect

from app.market_data.symbol import Symbol


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
    best_bid: Decimal | None = None
    best_bid_quantity: Decimal | None = None
    best_ask: Decimal | None = None
    best_ask_quantity: Decimal | None = None
    last_trade_price: Decimal | None = None
    last_trade_quantity: Decimal | None = None
    last_trade_time: datetime | None = None
    observed_at: datetime | None = None
    last_error: str | None = None


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


class BinanceFuturesMarketStream:
    """Read-only public market stream with bounded state and automatic reconnect."""

    def __init__(self, symbol: str, *, reconnect_delay: float = 1.0, max_reconnect_delay: float = 30.0):
        self.symbol = str(Symbol(symbol))
        self.reconnect_delay = reconnect_delay
        self.max_reconnect_delay = max_reconnect_delay
        self.state: Literal["connecting", "connected", "reconnecting", "stopped"] = "stopped"
        self.last_error: str | None = None
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
            symbol=self.symbol,
            state=self.state,
            last_error=self.last_error,
            **self._values,
        ).model_dump(mode="json")

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
                            self.ingest(payload)
                    self.last_error = "Connection closed by exchange"
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - keep the read-only feed resilient
                self.last_error = type(exc).__name__
            self.state = "reconnecting"
            await asyncio.sleep(delay)
            delay = min(delay * 2, self.max_reconnect_delay)


_streams: dict[str, BinanceFuturesMarketStream] = {}


def get_futures_market_stream(symbol: str) -> BinanceFuturesMarketStream:
    normalized = str(Symbol(symbol))
    stream = _streams.get(normalized)
    if stream is None:
        stream = BinanceFuturesMarketStream(normalized)
        _streams[normalized] = stream
    return stream


async def stop_all_futures_market_streams() -> None:
    streams = list(_streams.values())
    await asyncio.gather(*(stream.stop() for stream in streams))
    _streams.clear()
