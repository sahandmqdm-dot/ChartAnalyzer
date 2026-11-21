"""Snapshot-backed local L2 order-book state for Binance Spot and USD-M."""
from __future__ import annotations

import asyncio
import json
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.request import Request, urlopen

from binance_stream_collector import MarketSpec, OrderBookSequence, SequenceGap


class OrderBookError(RuntimeError):
    """Invalid or unavailable order-book snapshot."""


@dataclass(frozen=True, slots=True)
class DepthSnapshot:
    last_update_id: int
    bids: tuple[tuple[float, float], ...]
    asks: tuple[tuple[float, float], ...]
    market: str
    symbol: str


def _level(row: Any) -> tuple[float, float]:
    if not isinstance(row, (list, tuple)) or len(row) != 2:
        raise OrderBookError("malformed depth level")
    price, quantity = float(row[0]), float(row[1])
    if not math.isfinite(price) or not math.isfinite(quantity) or price <= 0 or quantity < 0:
        raise OrderBookError("invalid depth price or quantity")
    return price, quantity


def fetch_depth_snapshot(spec: MarketSpec, *, limit: int = 1000, timeout: float = 10.0,
                         opener: Callable[..., Any] = urlopen) -> DepthSnapshot:
    """Fetch a public REST snapshot. urllib honors configured system proxy variables."""
    if limit not in (5, 10, 20, 50, 100, 500, 1000, 5000):
        raise ValueError("unsupported Binance depth snapshot limit")
    path = "/api/v3/depth" if spec.market == "spot" else "/fapi/v1/depth"
    url = f"{spec.rest_url}{path}?symbol={spec.symbol.upper()}&limit={limit}"
    request = Request(url, headers={"Accept": "application/json",
                                    "User-Agent": "ChartAnalyzer/DepthSnapshot"})
    try:
        with opener(request, timeout=timeout) as response:
            payload = json.loads(response.read())
    except Exception as exc:
        raise OrderBookError(f"depth snapshot request failed: {type(exc).__name__}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("lastUpdateId"), int):
        raise OrderBookError("snapshot missing integer lastUpdateId")
    bids = tuple(_level(row) for row in payload.get("bids", []))
    asks = tuple(_level(row) for row in payload.get("asks", []))
    if not bids or not asks:
        raise OrderBookError("snapshot must contain both sides")
    if max(price for price, _ in bids) >= min(price for price, _ in asks):
        raise OrderBookError("crossed snapshot")
    return DepthSnapshot(payload["lastUpdateId"], bids, asks, spec.market, spec.symbol)


class LocalOrderBook:
    """In-memory L2 book; it is readable only after a contiguous diff bridges snapshot."""

    def __init__(self, market: str, symbol: str) -> None:
        if market not in {"spot", "usdm"}:
            raise ValueError("market must be spot or usdm")
        self.market = market
        self.symbol = symbol.upper()
        self.sequence = OrderBookSequence()
        self.bids: dict[float, float] = {}
        self.asks: dict[float, float] = {}

    @property
    def valid(self) -> bool:
        return self.sequence.valid

    def install_snapshot(self, snapshot: DepthSnapshot) -> None:
        if snapshot.market != self.market or snapshot.symbol.upper() != self.symbol:
            raise OrderBookError("snapshot market identity mismatch")
        self.bids = {price: qty for price, qty in snapshot.bids if qty > 0}
        self.asks = {price: qty for price, qty in snapshot.asks if qty > 0}
        self.sequence.install_snapshot(snapshot.last_update_id)

    def apply_diff(self, event: dict[str, Any]) -> bool:
        try:
            applied = self.sequence.apply_diff(event)
        except (KeyError, TypeError, ValueError, SequenceGap):
            self.sequence.valid = False
            raise
        if not applied:
            return False
        bids = [_level(row) for row in event.get("b", [])]
        asks = [_level(row) for row in event.get("a", [])]
        for side, updates in ((self.bids, bids), (self.asks, asks)):
            for price, quantity in updates:
                if quantity == 0:
                    side.pop(price, None)
                else:
                    side[price] = quantity
        if self.bids and self.asks and max(self.bids) >= min(self.asks):
            self.sequence.valid = False
            raise OrderBookError("depth update produced crossed book")
        return True

    def top(self, depth: int = 10) -> dict[str, Any]:
        if not self.valid:
            raise OrderBookError("order book is not sequence-valid")
        if not 1 <= depth <= 1000:
            raise ValueError("depth must be between 1 and 1000")
        bids = sorted(self.bids.items(), reverse=True)[:depth]
        asks = sorted(self.asks.items())[:depth]
        if not bids or not asks:
            raise OrderBookError("one side of the order book is empty")
        bid_price = bids[0][0]
        ask_price = asks[0][0]
        mid = (bid_price + ask_price) / 2
        total_bid = sum(qty for _, qty in bids)
        total_ask = sum(qty for _, qty in asks)
        return {
            "market": self.market, "symbol": self.symbol,
            "last_update_id": self.sequence.last_update_id,
            "bid": bid_price, "ask": ask_price, "mid": mid,
            "spread": ask_price - bid_price,
            "spread_bps": (ask_price - bid_price) / mid * 10000,
            "bid_depth": total_bid, "ask_depth": total_ask,
            "depth_imbalance": (total_bid - total_ask) / (total_bid + total_ask)
            if total_bid + total_ask else 0.0,
            "bids": bids, "asks": asks, "valid": True,
        }


def synchronize_buffered(book: LocalOrderBook, snapshot: DepthSnapshot,
                          buffered_events: list[dict[str, Any]]) -> int:
    """Install snapshot and replay buffered diffs; return applied event count.

    Events older than the snapshot are discarded. A missing bridge or sequence gap
    invalidates the book and raises; callers must fetch a fresh snapshot and buffer again.
    """
    book.install_snapshot(snapshot)
    applied = 0
    for event in buffered_events:
        if int(event.get("u", -1)) <= snapshot.last_update_id and not book.valid:
            continue
        try:
            if book.apply_diff(event):
                applied += 1
        except (SequenceGap, OrderBookError):
            book.sequence.valid = False
            raise
    if not book.valid:
        raise SequenceGap("buffer_did_not_bridge_snapshot")
    return applied



class AsyncOrderBookCapture:
    """Continuously buffer depth diffs and recover the L2 book from REST snapshots.

    Raw events must be persisted by the caller before ingesting them here. Snapshot
    fetches run off the event loop so the WebSocket reader can keep buffering diffs.
    The book remains unreadable until a buffered diff bridges the snapshot sequence.
    """

    def __init__(
        self,
        spec: MarketSpec,
        store: Any,
        *,
        snapshot_fetch: Callable[[], DepthSnapshot] | None = None,
        max_buffered_events: int = 10000,
        session_id: str | None = None,
        retry_min: float = 1.0,
        retry_max: float = 30.0,
    ) -> None:
        if max_buffered_events < 1 or retry_min <= 0 or retry_max < retry_min:
            raise ValueError("invalid order-book recovery limits")
        self.spec = spec
        self.store = store
        self.session_id = session_id
        self.book = LocalOrderBook(spec.market, spec.symbol.upper())
        self.snapshot_fetch = snapshot_fetch or (lambda: fetch_depth_snapshot(spec))
        self.max_buffered_events = max_buffered_events
        self.retry_min = retry_min
        self.retry_max = retry_max
        self._retry_delay = retry_min
        self._retry_at = 0.0
        self._snapshot_task: asyncio.Task[DepthSnapshot] | None = None
        self._buffer: list[dict[str, Any]] = []
        self._closed = False
        self.sequence_gap_count = 0
        self.buffer_overflow_count = 0
        self.dropped_buffered_events = 0
        self.dropped_on_close_events = 0
        self.snapshot_attempt_count = 0
        self.snapshot_failure_count = 0
        self.max_buffer_observed = 0

    def _state(self, state: str, **details: Any) -> None:
        self.store.append(
            self.spec.market,
            "orderbook_state",
            {"state": state, "symbol": self.spec.symbol.upper(),
             "last_update_id": self.book.sequence.last_update_id, **details},
            session_id=self.session_id,
        )

    def _start_snapshot(self) -> None:
        if self._closed or self._snapshot_task is not None:
            return
        if time.monotonic() < self._retry_at:
            return
        self.snapshot_attempt_count += 1
        self._snapshot_task = asyncio.create_task(
            asyncio.to_thread(self.snapshot_fetch), name=f"depth-snapshot-{self.spec.market}"
        )

    def _persist_snapshot(self, snapshot: DepthSnapshot) -> None:
        self.store.append(
            self.spec.market,
            "orderbook_snapshots",
            {"symbol": snapshot.symbol, "lastUpdateId": snapshot.last_update_id,
             "bids": snapshot.bids, "asks": snapshot.asks},
            session_id=self.session_id,
        )

    async def _consume_snapshot(self) -> bool:
        task = self._snapshot_task
        if task is None or not task.done():
            return False
        self._snapshot_task = None
        try:
            snapshot = task.result()
            self._persist_snapshot(snapshot)
            applied = synchronize_buffered(self.book, snapshot, self._buffer)
        except (OrderBookError, SequenceGap, OSError, ValueError, RuntimeError) as exc:
            self.snapshot_failure_count += 1
            self.book.sequence.valid = False
            self._state("invalid", reason=type(exc).__name__, message=str(exc))
            self._retry_at = time.monotonic() + self._retry_delay
            self._retry_delay = min(self.retry_max, self._retry_delay * 2)
            return False
        except Exception as exc:  # noqa: BLE001  # snapshot adapters may expose transport-specific exceptions
            self.snapshot_failure_count += 1
            self.book.sequence.valid = False
            self._state("snapshot_error", reason=type(exc).__name__)
            self._retry_at = time.monotonic() + self._retry_delay
            self._retry_delay = min(self.retry_max, self._retry_delay * 2)
            return False

        self._buffer.clear()
        self._retry_delay = self.retry_min
        self._retry_at = 0.0
        self._state("synchronized", applied_diffs=applied)
        return True

    async def ingest(self, event: dict[str, Any]) -> str:
        """Ingest one already-persisted raw depth event; return current book status."""
        if self._closed:
            raise RuntimeError("order-book capture is closed")

        if self.book.valid:
            try:
                applied = self.book.apply_diff(event)
            except (SequenceGap, OrderBookError, KeyError, TypeError, ValueError) as exc:
                self._buffer = [event]
                self.sequence_gap_count += 1
                self.max_buffer_observed = max(self.max_buffer_observed, len(self._buffer))
                self._state(
                    "sequence_gap",
                    reason=type(exc).__name__,
                    message=str(exc),
                    sequence_gap_count=self.sequence_gap_count,
                )
                self._start_snapshot()
                return "invalid"
            return "applied" if applied else "stale"

        self._buffer.append(event)
        self.max_buffer_observed = max(self.max_buffer_observed, len(self._buffer))
        if len(self._buffer) > self.max_buffered_events:
            dropped = len(self._buffer)
            self.buffer_overflow_count += 1
            self.dropped_buffered_events += dropped
            self._buffer.clear()
            self._state(
                "buffer_overflow",
                max_buffered_events=self.max_buffered_events,
                dropped_events=dropped,
                cumulative_dropped_events=self.dropped_buffered_events,
                buffer_overflow_count=self.buffer_overflow_count,
            )
            self._retry_at = max(self._retry_at, time.monotonic() + self._retry_delay)
            self._retry_delay = min(self.retry_max, self._retry_delay * 2)

        if self._snapshot_task is not None and self._snapshot_task.done():
            await self._consume_snapshot()
        self._start_snapshot()
        return "synchronized" if self.book.valid else "buffering"

    def metrics(self) -> dict[str, int | bool]:
        """Return cumulative order-book recovery and loss counters."""
        return {
            "book_valid": self.book.valid,
            "sequence_gap_count": self.sequence_gap_count,
            "buffer_overflow_count": self.buffer_overflow_count,
            "dropped_buffered_events": self.dropped_buffered_events,
            "dropped_on_close_events": self.dropped_on_close_events,
            "snapshot_attempt_count": self.snapshot_attempt_count,
            "snapshot_failure_count": self.snapshot_failure_count,
            "max_buffer_observed": self.max_buffer_observed,
            "buffered_events_current": len(self._buffer),
            "max_buffered_events": self.max_buffered_events,
        }

    async def close(self) -> None:
        """Cancel any outstanding snapshot task on disconnect or shutdown."""
        self._closed = True
        if self._snapshot_task is not None:
            self._snapshot_task.cancel()
            await asyncio.gather(self._snapshot_task, return_exceptions=True)
            self._snapshot_task = None
        if self._buffer:
            dropped = len(self._buffer)
            self.dropped_on_close_events += dropped
            self.dropped_buffered_events += dropped
            self._state(
                "buffer_discarded_on_close",
                dropped_events=dropped,
                cumulative_dropped_events=self.dropped_buffered_events,
                dropped_on_close_events=self.dropped_on_close_events,
            )
            self._buffer.clear()


__all__ = ["AsyncOrderBookCapture", "DepthSnapshot", "LocalOrderBook", "OrderBookError",
           "fetch_depth_snapshot", "synchronize_buffered"]
