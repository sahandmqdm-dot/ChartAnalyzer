"""Point-in-time evidence manifests and deterministic Binance L2 replay.

Hash chains detect accidental or unsophisticated edits, while file digests bind a
session manifest to exact raw partitions. This is tamper-evident, not immutable
storage or a cryptographic signature.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from binance_orderbook import DepthSnapshot, LocalOrderBook, OrderBookError, synchronize_buffered
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


def _read_partition(path: Path) -> list[dict[str, Any]]:
    valid, _, error = HashChainJSONLStore.verify_file(path)
    if not valid:
        raise PITVaultError(f"hash chain failed for {path.name}: {error}")
    records = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                records.append(json.loads(line))
    return records


def _session_partitions(
    root: Path, market: str, session_id: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    market_root = (root / market).resolve()
    if not market_root.is_dir():
        raise PITVaultError(f"market partition does not exist: {market}")
    records: list[dict[str, Any]] = []
    files: list[dict[str, Any]] = []
    for path in sorted(market_root.rglob("*.jsonl")):
        partition_records = _read_partition(path)
        selected = [r for r in partition_records if r.get("session_id") == session_id]
        if not selected:
            continue
        relative = path.relative_to(root).as_posix()
        files.append({
            "path": relative,
            "sha256": _sha256_file(path),
            "bytes": path.stat().st_size,
            "records": len(partition_records),
            "session_records": len(selected),
            "chain_head": partition_records[-1].get("event_hash") if partition_records else None,
        })
        records.extend(selected)
    if not records:
        raise PITVaultError(f"no records found for session {session_id}")
    records.sort(key=lambda r: (str(r.get("received_at", "")), str(r.get("stream", ""))))
    return records, files


def build_session_manifest(
    data_root: str | Path, market: str, session_id: str
) -> dict[str, Any]:
    """Create a deterministic content-addressed manifest for one capture session."""
    if market not in {"spot", "usdm"}:
        raise ValueError("market must be spot or usdm")
    if not session_id or not isinstance(session_id, str):
        raise ValueError("session_id is required")

    root = Path(data_root).expanduser().resolve()
    records, files = _session_partitions(root, market, session_id)
    manifests = [
        r.get("payload", {}) for r in records
        if r.get("stream") == "session_manifest"
    ]
    started = next((m for m in manifests if m.get("state") == "started"), None)
    if started is None:
        raise PITVaultError("session start manifest is missing")

    event_counts: dict[str, int] = {}
    for record in records:
        stream = str(record.get("stream", "unknown"))
        event_counts[stream] = event_counts.get(stream, 0) + 1

    streams = started.get("streams", [])
    symbols = sorted({
        str(stream).split("@", 1)[0].upper()
        for stream in streams if isinstance(stream, str) and "@" in stream
    })
    body = {
        "schema": "chartanalyzer_pit_session_manifest_v1",
        "market": market,
        "session_id": session_id,
        "symbol": symbols[0] if len(symbols) == 1 else None,
        "started_at": started.get("started_at"),
        "ended_at": next(
            (m.get("ended_at") for m in reversed(manifests)
             if m.get("state") in {"ended", "interrupted"}), None
        ),
        "streams": streams,
        "event_counts": event_counts,
        "total_session_records": len(records),
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
    verify_session_manifest(data_root, manifest)
    root = Path(data_root).expanduser().resolve()
    records, _ = _session_partitions(root, market, session_id)
    symbol = manifest.get("symbol")
    if not symbol:
        raise PITVaultError("session must identify exactly one symbol")

    book = LocalOrderBook(market, symbol)
    buffer: list[dict[str, Any]] = []
    depth_events = snapshots = applied = stale = gaps = 0
    replay_errors: list[str] = []

    ordered = sorted(
        (r for r in records if r.get("stream") in {"orderbook_depth", "orderbook_snapshots"}),
        key=lambda r: (
            str(r.get("received_at", "")),
            0 if r.get("stream") == "orderbook_depth" else 1,
        ),
    )
    for record in ordered:
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

    report_body = {
        "schema": "chartanalyzer_l2_replay_report_v1",
        "market": market,
        "symbol": symbol,
        "session_id": session_id,
        "manifest_hash": manifest["manifest_hash"],
        "integrity_valid": True,
        "depth_events": depth_events,
        "snapshots": snapshots,
        "applied_diffs": applied,
        "stale_diffs": stale,
        "sequence_gaps": gaps,
        "final_valid": book.valid,
        "final_update_id": book.sequence.last_update_id,
        "buffered_events": len(buffer),
        "errors": replay_errors,
    }
    replay_hash = hashlib.sha256(_canonical(report_body)).hexdigest()
    return {**report_body, "replay_hash": replay_hash}


__all__ = [
    "PITVaultError",
    "build_session_manifest",
    "persist_session_manifest",
    "replay_orderbook_session",
    "verify_session_manifest",
]



def _main() -> None:
    parser = argparse.ArgumentParser(description="Verify and replay ChartAnalyzer Binance PIT evidence.")
    parser.add_argument("--data-dir", default="./data/binance_raw")
    subparsers = parser.add_subparsers(dest="command", required=True)

    for command in ("manifest", "replay"):
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
    else:
        result = replay_orderbook_session(args.data_dir, args.market, args.session_id)
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    _main()
