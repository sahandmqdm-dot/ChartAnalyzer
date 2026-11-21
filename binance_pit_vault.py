"""Point-in-time evidence manifests and deterministic Binance L2 replay.

Hash chains detect accidental or unsophisticated edits, while file digests bind a
session manifest to exact raw partitions. This is tamper-evident, not immutable
storage or a cryptographic signature.
"""
from __future__ import annotations

import argparse
import hashlib
import heapq
import json
import math
import os
import tempfile
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from binance_orderbook import (
    DepthSnapshot,
    LocalOrderBook,
    OrderBookError,
    synchronize_buffered,
)
from binance_stream_collector import HashChainJSONLStore, SequenceGap
from execution_cost_engine import (
    evaluate_execution,
    market_sweep_fills,
    validate_fee_schedule,
)


class PITVaultError(RuntimeError):
    """Invalid, incomplete, or tampered point-in-time evidence."""


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _record_sort_key(record: dict[str, Any]) -> tuple[datetime, int, str]:
    try:
        received = datetime.fromisoformat(str(record["received_at"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise PITVaultError("invalid_or_missing_received_at") from exc
    if received.tzinfo is None:
        raise PITVaultError("timezone_required_received_at")
    stream = str(record.get("stream", "unknown"))
    priority = 0 if stream == "orderbook_depth" else 1
    return received.astimezone(UTC), priority, stream


def _session_source_files(
    root: Path, market: str, session_id: str
) -> list[dict[str, Any]]:
    """Scan partitions with bounded memory and bind only files containing this session."""
    market_root = (root / market).resolve()
    if root not in market_root.parents or not market_root.is_dir():
        raise PITVaultError(f"market partition does not exist: {market}")
    files: list[dict[str, Any]] = []
    for candidate in sorted(market_root.rglob("*.jsonl")):
        path = candidate.resolve()
        if root not in path.parents:
            raise PITVaultError("partition_path_escapes_data_root")
        record_count = 0
        session_count = 0
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    record = json.loads(line)
                    if not isinstance(record, dict):
                        raise PITVaultError("partition_record_must_be_an_object")
                    record_count += 1
                    if record.get("session_id") == session_id:
                        session_count += 1
        except (OSError, ValueError, TypeError) as exc:
            raise PITVaultError(f"unreadable_or_malformed_partition: {path.name}") from exc
        if not session_count:
            continue
        valid, verified_count, head = HashChainJSONLStore.verify_file(path)
        if not valid or verified_count != record_count:
            raise PITVaultError(f"hash chain failed for {path.name}")
        relative = path.relative_to(root).as_posix()
        files.append({
            "path": relative,
            "sha256": _sha256_file(path),
            "bytes": path.stat().st_size,
            "records": verified_count,
            "session_records": session_count,
            "chain_head": head,
        })
    if not files:
        raise PITVaultError(f"no records found for session {session_id}")
    return files


def iter_session_records(
    data_root: str | Path,
    manifest: dict[str, Any],
    session_id: str,
) -> Iterator[dict[str, Any]]:
    """Merge session records chronologically without loading full partitions into RAM.

    The caller must first build and verify the manifest. Each source partition is
    checked for nondecreasing receive time before it participates in the merge.
    """
    root = Path(data_root).expanduser().resolve()
    iterators = []
    for source in manifest.get("source_files", []):
        path = (root / source["path"]).resolve()
        if root not in path.parents:
            raise PITVaultError("manifest source path escapes data root")

        def partition_records(source_path: Path = path) -> Iterator[dict[str, Any]]:
            previous_key = None
            try:
                with source_path.open("r", encoding="utf-8") as handle:
                    for line in handle:
                        if not line.strip():
                            continue
                        record = json.loads(line)
                        if not isinstance(record, dict):
                            raise PITVaultError("partition_record_must_be_an_object")
                        if record.get("session_id") != session_id:
                            continue
                        if record.get("stream") != source_path.parent.name:
                            raise PITVaultError("partition_stream_identity_mismatch")
                        expected_market = manifest.get("market")
                        if expected_market and record.get("market") != expected_market:
                            raise PITVaultError("partition_market_identity_mismatch")
                        key = _record_sort_key(record)
                        if previous_key is not None and key < previous_key:
                            raise PITVaultError(
                                f"non_monotonic_received_at_in_partition: {source_path.name}"
                            )
                        previous_key = key
                        yield record
            except (OSError, ValueError, TypeError) as exc:
                raise PITVaultError(
                    f"unreadable_or_malformed_partition: {source_path.name}"
                ) from exc

        iterators.append(partition_records())
    yield from heapq.merge(*iterators, key=_record_sort_key)


def build_session_manifest(
    data_root: str | Path, market: str, session_id: str
) -> dict[str, Any]:
    """Create a deterministic content-addressed manifest using bounded-memory scans."""
    if market not in {"spot", "usdm"}:
        raise ValueError("market must be spot or usdm")
    if not session_id or not isinstance(session_id, str):
        raise ValueError("session_id is required")

    root = Path(data_root).expanduser().resolve()
    files = _session_source_files(root, market, session_id)
    event_counts: dict[str, int] = {}
    started = None
    terminal = None
    streams: list[str] = []
    symbols: set[str] = set()
    total_records = 0
    market_events = 0
    missing_event_time = 0
    event_after_receive = 0
    first_received = None
    last_received = None

    for record in iter_session_records(
        root, {"market": market, "source_files": files}, session_id
    ):
        total_records += 1
        stream = str(record.get("stream", "unknown"))
        event_counts[stream] = event_counts.get(stream, 0) + 1
        received = _record_sort_key(record)[0]
        if first_received is None:
            first_received = received
        last_received = received

        if stream == "session_manifest":
            payload = record.get("payload", {})
            if payload.get("state") == "started" and started is None:
                started = payload
                streams = list(payload.get("streams", []))
                symbols = {
                    str(item).split("@", 1)[0].upper()
                    for item in streams if isinstance(item, str) and "@" in item
                }
            elif payload.get("state") in {"ended", "interrupted"}:
                terminal = payload

        if stream in {"trades", "orderbook_depth", "liquidations"}:
            market_events += 1
            event_raw = record.get("event_time")
            if not event_raw:
                missing_event_time += 1
            else:
                try:
                    event_dt = datetime.fromisoformat(str(event_raw))
                    if event_dt.tzinfo is None:
                        raise ValueError("naive event timestamp")
                    if event_dt.astimezone(UTC) > received:
                        event_after_receive += 1
                except (TypeError, ValueError):
                    missing_event_time += 1

    if started is None:
        raise PITVaultError("session start manifest is missing")

    session_state = terminal.get("state") if terminal else "active"
    body = {
        "schema": "chartanalyzer_pit_session_manifest_v1",
        "market": market,
        "session_id": session_id,
        "symbol": next(iter(symbols)) if len(symbols) == 1 else None,
        "started_at": started.get("started_at"),
        "ended_at": terminal.get("ended_at") if terminal else None,
        "session_state": session_state,
        "session_closed": terminal is not None,
        "session_complete": bool(terminal and terminal.get("state") == "ended"),
        "termination": terminal.get("termination") if terminal else None,
        "streams": streams,
        "event_counts": event_counts,
        "total_session_records": total_records,
        "availability_contract": {
            "available_at_field": "received_at",
            "rule": "An event is unavailable to the strategy before local receipt time.",
            "event_time_is_not_availability_time": True,
        },
        "event_time_quality": {
            "market_events": market_events,
            "missing_or_invalid_event_time": missing_event_time,
            "event_time_after_receive_time": event_after_receive,
        },
        "received_time_range": {
            "first": first_received.isoformat() if first_received else None,
            "last": last_received.isoformat() if last_received else None,
        },
        "source_files": files,
    }
    manifest_hash = hashlib.sha256(_canonical(body)).hexdigest()
    return {**body, "manifest_hash": manifest_hash}


def persist_session_manifest(
    data_root: str | Path, market: str, session_id: str
) -> Path:
    """Write the manifest atomically under its content hash."""
    root = Path(data_root).expanduser().resolve()
    manifest = build_session_manifest(root, market, session_id)
    if not manifest["session_closed"]:
        raise PITVaultError("cannot seal an active capture session")
    directory = root / "vault" / "manifests"
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f"{manifest['manifest_hash']}.json"
    payload = _canonical(manifest) + b"\n"
    if destination.exists():
        if destination.read_bytes() != payload:
            raise PITVaultError("content-address collision or existing manifest corruption")
        return destination

    fd, temporary_name = tempfile.mkstemp(prefix=".manifest-", dir=directory)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, destination)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)
    return destination


def verify_session_manifest(
    data_root: str | Path, manifest: dict[str, Any] | str | Path
) -> dict[str, Any]:
    """Verify manifest identity, source-file digests, and partition hash chains."""
    root = Path(data_root).expanduser().resolve()
    if isinstance(manifest, (str, Path)):
        document = json.loads(Path(manifest).read_text(encoding="utf-8"))
    else:
        document = dict(manifest)

    if not isinstance(document, dict):
        raise PITVaultError("manifest_must_be_an_object")
    expected_hash = document.pop("manifest_hash", None)
    actual_hash = hashlib.sha256(_canonical(document)).hexdigest()
    if expected_hash != actual_hash:
        raise PITVaultError("manifest_hash_mismatch")

    expected_market = document.get("market")
    for source in document.get("source_files", []):
        relative_path = Path(source["path"])
        if expected_market and (
            not relative_path.parts or relative_path.parts[0] != expected_market
        ):
            raise PITVaultError("manifest_market_path_mismatch")
        path = (root / relative_path).resolve()
        if root not in path.parents:
            raise PITVaultError("manifest source path escapes data root")
        if not path.is_file() or _sha256_file(path) != source["sha256"]:
            raise PITVaultError(f"source_file_digest_mismatch: {source['path']}")
        valid, count, head = HashChainJSONLStore.verify_file(path)
        if not valid or count != source["records"] or head != source["chain_head"]:
            raise PITVaultError(f"source_hash_chain_mismatch: {source['path']}")

    return {
        "valid": True,
        "manifest_hash": expected_hash,
        "source_files": len(document.get("source_files", [])),
        "session_id": document.get("session_id"),
    }


def replay_orderbook_session(
    data_root: str | Path, market: str, session_id: str
) -> dict[str, Any]:
    """Replay one session's snapshots and raw depth diffs in receive-time order."""
    manifest = build_session_manifest(data_root, market, session_id)
    if not manifest["session_closed"]:
        raise PITVaultError("cannot replay an active capture session")
    verify_session_manifest(data_root, manifest)
    root = Path(data_root).expanduser().resolve()
    symbol = manifest.get("symbol")
    if not symbol:
        raise PITVaultError("session must identify exactly one symbol")

    book = LocalOrderBook(market, symbol)
    buffer: list[dict[str, Any]] = []
    depth_events = snapshots = applied = stale = gaps = 0
    replay_errors: list[str] = []

    for record in iter_session_records(root, manifest, session_id):
        if record.get("stream") not in {"orderbook_depth", "orderbook_snapshots"}:
            continue
        payload = record.get("payload", {})
        if record.get("stream") == "orderbook_depth":
            depth_events += 1
            if not book.valid:
                buffer.append(payload)
                continue
            try:
                if book.apply_diff(payload):
                    applied += 1
                else:
                    stale += 1
            except (SequenceGap, OrderBookError, KeyError, TypeError, ValueError) as exc:
                book.sequence.valid = False
                buffer = [payload]
                gaps += 1
                replay_errors.append(type(exc).__name__)
            continue

        snapshots += 1
        try:
            snapshot = DepthSnapshot(
                last_update_id=int(payload["lastUpdateId"]),
                bids=tuple((float(p), float(q)) for p, q in payload["bids"]),
                asks=tuple((float(p), float(q)) for p, q in payload["asks"]),
                market=market,
                symbol=str(payload["symbol"]).upper(),
            )
            count = synchronize_buffered(book, snapshot, buffer)
            applied += count
            buffer.clear()
        except (KeyError, TypeError, ValueError, OrderBookError, SequenceGap) as exc:
            book.sequence.valid = False
            gaps += 1
            replay_errors.append(type(exc).__name__)

    reference = _reference_replay_records(
        (
            record for record in iter_session_records(root, manifest, session_id)
            if record.get("stream") in {"orderbook_depth", "orderbook_snapshots"}
        ),
        market,
        symbol,
    )
    comparison_fields = (
        "depth_events", "snapshots", "applied_diffs", "stale_diffs",
        "sequence_gaps", "final_valid", "final_update_id", "buffered_events",
    )
    reconstruction_agreement = all(
        {
            "depth_events": depth_events,
            "snapshots": snapshots,
            "applied_diffs": applied,
            "stale_diffs": stale,
            "sequence_gaps": gaps,
            "final_valid": book.valid,
            "final_update_id": book.sequence.last_update_id,
            "buffered_events": len(buffer),
        }[field] == reference[field]
        for field in comparison_fields
    )
    # Detect any source mutation that occurred while the two replay passes ran.
    verify_session_manifest(root, manifest)
    report_body = {
        "schema": "chartanalyzer_l2_replay_report_v1",
        "market": market,
        "symbol": symbol,
        "session_id": session_id,
        "manifest_hash": manifest["manifest_hash"],
        "integrity_valid": True,
        "session_state": manifest["session_state"],
        "session_complete": manifest["session_complete"],
        "termination": manifest["termination"],
        "data_quality_status": (
            "reconstruction_mismatch" if not reconstruction_agreement
            else "complete_capture" if manifest["session_complete"]
            else "incomplete_capture"
        ),
        "availability_contract": manifest["availability_contract"],
        "event_time_quality": manifest["event_time_quality"],
        "depth_events": depth_events,
        "snapshots": snapshots,
        "applied_diffs": applied,
        "stale_diffs": stale,
        "sequence_gaps": gaps,
        "final_valid": book.valid,
        "final_update_id": book.sequence.last_update_id,
        "buffered_events": len(buffer),
        "reconstruction_agreement": reconstruction_agreement,
        "independent_reference": {
            "engine": "decimal_reference_replay_v1",
            "replay_hash": reference["replay_hash"],
            "final_valid": reference["final_valid"],
            "final_update_id": reference["final_update_id"],
        },
        "errors": replay_errors,
    }
    replay_hash = hashlib.sha256(_canonical(report_body)).hexdigest()
    return {**report_body, "replay_hash": replay_hash}


def _reference_replay_records(
    ordered_records: Iterable[dict[str, Any]], market: str, symbol
) -> dict[str, Any]:
    """Independent Decimal-based reference replay; intentionally avoids LocalOrderBook."""
    bids: dict[Decimal, Decimal] = {}
    asks: dict[Decimal, Decimal] = {}
    last_update_id: int | None = None
    valid = False
    buffer: list[dict[str, Any]] = []
    depth_events = snapshots = applied = stale = gaps = 0
    errors: list[str] = []

    def decimal_value(value: Any) -> Decimal:
        try:
            result = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError("invalid_decimal_level") from exc
        if not result.is_finite():
            raise ValueError("non_finite_orderbook_level")
        return result

    def apply_levels(
        side: dict[Decimal, Decimal], updates: Any
    ) -> None:
        if not isinstance(updates, list):
            raise TypeError("orderbook_levels_must_be_a_list")
        for level in updates:
            if not isinstance(level, (list, tuple)) or len(level) != 2:
                raise ValueError("invalid_orderbook_level")
            price = decimal_value(level[0])
            quantity = decimal_value(level[1])
            if price <= 0 or quantity < 0:
                raise ValueError("invalid_orderbook_price_or_quantity")
            if quantity == 0:
                side.pop(price, None)
            else:
                side[price] = quantity

    def apply_diff(payload: dict[str, Any]) -> str:
        nonlocal last_update_id, valid
        if last_update_id is None:
            raise ValueError("snapshot_required")
        first = int(payload["U"])
        final = int(payload["u"])
        if first < 0 or final < first:
            raise ValueError("invalid_depth_sequence")
        if final <= last_update_id:
            return "stale"
        if first > last_update_id + 1:
            valid = False
            raise ValueError("depth_sequence_gap")
        if not first <= last_update_id + 1 <= final:
            valid = False
            raise ValueError("depth_sequence_invalid")
        apply_levels(bids, payload.get("b", []))
        apply_levels(asks, payload.get("a", []))
        last_update_id = final
        valid = True
        return "applied"

    for record in ordered_records:
        payload = record.get("payload", {})
        if record.get("stream") == "orderbook_depth":
            depth_events += 1
            if not valid:
                buffer.append(payload)
                continue
            try:
                result = apply_diff(payload)
                if result == "applied":
                    applied += 1
                else:
                    stale += 1
            except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
                valid = False
                buffer = [payload]
                gaps += 1
                errors.append(type(exc).__name__)
            continue

        if record.get("stream") != "orderbook_snapshots":
            continue
        snapshots += 1
        try:
            if str(payload["symbol"]).upper() != symbol.upper():
                raise ValueError("snapshot_symbol_mismatch")
            snapshot_id = int(payload["lastUpdateId"])
            if snapshot_id < 0:
                raise ValueError("invalid_snapshot_sequence")
            next_bids: dict[Decimal, Decimal] = {}
            next_asks: dict[Decimal, Decimal] = {}
            apply_levels(next_bids, payload["bids"])
            apply_levels(next_asks, payload["asks"])
            if not next_bids or not next_asks:
                raise ValueError("snapshot_missing_book_side")
            bids, asks = next_bids, next_asks
            last_update_id = snapshot_id
            valid = False
            bridged = False
            for buffered in buffer:
                if int(buffered["u"]) <= snapshot_id and not valid:
                    continue
                result = apply_diff(buffered)
                if result == "applied":
                    applied += 1
                    bridged = True
            if not bridged:
                raise ValueError("buffer_did_not_bridge_snapshot")
            buffer.clear()
        except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
            valid = False
            gaps += 1
            errors.append(type(exc).__name__)

    report_body = {
        "schema": "chartanalyzer_l2_reference_replay_v1",
        "market": market,
        "symbol": symbol,
        "depth_events": depth_events,
        "snapshots": snapshots,
        "applied_diffs": applied,
        "stale_diffs": stale,
        "sequence_gaps": gaps,
        "final_valid": valid,
        "final_update_id": last_update_id,
        "buffered_events": len(buffer),
        "best_bid": str(max(bids)) if valid and bids else None,
        "best_ask": str(min(asks)) if valid and asks else None,
        "errors": errors,
    }
    report_hash = hashlib.sha256(_canonical(report_body)).hexdigest()
    return {**report_body, "replay_hash": report_hash}


def reference_replay_orderbook_session(
    data_root: str | Path, market: str, session_id: str
) -> dict[str, Any]:
    """Run the independent Decimal reference engine against a sealed raw session."""
    manifest = build_session_manifest(data_root, market, session_id)
    if not manifest["session_closed"]:
        raise PITVaultError("cannot replay an active capture session")
    verify_session_manifest(data_root, manifest)
    if not manifest.get("symbol"):
        raise PITVaultError("session must identify exactly one symbol")
    root = Path(data_root).expanduser().resolve()
    ordered = (
        record for record in iter_session_records(root, manifest, session_id)
        if record.get("stream") in {"orderbook_depth", "orderbook_snapshots"}
    )
    result = _reference_replay_records(ordered, market, manifest["symbol"])
    verify_session_manifest(root, manifest)
    return {
        **result,
        "session_id": session_id,
        "manifest_hash": manifest["manifest_hash"],
        "session_complete": manifest["session_complete"],
        "data_quality_status": (
            "complete_capture" if manifest["session_complete"]
            else "incomplete_capture"
        ),
    }


__all__ = [
    "PITVaultError",
    "build_session_manifest",
    "iter_session_records",
    "persist_session_manifest",
    "reference_replay_orderbook_session",
    "replay_orderbook_session",
    "replay_historical_market_orders",
    "verify_session_manifest",
]


def _parse_replay_timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 string") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed


def replay_historical_market_orders(
    data_root: str | Path,
    market: str,
    session_id: str,
    orders: Iterable[dict[str, Any]],
    *,
    fee_schedule: dict[str, Any],
    max_book_age_ms: float = 500.0,
    book_depth: int = 1000,
) -> dict[str, Any]:
    """Replay hypothetical market orders using only L2 received by decision time.

    Each order requires order_id, decision_at, side, quantity and impact_bps.
    This estimates displayed depth only; it is not a realized or queue-aware fill.
    """
    if market not in {"spot", "usdm"}:
        raise ValueError("market must be spot or usdm")
    if isinstance(book_depth, bool) or not isinstance(book_depth, int) or not 1 <= book_depth <= 1000:
        raise ValueError("book_depth must be an integer from 1 to 1000")
    if isinstance(max_book_age_ms, bool):
        raise ValueError("max_book_age_ms must be numeric")
    try:
        age_limit = float(max_book_age_ms)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("max_book_age_ms must be numeric") from exc
    if not math.isfinite(age_limit) or age_limit < 0:
        raise ValueError("max_book_age_ms must be finite and >= 0")
    fees = validate_fee_schedule(fee_schedule)
    if fees["market"] != market:
        raise ValueError("fee schedule market does not match replay market")

    manifest = build_session_manifest(data_root, market, session_id)
    if not manifest.get("session_closed") or not manifest.get("session_complete"):
        raise PITVaultError("historical execution requires a closed, complete capture session")
    verify_session_manifest(data_root, manifest)
    if manifest.get("availability_contract", {}).get("available_at_field") != "received_at":
        raise PITVaultError("unsupported point-in-time availability contract")
    if manifest.get("event_time_quality", {}).get("event_time_after_receive_time", 0):
        raise PITVaultError("event timestamps after local receipt violate point-in-time ordering")

    integrity = replay_orderbook_session(data_root, market, session_id)
    if (
        not integrity.get("integrity_valid")
        or not integrity.get("reconstruction_agreement")
        or integrity.get("sequence_gaps") != 0
        or not integrity.get("final_valid")
        or integrity.get("data_quality_status") != "complete_capture"
    ):
        raise PITVaultError("order-book replay integrity or sequence validation failed")

    start = _parse_replay_timestamp(manifest["started_at"], "session.started_at")
    end = _parse_replay_timestamp(manifest["ended_at"], "session.ended_at")
    prepared = []
    seen_ids = set()
    for index, order in enumerate(orders):
        if not isinstance(order, dict):
            raise TypeError(f"orders[{index}] must be an object")
        order_id = str(order.get("order_id", "")).strip()
        if not order_id or order_id in seen_ids:
            raise ValueError("each order must have a unique non-empty order_id")
        seen_ids.add(order_id)
        decision = _parse_replay_timestamp(
            order.get("decision_at"), f"orders[{index}].decision_at"
        )
        if decision < start or decision > end:
            raise ValueError(f"order {order_id} decision is outside the capture session")
        side = str(order.get("side", "")).lower()
        if side not in {"buy", "sell"}:
            raise ValueError(f"order {order_id} side must be buy or sell")
        raw_quantity = order.get("quantity")
        if isinstance(raw_quantity, bool):
            raise ValueError(f"order {order_id} quantity must be numeric")
        try:
            quantity = float(raw_quantity)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"order {order_id} quantity must be numeric") from exc
        if not math.isfinite(quantity) or quantity <= 0:
            raise ValueError(f"order {order_id} quantity must be finite and > 0")
        if "impact_bps" not in order or isinstance(order["impact_bps"], bool):
            raise ValueError(f"order {order_id} requires numeric impact_bps")
        try:
            impact = float(order["impact_bps"])
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"order {order_id} impact_bps must be numeric") from exc
        if not math.isfinite(impact) or impact < 0:
            raise ValueError(f"order {order_id} impact_bps must be finite and >= 0")
        raw_events = order.get("funding_events", 0)
        if isinstance(raw_events, bool):
            raise ValueError(f"order {order_id} funding_events must be an integer")
        try:
            events = float(raw_events)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"order {order_id} funding_events must be an integer") from exc
        if not math.isfinite(events) or events < 0 or not events.is_integer():
            raise ValueError(f"order {order_id} funding_events must be an integer >= 0")
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
            received_at = _parse_replay_timestamp(
                current.get("received_at"), "record.received_at"
            )
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
                    # Synchronization only becomes knowable once the snapshot arrives.
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
            results.append({
                **common, "status": "blocked",
                "blocked_reason": "no_sequence_valid_book_at_decision",
            })
            continue

        try:
            top = book.top(book_depth)
            sweep = market_sweep_fills(
                top,
                side=side,
                requested_quantity=quantity,
                book_received_at=latest_book_received_at,
                decision_at=decision.isoformat(),
                max_age_ms=age_limit,
            )
        except (OrderBookError, ValueError, TypeError) as exc:
            reason = (
                "stale_order_book" if "stale" in str(exc).lower()
                else "invalid_order_book"
            )
            results.append({
                **common, "status": "blocked", "blocked_reason": reason,
                "detail": str(exc),
            })
            continue

        execution = evaluate_execution(
            side=side,
            order_type="market",
            requested_quantity=quantity,
            fills=sweep["fills"],
            fee_schedule=fees,
            reference_mid=top["mid"],
            best_bid=top["bid"],
            best_ask=top["ask"],
            impact_bps=impact,
            funding_rate=order.get("funding_rate"),
            funding_events=int(events),
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
        "engine": "HistoricalExecutionReplay 1.0",
        "market": market,
        "symbol": manifest["symbol"],
        "session_id": session_id,
        "manifest_hash": manifest["manifest_hash"],
        "orderbook_replay_hash": integrity["replay_hash"],
        "availability_field": "received_at",
        "orders": results,
        "summary": {
            "orders": len(results),
            "estimated_filled": sum(
                row.get("execution", {}).get("filled_quantity", 0.0) > 0
                for row in results
            ),
            "blocked": sum(row["status"] == "blocked" for row in results),
            "partial": sum(row["status"] == "partial" for row in results),
            "unfilled": sum(row["status"] == "unfilled" for row in results),
            "cost_models_incomplete": sum(
                row.get("cost_model_complete") is False for row in results
            ),
        },
    }
    return {**body, "replay_hash": hashlib.sha256(_canonical(body)).hexdigest()}



def _main() -> None:
    parser = argparse.ArgumentParser(description="Verify and replay ChartAnalyzer Binance PIT evidence.")
    parser.add_argument("--data-dir", default="./data/binance_raw")
    subparsers = parser.add_subparsers(dest="command", required=True)

    for command in ("manifest", "replay", "reference-replay"):
        sub = subparsers.add_parser(command)
        sub.add_argument("--market", choices=("spot", "usdm"), required=True)
        sub.add_argument("--session-id", required=True)

    verify = subparsers.add_parser("verify")
    verify.add_argument("--manifest", required=True)

    args = parser.parse_args()
    if args.command == "manifest":
        result = persist_session_manifest(args.data_dir, args.market, args.session_id)
        print(json.dumps({"manifest_path": str(result)}, indent=2))
    elif args.command == "verify":
        result = verify_session_manifest(args.data_dir, args.manifest)
        print(json.dumps(result, indent=2))
    elif args.command == "replay":
        result = replay_orderbook_session(args.data_dir, args.market, args.session_id)
        print(json.dumps(result, indent=2))
    else:
        result = reference_replay_orderbook_session(
            args.data_dir, args.market, args.session_id
        )
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    _main()
