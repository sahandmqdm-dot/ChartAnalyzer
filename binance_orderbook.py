"""Snapshot-backed local L2 order-book state for Binance Spot and USD-M."""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from urllib.request import Request, urlopen
from typing import Any, Callable

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
        bid_price, bid_qty = bids[0]
        ask_price, ask_qty = asks[0]
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


__all__ = ["DepthSnapshot", "LocalOrderBook", "OrderBookError",
           "fetch_depth_snapshot", "synchronize_buffered"]
