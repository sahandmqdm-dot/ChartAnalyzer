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
from binance_stream_collector import HashChainJSONLStore


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
                            "session_chain_head": item["session_chain_head"],
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


def verify_snapshot_source_prefixes(
    data_root: str | Path, snapshot: dict[str, Any]
) -> dict[str, Any]:
    """Verify feature hashes and the exact raw-partition prefixes used by a snapshot.

    Live daily JSONL partitions may continue growing. The snapshot records the
    record/byte boundary and digest at decision time, so later appends are allowed
    while edits, truncation, or replacement of the consumed prefix are rejected.
    """
    if not isinstance(snapshot, dict):
        raise TypeError("snapshot must be an object")
    dataset_hash = snapshot.get("dataset_hash")
    dataset_body = dict(snapshot)
    dataset_body.pop("dataset_hash", None)
    if dataset_hash != hashlib.sha256(_canonical(dataset_body)).hexdigest():
        raise LiveSnapshotError("feature_dataset_hash_mismatch")
    snapshots = snapshot.get("snapshots")
    if not isinstance(snapshots, list) or not snapshots:
        raise LiveSnapshotError("feature_snapshots_missing")
    for feature in snapshots:
        if not isinstance(feature, dict):
            raise LiveSnapshotError("feature_snapshot_must_be_an_object")
        feature_hash = feature.get("feature_hash")
        feature_body = dict(feature)
        feature_body.pop("feature_hash", None)
        if feature_hash != hashlib.sha256(_canonical(feature_body)).hexdigest():
            raise LiveSnapshotError("feature_snapshot_hash_mismatch")
    source_snapshot = snapshot.get("source_snapshot")
    if not isinstance(source_snapshot, dict):
        raise LiveSnapshotError("source_snapshot_manifest_missing")
    if source_snapshot.get("manifest_hash") != snapshot.get("manifest_hash"):
        raise LiveSnapshotError("source_snapshot_manifest_identity_mismatch")

    root = Path(data_root).expanduser().resolve()
    verified = 0
    verified_records = 0
    for source in source_snapshot.get("source_files", []):
        relative = Path(str(source.get("path") or ""))
        path = (root / relative).resolve()
        if root not in path.parents or not path.is_file():
            raise LiveSnapshotError("snapshot_source_partition_missing_or_escaped")
        expected_records = source.get("records")
        expected_bytes = source.get("bytes")
        if (
            isinstance(expected_records, bool) or not isinstance(expected_records, int)
            or expected_records < 1
            or isinstance(expected_bytes, bool) or not isinstance(expected_bytes, int)
            or expected_bytes < 1
        ):
            raise LiveSnapshotError("snapshot_source_partition_boundary_invalid")
        prefix = bytearray()
        count = 0
        session_digest = hashlib.sha256()
        session_count = 0
        session_head = None
        try:
            with path.open("rb") as handle:
                for raw_line in handle:
                    prefix.extend(raw_line)
                    if not raw_line.strip():
                        continue
                    record = json.loads(raw_line.decode("utf-8"))
                    count += 1
                    if record.get("session_id") == snapshot.get("session_id"):
                        session_count += 1
                        session_digest.update(raw_line)
                        session_head = record.get("event_hash")
                    if count == expected_records:
                        break
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LiveSnapshotError("snapshot_source_partition_unreadable") from exc
        if count != expected_records or len(prefix) != expected_bytes:
            raise LiveSnapshotError("snapshot_source_partition_prefix_truncated_or_changed")
        if hashlib.sha256(prefix).hexdigest() != source.get("sha256"):
            raise LiveSnapshotError("snapshot_source_partition_prefix_hash_mismatch")

        with tempfile.TemporaryDirectory(prefix="chartanalyzer-prefix-verify-") as temporary:
            prefix_path = Path(temporary) / "prefix.jsonl"
            prefix_path.write_bytes(prefix)
            valid, verified_count, chain_head = HashChainJSONLStore.verify_file(prefix_path)
        if not valid or verified_count != expected_records or chain_head != source.get("chain_head"):
            raise LiveSnapshotError("snapshot_source_partition_prefix_chain_mismatch")
        if (
            session_count != source.get("session_records")
            or session_digest.hexdigest() != source.get("session_sha256")
            or session_head != source.get("session_chain_head")
        ):
            raise LiveSnapshotError("snapshot_source_session_prefix_mismatch")
        verified += 1
        verified_records += expected_records
    if not verified:
        raise LiveSnapshotError("snapshot_source_partitions_missing")
    return {
        "valid": True,
        "dataset_hash": dataset_hash,
        "partitions_verified": verified,
        "partition_prefix_records_verified": verified_records,
    }


