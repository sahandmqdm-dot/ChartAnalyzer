"""Point-in-time historical market-order replay against captured Binance L2.

Only information locally received by a decision timestamp is available. Results
are displayed-depth estimates, not realized fills or queue-aware simulations.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Iterable

from binance_orderbook import (
    DepthSnapshot,
    LocalOrderBook,
    OrderBookError,
    synchronize_buffered,
)
from binance_pit_vault import (
    PITVaultError,
    build_session_manifest,
    iter_session_records,
    replay_orderbook_session,
    verify_session_manifest,
)
from binance_stream_collector import SequenceGap
from execution_cost_engine import evaluate_execution, market_sweep_fills


ENGINE_VERSION = "HistoricalExecutionReplay 1.0"


def _timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 string") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed


def _canonical_hash(value: dict[str, Any]) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"),
                     ensure_ascii=False, allow_nan=False, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def replay_historical_market_orders(
    data_root: str,
    market: str,
    session_id: str,
    orders: Iterable[dict[str, Any]],
    *,
    fee_schedule: dict[str, Any],
    max_book_age_ms: float = 500.0,
    book_depth: int = 1000,
) -> dict[str, Any]:
    """Replay hypothetical market orders at receive-time decisions over a sealed PIT session.

    Each order requires order_id, decision_at, side, quantity and impact_bps.
    Funding assumptions are explicit per order. Orders with no valid/fresh book
    are returned as blocked, never as zero-cost executions.
    """
    manifest = build_session_manifest(data_root, market, session_id)
    if not manifest.get("session_closed") or not manifest.get("session_complete"):
        raise PITVaultError("historical execution requires a closed, complete capture session")
    verify_session_manifest(data_root, manifest)
    if manifest.get("availability_contract", {}).get("available_at_field") != "received_at":
        raise PITVaultError("unsupported point-in-time availability contract")
    if manifest.get("event_time_quality", {}).get("event_time_after_receive_time", 0):
        raise PITVaultError("event timestamps after local receipt violate point-in-time ordering")

    integrity = replay_orderbook_session(data_root, market, session_id)
    if (not integrity.get("integrity_valid") or not integrity.get("reconstruction_agreement")
            or integrity.get("sequence_gaps") != 0 or not integrity.get("final_valid")
            or integrity.get("data_quality_status") != "complete_capture"):
        raise PITVaultError("order-book replay integrity or sequence validation failed")

    start = _timestamp(manifest["started_at"], "session.started_at")
    end = _timestamp(manifest["ended_at"], "session.ended_at")
    prepared = []
    seen_ids = set()
    for index, order in enumerate(orders):
        if not isinstance(order, dict):
            raise TypeError(f"orders[{index}] must be an object")
        order_id = str(order.get("order_id", "")).strip()
        if not order_id or order_id in seen_ids:
            raise ValueError("each order must have a unique non-empty order_id")
        seen_ids.add(order_id)
        decision = _timestamp(order.get("decision_at"), f"orders[{index}].decision_at")
        if decision < start or decision > end:
            raise ValueError(f"order {order_id} decision is outside the capture session")
        side = str(order.get("side", "")).lower()
        if side not in {"buy", "sell"}:
            raise ValueError(f"order {order_id} side must be buy or sell")
        quantity = float(order.get("quantity"))
        if not quantity > 0 or not __import__("math").isfinite(quantity):
            raise ValueError(f"order {order_id} quantity must be finite and > 0")
        if "impact_bps" not in order:
            raise ValueError(f"order {order_id} requires an explicit impact_bps assumption")
        prepared.append((decision, order_id, order, quantity, side))
    prepared.sort(key=lambda item: (item[0], item[1]))

    book = LocalOrderBook(market, str(manifest["symbol"]).upper())
    buffered: list[dict[str, Any]] = []
    latest_book_received_at: str | None = None
    record_iter = iter(iter_session_records(data_root, manifest, session_id))
    current = next(record_iter, None)
    results = []

    for decision, order_id, order, quantity, side in prepared:
        while current is not None:
            received_at = _timestamp(current.get("received_at"), "record.received_at")
            if received_at > decision:
                break
            stream = current.get("stream")
            payload = current.get("payload", {})
            if stream == "orderbook_depth":
                if not book.valid:
                    buffered.append(payload)
                else:
                    try:
                        if book.apply_diff(payload):
                            latest_book_received_at = current["received_at"]
                    except (SequenceGap, OrderBookError, KeyError, TypeError, ValueError):
                        book.sequence.valid = False
                        buffered = [payload]
                        latest_book_received_at = None
            elif stream == "orderbook_snapshots":
                try:
                    snapshot = DepthSnapshot(
                        last_update_id=int(payload["lastUpdateId"]),
                        bids=tuple((float(p), float(q)) for p, q in payload["bids"]),
                        asks=tuple((float(p), float(q)) for p, q in payload["asks"]),
                        market=market,
                        symbol=str(payload["symbol"]).upper(),
                    )
                    synchronize_buffered(book, snapshot, buffered)
                    buffered.clear()
                    # The book only becomes knowable after this REST snapshot arrives.
                    latest_book_received_at = current["received_at"]
                except (KeyError, TypeError, ValueError, OrderBookError, SequenceGap):
                    book.sequence.valid = False
                    buffered.clear()
                    latest_book_received_at = None
            current = next(record_iter, None)

        common = {
            "order_id": order_id,
            "decision_at": decision.isoformat(),
            "side": side,
            "requested_quantity": quantity,
            "session_id": session_id,
            "manifest_hash": manifest["manifest_hash"],
            "orderbook_replay_hash": integrity["replay_hash"],
        }
        if not book.valid or latest_book_received_at is None:
            results.append({**common, "status": "blocked",
                            "blocked_reason": "no_sequence_valid_book_at_decision"})
            continue

        try:
            top = book.top(book_depth)
            sweep = market_sweep_fills(
                top,
                side=side,
                requested_quantity=quantity,
                book_received_at=latest_book_received_at,
                decision_at=decision.isoformat(),
                max_age_ms=max_book_age_ms,
            )
        except (OrderBookError, ValueError, TypeError) as exc:
            reason = "stale_order_book" if "stale" in str(exc).lower() else "invalid_order_book"
            results.append({**common, "status": "blocked", "blocked_reason": reason,
                            "detail": str(exc)})
            continue

        execution = evaluate_execution(
            side=side,
            order_type="market",
            requested_quantity=quantity,
            fills=sweep["fills"],
            fee_schedule=fee_schedule,
            reference_mid=top["mid"],
            best_bid=top["bid"],
            best_ask=top["ask"],
            impact_bps=order["impact_bps"],
            funding_rate=order.get("funding_rate"),
            funding_events=order.get("funding_events", 0),
            latency_ms=order.get("latency_ms"),
            fill_source="sequence_validated_historical_l2_snapshot_sweep",
            assumptions={
                "decision_availability": "local_received_at",
                "book_age_ms": sweep["book_provenance"]["age_ms"],
                "market_sweep_limitations": sweep["limitations"],
                "impact_bps_source": order.get("impact_bps_source", "caller_supplied"),
                "funding_source": order.get("funding_source"),
            },
        )
        results.append({
            **common,
            "status": execution["status"],
            "market_sweep": sweep,
            "execution": execution,
            "cost_model_complete": execution["cost_breakdown"]["complete"],
            "execution_estimate_kind": "displayed_depth_snapshot_estimate",
            "limitations": [
                "Not a realized historical fill or a live execution result.",
                "No queue position, hidden liquidity, cancellations or intervening matching events.",
                "Latency is recorded but is not independently converted into a cost.",
                "Funding is included only when explicitly supplied for the order.",
            ],
        })

    body = {
        "schema": "chartanalyzer_historical_execution_replay_v1",
        "engine": ENGINE_VERSION,
        "market": market,
        "symbol": manifest["symbol"],
        "session_id": session_id,
        "manifest_hash": manifest["manifest_hash"],
        "orderbook_replay_hash": integrity["replay_hash"],
        "availability_field": "received_at",
        "orders": results,
        "summary": {
            "orders": len(results),
            "estimated_filled": sum(row.get("execution", {}).get("filled_quantity", 0.0)
                                    > 0 for row in results),
            "blocked": sum(row["status"] == "blocked" for row in results),
            "partial": sum(row["status"] == "partial" for row in results),
            "unfilled": sum(row["status"] == "unfilled" for row in results),
            "cost_models_incomplete": sum(row.get("cost_model_complete") is False
                                          for row in results),
        },
    }
    return {**body, "replay_hash": _canonical_hash(body)}


__all__ = ["ENGINE_VERSION", "replay_historical_market_orders"]
