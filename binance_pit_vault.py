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
                        if record.get("session_id") != session_id:
                            continue
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

    for record in iter_session_records(root, {"source_files": files}, session_id):
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

    expected_hash = document.pop("manifest_hash", None)
    actual_hash = hashlib.sha256(_canonical(document)).hexdigest()
    if expected_hash != actual_hash:
        raise PITVaultError("manifest_hash_mismatch")

    for source in document.get("source_files", []):
        path = (root / source["path"]).resolve()
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
    "verify_session_manifest",
]



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