def verify_snapshot_artifact(
    data_root: str | Path,
    artifact_path: str | Path,
    expected_sha256: str,
) -> dict[str, Any]:
    """Verify the persisted artifact bytes, feature hashes and raw source prefixes."""
    root = Path(data_root).expanduser().resolve()
    relative = Path(artifact_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise LiveSnapshotError("feature_artifact_path_invalid")
    path = (root / relative).resolve()
    if root not in path.parents or not path.is_file():
        raise LiveSnapshotError("feature_artifact_missing_or_escaped")
    payload = path.read_bytes()
    actual_sha256 = hashlib.sha256(payload).hexdigest()
    if actual_sha256 != expected_sha256:
        raise LiveSnapshotError("feature_artifact_sha256_mismatch")
    try:
        snapshot = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LiveSnapshotError("feature_artifact_unreadable") from exc
    if path.stem != snapshot.get("dataset_hash"):
        raise LiveSnapshotError("feature_artifact_content_address_mismatch")
    prefix_audit = verify_snapshot_source_prefixes(root, snapshot)
    return {
        **prefix_audit,
        "artifact_path": relative.as_posix(),
        "artifact_sha256": actual_sha256,
    }


def find_latest_active_session(
    data_root: str | Path,
    market: str,
    symbol: str,
    *,
    decision_time: str,
) -> str:
    """Find the newest active session for the exact market/symbol, started by cutoff."""
    if market not in {"spot", "usdm"}:
        raise ValueError("market must be spot or usdm")
    expected_symbol = symbol.upper()
    cutoff = datetime.fromisoformat(decision_time)
    if cutoff.tzinfo is None:
        raise ValueError("decision_time must be timezone-aware")
    root = Path(data_root).expanduser().resolve()
    manifest_dir = root / market / "session_manifest"
    if not manifest_dir.is_dir():
        raise LiveSnapshotError("session_manifest_partition_not_found")

    sessions: dict[str, dict[str, Any]] = {}
    for path in sorted(manifest_dir.glob("*.jsonl")):
        valid, _, _ = HashChainJSONLStore.verify_file(path)
        if not valid:
            raise LiveSnapshotError("session_manifest_hash_chain_invalid")
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    record = json.loads(line)
                    if not isinstance(record, dict) or record.get("stream") != "session_manifest":
                        continue
                    session_id = record.get("session_id")
                    payload = record.get("payload", {})
                    if not session_id or not isinstance(payload, dict):
                        continue
                    state = sessions.setdefault(str(session_id), {
                        "started_at": None, "terminal": False, "streams": [],
                    })
                    if payload.get("state") == "started":
                        state["started_at"] = payload.get("started_at")
                        state["streams"] = payload.get("streams", [])
                    elif payload.get("state") in {"ended", "interrupted"}:
                        state["terminal"] = True
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LiveSnapshotError("session_manifest_partition_unreadable") from exc

    candidates = []
    for session_id, state in sessions.items():
        if state["terminal"] or not state["started_at"]:
            continue
        streams = state["streams"]
        if not any(
            isinstance(item, str)
            and item.split("@", 1)[0].upper() == expected_symbol
            for item in streams
        ):
            continue
        started = datetime.fromisoformat(str(state["started_at"]))
        if started.tzinfo is None or started.astimezone(UTC) > cutoff.astimezone(UTC):
            continue
        candidates.append((started.astimezone(UTC), session_id))
    if not candidates:
        raise LiveSnapshotError("no_active_session_for_symbol_at_decision_cutoff")
    return max(candidates, key=lambda item: item[0])[1]


def persist_snapshot_content_addressed(
    snapshot: dict[str, Any], data_root: str | Path
) -> dict[str, str]:
    """Persist a snapshot under its dataset hash with exclusive creation."""
    dataset_hash = str(snapshot.get("dataset_hash") or "")
    if len(dataset_hash) != 64 or any(ch not in "0123456789abcdef" for ch in dataset_hash):
        raise ValueError("snapshot dataset_hash must be a lowercase SHA-256 digest")
    root = Path(data_root).expanduser().resolve()
    directory = root / "vault" / "features"
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f"{dataset_hash}.json"
    payload = _canonical(snapshot) + b"\\n"
    artifact_hash = hashlib.sha256(payload).hexdigest()
    try:
        fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        if destination.read_bytes() != payload:
            raise LiveSnapshotError("content_addressed_feature_artifact_mismatch")
    else:
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            if os.name == "posix":
                directory_fd = os.open(directory, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        except Exception:
            destination.unlink(missing_ok=True)
            raise
    return {
        "artifact_path": destination.relative_to(root).as_posix(),
        "artifact_sha256": artifact_hash,
    }


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
