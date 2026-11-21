from __future__ import annotations

import asyncio
import hashlib
import json
from collections import deque
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from websockets.asyncio.client import connect

from app.market_data.symbol import Symbol


class LiquidationEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_id: str
    provider: str
    symbol: str
    event_time: datetime
    liquidation_side: Literal["long", "short"]
    order_side: Literal["BUY", "SELL"]
    quantity: Decimal = Field(gt=0)
    price: Decimal = Field(gt=0)
    notional: Decimal = Field(gt=0)
    price_kind: Literal["average_execution", "bankruptcy"]


class LiquidationStreamStatus(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider: str
    symbol: str
    state: Literal["connecting", "connected", "reconnecting", "stopped"]
    event_count: int
    last_event_time: datetime | None = None
    last_error: str | None = None


def _event_id(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:24]


def parse_binance_liquidation(payload: dict) -> LiquidationEvent | None:
    order = payload.get("o")
    if payload.get("e") != "forceOrder" or not isinstance(order, dict):
        return None
    try:
        symbol = str(Symbol(str(order["s"])))
        side = str(order["S"]).upper()
        if side not in {"BUY", "SELL"}:
            return None
        quantity = Decimal(str(order.get("l") or order["q"]))
        price = Decimal(str(order.get("ap") or order.get("p")))
        event_ms = int(order.get("T") or payload["E"])
        if quantity <= 0 or price <= 0:
            return None
        event_time = datetime.fromtimestamp(event_ms / 1000, tz=UTC)
        return LiquidationEvent(
            event_id=_event_id(
                "binance", symbol, str(event_ms), side, str(quantity), str(price)
            ),
            provider="binance_usdm_futures",
            symbol=symbol,
            event_time=event_time,
            liquidation_side="long" if side == "SELL" else "short",
            order_side=side,
            quantity=quantity,
            price=price,
            notional=quantity * price,
            price_kind="average_execution",
        )
    except (KeyError, ValueError, TypeError, InvalidOperation, ArithmeticError):
        return None


def parse_bybit_liquidations(payload: dict) -> list[LiquidationEvent]:
    topic = payload.get("topic")
    rows = payload.get("data")
    if not isinstance(topic, str) or not topic.startswith("allLiquidation."):
        return []
    if not isinstance(rows, list):
        return []
    events: list[LiquidationEvent] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            symbol = str(Symbol(str(row["s"])))
            side = str(row["S"]).capitalize()
            if side not in {"Buy", "Sell"}:
                continue
            quantity = Decimal(str(row["v"]))
            price = Decimal(str(row["p"]))
            event_ms = int(row["T"])
            if quantity <= 0 or price <= 0:
                continue
            event_time = datetime.fromtimestamp(event_ms / 1000, tz=UTC)
            order_side = side.upper()
            events.append(
                LiquidationEvent(
                    event_id=_event_id(
                        "bybit", symbol, str(event_ms), order_side, str(quantity), str(price)
                    ),
                    provider="bybit_linear_futures",
                    symbol=symbol,
                    event_time=event_time,
                    liquidation_side="long" if side == "Buy" else "short",
                    order_side=order_side,
                    quantity=quantity,
                    price=price,
                    notional=quantity * price,
                    price_kind="bankruptcy",
                )
            )
        except (KeyError, ValueError, TypeError, InvalidOperation, ArithmeticError):
            continue
    return events


class LiquidationCollector:
    """Bounded in-memory collector for exchange public liquidation snapshots."""

    def __init__(self, provider: str, symbol: str, max_events: int = 2000):
        self.provider = provider
        self.symbol = str(Symbol(symbol))
        self.events: deque[LiquidationEvent] = deque(maxlen=max_events)
        self._seen: set[str] = set()
        self._task: asyncio.Task | None = None
        self.state: Literal["connecting", "connected", "reconnecting", "stopped"] = "stopped"
        self.last_error: str | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self.state = "connecting"
            self._task = asyncio.create_task(self._run(), name=f"liquidations-{self.provider}-{self.symbol}")

    async def stop(self) -> None:
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self.state = "stopped"

    def add(self, event: LiquidationEvent) -> None:
        if event.event_id in self._seen:
            return
        if len(self.events) == self.events.maxlen and self.events:
            self._seen.discard(self.events[-1].event_id)
        self.events.appendleft(event)
        self._seen.add(event.event_id)

    def snapshot(self, limit: int = 100) -> dict:
        status = LiquidationStreamStatus(
            provider=self.provider,
            symbol=self.symbol,
            state=self.state,
            event_count=len(self.events),
            last_event_time=self.events[0].event_time if self.events else None,
            last_error=self.last_error,
        )
        return {
            "status": status.model_dump(mode="json"),
            "events": [event.model_dump(mode="json") for event in list(self.events)[:limit]],
            "retention": "in_memory_since_collector_start",
        }

    async def _run(self) -> None:
        while True:
            try:
                if self.provider == "binance_usdm_futures":
                    await self._run_binance()
                else:
                    await self._run_bybit()
                self.last_error = "Connection closed by exchange"
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - reconnect on any transport or payload failure
                self.last_error = type(exc).__name__
            self.state = "reconnecting"
            await asyncio.sleep(2)

    async def _run_binance(self) -> None:
        url = f"wss://fstream.binance.com/ws/{self.symbol.lower()}@forceOrder"
        async with connect(url, ping_interval=20, ping_timeout=20, open_timeout=10) as socket:
            self.state = "connected"
            self.last_error = None
            async for raw in socket:
                payload = json.loads(raw)
                if isinstance(payload, dict):
                    event = parse_binance_liquidation(payload)
                    if event is not None and event.symbol == self.symbol:
                        self.add(event)

    async def _run_bybit(self) -> None:
        url = "wss://stream.bybit.com/v5/public/linear"
        async with connect(url, ping_interval=20, ping_timeout=20, open_timeout=10) as socket:
            await socket.send(json.dumps({"op": "subscribe", "args": [f"allLiquidation.{self.symbol}"]}))
            self.state = "connected"
            self.last_error = None
            async for raw in socket:
                payload = json.loads(raw)
                if isinstance(payload, dict):
                    for event in parse_bybit_liquidations(payload):
                        if event.symbol == self.symbol:
                            self.add(event)


_collectors: dict[tuple[str, str], LiquidationCollector] = {}


def get_collector(provider: str, symbol: str) -> LiquidationCollector:
    key = (provider, str(Symbol(symbol)))
    collector = _collectors.get(key)
    if collector is None:
        collector = LiquidationCollector(*key)
        _collectors[key] = collector
    return collector


async def stop_all_collectors() -> None:
    collectors = list(_collectors.values())
    await asyncio.gather(*(collector.stop() for collector in collectors))
    _collectors.clear()
