"""Causal, receive-time-aligned Binance microstructure feature snapshots.

Features are research artifacts only. Exchange event time is retained as source
metadata, while local received_at determines when an event became available.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from collections import deque
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from binance_pit_vault import (
    PITVaultError,
    build_session_manifest,
    iter_session_records,
    verify_session_manifest,
)


class PITFeatureError(RuntimeError):
    """Raised when a causal feature snapshot cannot be produced safely."""


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _timestamp(value: Any, field: str) -> datetime:
    try:
        result = datetime.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise PITFeatureError(f"invalid_{field}") from exc
    if result.tzinfo is None:
        raise PITFeatureError(f"timezone_required_{field}")
    return result.astimezone(UTC)


def _decimal(value: Any, field: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise PITFeatureError(f"invalid_{field}") from exc
    if not result.is_finite():
        raise PITFeatureError(f"non_finite_{field}")
    return result


def _load_verified_session(
    data_root: str | Path, market: str, session_id: str, *, allow_incomplete: bool
) -> dict[str, Any]:
    """Build and verify the session manifest without materializing raw events."""
    manifest = build_session_manifest(data_root, market, session_id)
    if not manifest["session_closed"]:
        raise PITFeatureError("active_session_not_eligible_for_features")
    if not manifest["session_complete"] and not allow_incomplete:
        raise PITFeatureError("incomplete_capture_not_eligible_for_research_features")
    try:
        verify_session_manifest(data_root, manifest)
    except PITVaultError as exc:
        raise PITFeatureError("session_integrity_verification_failed") from exc
    return manifest


class _ReferenceBook:
    """Small Decimal L2 state machine, independent of the live book class."""

    def __init__(self, symbol: str, max_buffered_events: int = 10000) -> None:
        if max_buffered_events < 1:
            raise ValueError("max_buffered_events must be >= 1")
        self.symbol = symbol.upper()
        self.max_buffered_events = max_buffered_events
        self.buffer_overflows = 0
        self.bids: dict[Decimal, Decimal] = {}
        self.asks: dict[Decimal, Decimal] = {}
        self.last_update_id: int | None = None
        self.valid = False
        self.buffer: list[dict[str, Any]] = []
        self.sequence_gaps = 0

    @staticmethod
    def _levels(target: dict[Decimal, Decimal], updates: Any) -> None:
        if not isinstance(updates, list):
            raise TypeError("orderbook_levels_must_be_a_list")
        for level in updates:
            if not isinstance(level, (list, tuple)) or len(level) != 2:
                raise ValueError("invalid_orderbook_level")
            price = _decimal(level[0], "book_price")
            quantity = _decimal(level[1], "book_quantity")
            if price <= 0 or quantity < 0:
                raise ValueError("invalid_orderbook_level_value")
            if quantity == 0:
                target.pop(price, None)
            else:
                target[price] = quantity

    def _apply_diff(self, event: dict[str, Any]) -> bool:
        if self.last_update_id is None:
            raise ValueError("snapshot_required")
        event_symbol = event.get("s")
        if event_symbol is not None and str(event_symbol).upper() != self.symbol:
            raise ValueError("depth_symbol_mismatch")
        first = int(event["U"])
        final = int(event["u"])
        if first < 0 or final < first:
            raise ValueError("invalid_depth_sequence")
        if final <= self.last_update_id:
            return False
        if first > self.last_update_id + 1:
            self.valid = False
            raise ValueError("depth_sequence_gap")
        if not first <= self.last_update_id + 1 <= final:
            self.valid = False
            raise ValueError("depth_sequence_invalid")
        self._levels(self.bids, event.get("b", []))
        self._levels(self.asks, event.get("a", []))
        self.last_update_id = final
        self.valid = True
        return True

    def ingest_depth(self, event: dict[str, Any]) -> None:
        if not self.valid:
            if len(self.buffer) >= self.max_buffered_events:
                self.buffer.clear()
                self.buffer_overflows += 1
            self.buffer.append(event)
            return
        try:
            self._apply_diff(event)
        except (KeyError, TypeError, ValueError, PITFeatureError):
            self.valid = False
            self.buffer = [event]
            self.sequence_gaps += 1

    def ingest_snapshot(self, payload: dict[str, Any]) -> None:
        try:
            if str(payload["symbol"]).upper() != self.symbol:
                raise ValueError("snapshot_symbol_mismatch")
            snapshot_id = int(payload["lastUpdateId"])
            if snapshot_id < 0:
                raise ValueError("invalid_snapshot_sequence")
            bids: dict[Decimal, Decimal] = {}
            asks: dict[Decimal, Decimal] = {}
            self._levels(bids, payload["bids"])
            self._levels(asks, payload["asks"])
            if not bids or not asks:
                raise ValueError("snapshot_missing_book_side")
            self.bids, self.asks = bids, asks
            self.last_update_id = snapshot_id
            self.valid = False
            bridged = False
            for event in self.buffer:
                if int(event["u"]) <= snapshot_id and not self.valid:
                    continue
                if self._apply_diff(event):
                    bridged = True
            if not bridged:
                raise ValueError("snapshot_not_bridged")
            self.buffer.clear()
        except (KeyError, TypeError, ValueError, PITFeatureError):
            self.valid = False
            self.sequence_gaps += 1

    def metrics(self, depth: int = 10) -> dict[str, Any]:
        if not self.valid or not self.bids or not self.asks:
            return {
                "valid": False,
                "last_update_id": self.last_update_id,
                "spread": None,
                "spread_bps": None,
                "bid_depth": None,
                "ask_depth": None,
                "depth_imbalance": None,
            }
        top_bids = sorted(self.bids.items(), reverse=True)[:depth]
        top_asks = sorted(self.asks.items())[:depth]
        bid, ask = top_bids[0][0], top_asks[0][0]
        mid = (bid + ask) / Decimal(2)
        bid_depth = sum((qty for _, qty in top_bids), Decimal(0))
        ask_depth = sum((qty for _, qty in top_asks), Decimal(0))
        total = bid_depth + ask_depth
        return {
            "valid": True,
            "last_update_id": self.last_update_id,
            "best_bid": str(bid),
            "best_ask": str(ask),
            "spread": str(ask - bid),
            "spread_bps": str((ask - bid) / mid * Decimal(10000)),
            "bid_depth": str(bid_depth),
            "ask_depth": str(ask_depth),
            "depth_imbalance": str(
                (bid_depth - ask_depth) / total if total else Decimal(0)
            ),
        }


def _trade_event(
    record: dict[str, Any], expected_symbol: str
) -> tuple[datetime, Decimal, Decimal, bool] | None:
    payload = record.get("payload", {})
    if payload.get("s") and str(payload["s"]).upper() != expected_symbol.upper():
        return None
    maker_is_buyer = payload.get("m")
    if not isinstance(maker_is_buyer, bool):
        return None
    try:
        price = _decimal(payload["p"], "trade_price")
        quantity = _decimal(payload["q"], "trade_quantity")
        received = _timestamp(record.get("received_at"), "received_at")
    except (KeyError, PITFeatureError):
        return None
    if price <= 0 or quantity <= 0:
        return None
    # Binance m=True means the buyer was maker, so the taker aggressor sold.
    taker_buy = not maker_is_buyer
    return received, price, quantity, taker_buy


def _liquidation_event(
    record: dict[str, Any], expected_symbol: str
) -> tuple[datetime, Decimal, Decimal, str] | None:
    order = record.get("payload", {}).get("o", {})
    if order.get("s") and str(order["s"]).upper() != expected_symbol.upper():
        return None
    side = order.get("S")
    if side not in {"BUY", "SELL"}:
        return None
    try:
        quantity = _decimal(order["z"], "liquidation_quantity")
        price = _decimal(order["ap"], "liquidation_average_price")
        received = _timestamp(record.get("received_at"), "received_at")
    except (KeyError, PITFeatureError):
        return None
    if quantity <= 0 or price <= 0:
        return None
    return received, price, quantity, side


def _new_window_state() -> dict[str, Any]:
    return {
        "trades": deque(),
        "trade_count": 0,
        "buy_count": 0,
        "sell_count": 0,
        "buy_volume": Decimal(0),
        "sell_volume": Decimal(0),
        "buy_notional": Decimal(0),
        "sell_notional": Decimal(0),
        "liquidations": deque(),
        "liquidation_count": 0,
        "long_liquidation_count": 0,
        "short_liquidation_count": 0,
        "long_liquidation_notional": Decimal(0),
        "short_liquidation_notional": Decimal(0),
    }


def _add_trade(state: dict[str, Any], event: tuple[datetime, Decimal, Decimal, bool]) -> None:
    received, price, quantity, taker_buy = event
    notional = price * quantity
    state["trades"].append((received, quantity, notional, taker_buy))
    state["trade_count"] += 1
    if taker_buy:
        state["buy_count"] += 1
        state["buy_volume"] += quantity
        state["buy_notional"] += notional
    else:
        state["sell_count"] += 1
        state["sell_volume"] += quantity
        state["sell_notional"] += notional


def _expire_window(state: dict[str, Any], cutoff: datetime) -> None:
    while state["trades"] and state["trades"][0][0] <= cutoff:
        _, quantity, notional, taker_buy = state["trades"].popleft()
        state["trade_count"] -= 1
        if taker_buy:
            state["buy_count"] -= 1
            state["buy_volume"] -= quantity
            state["buy_notional"] -= notional
        else:
            state["sell_count"] -= 1
            state["sell_volume"] -= quantity
            state["sell_notional"] -= notional
    while state["liquidations"] and state["liquidations"][0][0] <= cutoff:
        _, notional, side = state["liquidations"].popleft()
        state["liquidation_count"] -= 1
        if side == "SELL":
            state["long_liquidation_count"] -= 1
            state["long_liquidation_notional"] -= notional
        else:
            state["short_liquidation_count"] -= 1
            state["short_liquidation_notional"] -= notional


def _window_output(state: dict[str, Any], seconds: int) -> dict[str, Any]:
    total_volume = state["buy_volume"] + state["sell_volume"]
    total_notional = state["buy_notional"] + state["sell_notional"]
    return {
        "window_seconds": seconds,
        "trade_count": state["trade_count"],
        "buy_trade_count": state["buy_count"],
        "sell_trade_count": state["sell_count"],
        "taker_buy_volume": str(state["buy_volume"]),
        "taker_sell_volume": str(state["sell_volume"]),
        "net_taker_volume": str(state["buy_volume"] - state["sell_volume"]),
        "taker_buy_notional": str(state["buy_notional"]),
        "taker_sell_notional": str(state["sell_notional"]),
        "net_taker_notional": str(state["buy_notional"] - state["sell_notional"]),
        "taker_buy_ratio": str(
            state["buy_volume"] / total_volume if total_volume else Decimal(0)
        ),
        "trade_vwap": str(
            total_notional / total_volume if total_volume else Decimal(0)
        ),
        "trade_intensity_per_second": str(
            Decimal(state["trade_count"]) / Decimal(seconds)
        ),
        "liquidation_event_count": state["liquidation_count"],
        "long_liquidation_count": state["long_liquidation_count"],
        "short_liquidation_count": state["short_liquidation_count"],
        "long_liquidation_notional": str(state["long_liquidation_notional"]),
        "short_liquidation_notional": str(state["short_liquidation_notional"]),
    }


def build_pit_microstructure_features(
    data_root: str | Path,
    market: str,
    session_id: str,
    decision_times: list[str],
    *,
    window_seconds: tuple[int, ...] = (5, 60, 300),
    book_depth: int = 10,
    allow_incomplete: bool = False,
    max_buffered_depth_events: int = 10000,
) -> dict[str, Any]:
    """Build causal feature snapshots using only events received by each decision time."""
    if not decision_times:
        raise ValueError("at least one decision time is required")
    if not window_seconds or any(
        isinstance(value, bool) or not isinstance(value, int) or value < 1
        for value in window_seconds
    ):
        raise ValueError("window_seconds must contain positive integers")
    if book_depth < 1:
        raise ValueError("book_depth must be >= 1")
    if max_buffered_depth_events < 1:
        raise ValueError("max_buffered_depth_events must be >= 1")

    manifest = _load_verified_session(
        data_root, market, session_id, allow_incomplete=allow_incomplete
    )
    decisions = [_timestamp(value, "decision_time") for value in decision_times]
    if decisions != sorted(decisions):
        raise ValueError("decision_times must be in ascending order")
    started = _timestamp(manifest["started_at"], "session_started_at")
    ended = _timestamp(manifest["ended_at"], "session_ended_at")
    if any(value < started or value > ended for value in decisions):
        raise PITFeatureError("decision_time_outside_capture_session")

    book = _ReferenceBook(str(manifest["symbol"]), max_buffered_depth_events)
    window_state = {seconds: _new_window_state() for seconds in sorted(set(window_seconds))}
    snapshots: list[dict[str, Any]] = []
    processed_counts = {
        "orderbook_depth": 0, "orderbook_snapshots": 0,
        "trades": 0, "liquidations": 0,
    }
    max_received: datetime | None = None
    record_iterator = iter_session_records(data_root, manifest, session_id)
    pending_record = next(record_iterator, None)

    for decision in decisions:
        for seconds, state in window_state.items():
            _expire_window(state, decision - timedelta(seconds=seconds))

        while pending_record is not None:
            received = _timestamp(pending_record.get("received_at"), "received_at")
            if received > decision:
                break
            stream = pending_record.get("stream")
            if stream == "orderbook_depth":
                book.ingest_depth(pending_record.get("payload", {}))
                processed_counts["orderbook_depth"] += 1
                max_received = max(max_received, received) if max_received else received
            elif stream == "orderbook_snapshots":
                book.ingest_snapshot(pending_record.get("payload", {}))
                processed_counts["orderbook_snapshots"] += 1
                max_received = max(max_received, received) if max_received else received
            elif stream == "trades":
                event = _trade_event(pending_record, str(manifest["symbol"]))
                if event is not None:
                    for seconds, state in window_state.items():
                        if event[0] > decision - timedelta(seconds=seconds):
                            _add_trade(state, event)
                    max_received = max(max_received, received) if max_received else received
                    processed_counts["trades"] += 1
            elif stream == "liquidations":
                event = _liquidation_event(pending_record, str(manifest["symbol"]))
                if event is not None:
                    event_received, price, quantity, side = event
                    notional = price * quantity
                    for seconds, state in window_state.items():
                        if event_received <= decision - timedelta(seconds=seconds):
                            continue
                        state["liquidations"].append((event_received, notional, side))
                        state["liquidation_count"] += 1
                        if side == "SELL":
                            state["long_liquidation_count"] += 1
                            state["long_liquidation_notional"] += notional
                        else:
                            state["short_liquidation_count"] += 1
                            state["short_liquidation_notional"] += notional
                    max_received = max(max_received, event_received) if max_received else event_received
                    processed_counts["liquidations"] += 1
            pending_record = next(record_iterator, None)

        windows: dict[str, Any] = {}
        for seconds, state in window_state.items():
            windows[str(seconds)] = _window_output(state, seconds)

        feature = {
            "schema": "chartanalyzer_pit_microstructure_features_v1",
            "market": market,
            "symbol": manifest["symbol"],
            "session_id": session_id,
            "manifest_hash": manifest["manifest_hash"],
            "decision_time": decision.isoformat(),
            "available_at": decision.isoformat(),
            "availability_field": "received_at",
            "point_in_time_valid": max_received is None or max_received <= decision,
            "source_event_watermark": max_received.isoformat() if max_received else None,
            "book": book.metrics(book_depth),
            "book_sequence_gaps": book.sequence_gaps,
            "book_buffer_overflows": book.buffer_overflows,
            "windows": windows,
            "processed_event_counts": dict(processed_counts),
            "session_complete": manifest["session_complete"],
        }
        feature["feature_hash"] = hashlib.sha256(_canonical(feature)).hexdigest()
        snapshots.append(feature)

    try:
        verify_session_manifest(data_root, manifest)
    except PITVaultError as exc:
        raise PITFeatureError("session_integrity_changed_during_feature_build") from exc

    result = {
        "schema": "chartanalyzer_pit_microstructure_dataset_v1",
        "market": market,
        "symbol": manifest["symbol"],
        "session_id": session_id,
        "manifest_hash": manifest["manifest_hash"],
        "session_complete": manifest["session_complete"],
        "window_seconds": sorted(set(window_seconds)),
        "snapshots": snapshots,
    }
    result["dataset_hash"] = hashlib.sha256(_canonical(result)).hexdigest()
    return result


def _main() -> None:
    parser = argparse.ArgumentParser(
        description="Build receive-time-causal Binance microstructure features."
    )
    parser.add_argument("--data-dir", default="./data/binance_raw")
    parser.add_argument("--market", choices=("spot", "usdm"), required=True)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--decision-time", action="append", required=True)
    parser.add_argument("--windows", default="5,60,300")
    parser.add_argument("--book-depth", type=int, default=10)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    windows = tuple(int(value.strip()) for value in args.windows.split(",") if value.strip())
    result = build_pit_microstructure_features(
        args.data_dir, args.market, args.session_id, args.decision_time,
        window_seconds=windows, book_depth=args.book_depth,
    )
    destination = Path(args.output).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = _canonical(result) + b"\n"
    fd, temporary_name = tempfile.mkstemp(prefix=".pit-features-", dir=destination.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, destination)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)
    print(json.dumps({
        "output": str(destination),
        "dataset_hash": result["dataset_hash"],
        "snapshots": len(result["snapshots"]),
    }, indent=2))


if __name__ == "__main__":
    _main()
