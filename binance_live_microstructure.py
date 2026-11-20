"""Build causal microstructure snapshots from a verified copy of active raw partitions.

The collector remains the sole writer. This reader snapshots the append-only files
into a temporary workspace, verifies the copied hash chains, and then computes
features strictly by local received_at. It never mutates the live capture.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from binance_microstructure_features import (
    PITFeatureError,
    build_pit_microstructure_features,
)
from binance_pit_vault import PITVaultError, build_session_manifest


class LiveSnapshotError(RuntimeError):
    """Raised when an active-session snapshot cannot be proven internally consistent."""


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def build_active_session_snapshot(
    data_root: str | Path,
    market: str,
    session_id: str,
    decision_time: str,
    *,
    window_seconds: tuple[int, ...] = (5, 60, 300),
    book_depth: int = 10,
    retries: int = 3,
) -> dict[str, Any]:
    """Snapshot active partitions, verify the copy, and build one causal feature point."""
    if market not in {"spot", "usdm"}:
        raise ValueError("market must be spot or usdm")
    if not isinstance(retries, int) or isinstance(retries, bool) or retries < 1 or retries > 10:
        raise ValueError("retries must be an integer between 1 and 10")
    root = Path(data_root).expanduser().resolve()
    if not root.is_dir():
        raise LiveSnapshotError("data_root_not_found")

    last_error: Exception | None = None
    for attempt in range(retries):
        with tempfile.TemporaryDirectory(prefix="chartanalyzer-pit-snapshot-") as temporary:
            snapshot_root = Path(temporary)
            try:
                source_manifest = build_session_manifest(root, market, session_id)
                if source_manifest["session_closed"]:
                    raise LiveSnapshotError("active_session_required_for_live_snapshot")
                for source in source_manifest["source_files"]:
                    relative = Path(source["path"])
                    source_path = (root / relative).resolve()
                    if root not in source_path.parents or not source_path.is_file():
                        raise LiveSnapshotError("source_partition_missing_or_escaped")
                    destination = snapshot_root / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source_path, destination)

                # Rebuild the manifest from the copied files. This verifies every
                # copied hash chain and binds the feature build to this exact snapshot.
                copied_manifest = build_session_manifest(snapshot_root, market, session_id)
                if copied_manifest["session_closed"]:
                    raise LiveSnapshotError("snapshot_unexpectedly_contains_terminal_session")
                event_counts = copied_manifest["event_counts"]
                raw_frames = int(event_counts.get("raw_frames", 0))
                derived_frames = sum(
                    int(event_counts.get(stream, 0))
                    for stream in (
                        "trades", "orderbook_depth", "liquidations",
                        "unknown", "collector_errors",
                    )
                )
                # The collector persists each raw frame before deriving its typed
                # event. A mismatch means the copy may have caught the writer
                # between those operations, or the session has a capture defect.
                if raw_frames < 1 or raw_frames != derived_frames:
                    raise LiveSnapshotError("raw_frame_typed_event_count_mismatch")
                result = build_pit_microstructure_features(
                    snapshot_root,
                    market,
                    session_id,
                    [decision_time],
                    window_seconds=window_seconds,
                    book_depth=book_depth,
                    allow_incomplete=True,
                )
                snapshot_created_at = datetime.now(UTC).isoformat()
                result["snapshot_mode"] = "verified_active_partition_copy"
                result["snapshot_created_at"] = snapshot_created_at
                result["source_snapshot"] = {
                    "manifest_hash": copied_manifest["manifest_hash"],
                    "source_files": [
                        {
                            "path": item["path"],
                            "sha256": item["sha256"],
                            "bytes": item["bytes"],
                            "records": item["records"],
                            "session_records": item["session_records"],
                            "chain_head": item["chain_head"],
                            "session_sha256": item["session_sha256"],
                        }
                        for item in copied_manifest["source_files"]
                    ],
                }
                result.pop("dataset_hash", None)
                result["dataset_hash"] = hashlib.sha256(_canonical(result)).hexdigest()
                snapshot = result["snapshots"][0]
                if not snapshot["point_in_time_valid"]:
                    raise LiveSnapshotError("feature_snapshot_contains_future_availability")
                return result
            except (PITVaultError, PITFeatureError, OSError, ValueError, LiveSnapshotError) as exc:
                last_error = exc
                # A collector may have been appending while a file was copied.
                # Retry from a fresh copy; never accept a partial hash chain.
                if isinstance(exc, LiveSnapshotError) and str(exc) in {
                    "active_session_required_for_live_snapshot",
                    "data_root_not_found",
                    "source_partition_missing_or_escaped",
                }:
                    raise
        if attempt + 1 < retries:
            continue
    raise LiveSnapshotError(
        "could_not_create_consistent_active_snapshot"
    ) from last_error


def write_snapshot(snapshot: dict[str, Any], output: str | Path) -> Path:
    """Atomically persist the snapshot without modifying source partitions."""
    destination = Path(output).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = _canonical(snapshot) + b"\\n"
    fd, temporary_name = tempfile.mkstemp(
        prefix=".live-pit-snapshot-", dir=destination.parent
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, destination)
        if os.name == "posix":
            directory_fd = os.open(destination.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)
    return destination


def _main() -> None:
    parser = argparse.ArgumentParser(
        description="Build a verified point-in-time feature snapshot from an active Binance session."
    )
    parser.add_argument("--data-dir", default=os.environ.get("CHARTANALYZER_DATA_DIR", "./data/binance_raw"))
    parser.add_argument("--market", choices=("spot", "usdm"), required=True)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--decision-time", required=True)
    parser.add_argument("--windows", default="5,60,300")
    parser.add_argument("--book-depth", type=int, default=10)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    windows = tuple(int(value.strip()) for value in args.windows.split(",") if value.strip())
    result = build_active_session_snapshot(
        args.data_dir,
        args.market,
        args.session_id,
        args.decision_time,
        window_seconds=windows,
        book_depth=args.book_depth,
        retries=args.retries,
    )
    path = write_snapshot(result, args.output)
    print(json.dumps({
        "output": str(path),
        "dataset_hash": result["dataset_hash"],
        "feature_hash": result["snapshots"][0]["feature_hash"],
        "decision_time": result["snapshots"][0]["decision_time"],
        "session_complete": result["session_complete"],
        "book_valid": result["snapshots"][0]["book"]["valid"],
    }, indent=2))


if __name__ == "__main__":
    _main()
