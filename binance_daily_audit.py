"""Immutable UTC daily audit manifests for sealed Binance raw-data partitions."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from binance_stream_collector import HashChainJSONLStore


class DailyAuditError(RuntimeError):
    """Raised when a daily partition cannot be safely sealed or verified."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_daily_manifest(data_root: str | Path, day: str, *, now: datetime | None = None) -> dict[str, Any]:
    """Build a deterministic audit manifest for a fully closed UTC day."""
    try:
        target_day = date.fromisoformat(day)
    except (TypeError, ValueError) as exc:
        raise ValueError("day must be YYYY-MM-DD") from exc
    current = now or datetime.now(UTC)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    if target_day >= current.astimezone(UTC).date():
        raise DailyAuditError("only_completed_utc_days_can_be_sealed")
    root = Path(data_root).expanduser().resolve()
    if not root.is_dir():
        raise DailyAuditError("data_root_not_found")

    files: list[dict[str, Any]] = []
    streams: Counter[str] = Counter()
    terminations: Counter[str] = Counter()
    book_states: Counter[str] = Counter()
    sessions: dict[str, dict[str, Any]] = {}
    total = 0
    first = last = None
    markets: set[str] = set()

    for path in sorted(root.glob(f"*/*/{target_day.isoformat()}.jsonl")):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        market, stream = path.parent.parent.name, path.parent.name
        valid, verified_count, chain_head = HashChainJSONLStore.verify_file(path)
        if not valid:
            raise DailyAuditError(f"partition_hash_chain_invalid:{relative}")
        count = 0
        file_first = file_last = None
        with path.open("r", encoding="utf-8") as handle:
            for number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise DailyAuditError(f"malformed_record:{relative}:{number}") from exc
                if not isinstance(record, dict) or record.get("market") != market or record.get("stream") != stream:
                    raise DailyAuditError(f"partition_identity_mismatch:{relative}:{number}")
                try:
                    received = datetime.fromisoformat(str(record["received_at"]))
                except (KeyError, TypeError, ValueError) as exc:
                    raise DailyAuditError(f"invalid_received_at:{relative}:{number}") from exc
                if received.tzinfo is None or received.astimezone(UTC).date() != target_day:
                    raise DailyAuditError(f"record_outside_utc_partition_day:{relative}:{number}")
                stamp = received.astimezone(UTC).isoformat()
                file_first = file_first or stamp
                file_last = stamp
                first = min(first, stamp) if first else stamp
                last = max(last, stamp) if last else stamp
                count += 1
                total += 1
                streams[stream] += 1
                markets.add(market)
                session_id = record.get("session_id")
                if session_id:
                    state = sessions.setdefault(str(session_id), {"market": market, "started": False, "terminal": None})
                    if state["market"] != market:
                        raise DailyAuditError("session_id_reused_across_markets")
                    payload = record.get("payload", {})
                    if stream == "session_manifest" and isinstance(payload, dict):
                        if payload.get("state") == "started":
                            state["started"] = True
                        elif payload.get("state") in {"ended", "interrupted"}:
                            state["terminal"] = payload["state"]
                            terminations[str(payload.get("termination") or payload["state"])] += 1
                    elif stream == "orderbook_state" and isinstance(payload, dict):
                        book_states[str(payload.get("state", "unknown"))] += 1
        if count != verified_count:
            raise DailyAuditError(f"partition_record_count_changed:{relative}")
        files.append({
            "path": relative, "sha256": _sha256(path), "bytes": path.stat().st_size,
            "records": count, "first_received_at": file_first,
            "last_received_at": file_last, "chain_head": chain_head,
        })

    if not files:
        raise DailyAuditError("no_partitions_found_for_day")
    unsealed = sorted(key for key, value in sessions.items() if not value["started"] or value["terminal"] is None)
    if unsealed:
        raise DailyAuditError("day_contains_unsealed_sessions:" + ",".join(unsealed[:10]))
    body = {
        "schema": "chartanalyzer_binance_daily_audit_v1",
        "utc_day": target_day.isoformat(),
        "markets": sorted(markets),
        "sealed_after_utc_day": datetime.combine(target_day + timedelta(days=1), time.min, tzinfo=UTC).isoformat(),
        "total_records": total,
        "session_count": len(sessions),
        "complete_sessions": sum(item["terminal"] == "ended" for item in sessions.values()),
        "interrupted_sessions": sum(item["terminal"] == "interrupted" for item in sessions.values()),
        "received_time_range": {"first": first, "last": last},
        "stream_counts": dict(sorted(streams.items())),
        "termination_counts": dict(sorted(terminations.items())),
        "orderbook_state_counts": dict(sorted(book_states.items())),
        "source_partitions": files,
        "archive_contract": "store this content-addressed manifest outside data_root on independent durable storage",
    }
    return {**body, "manifest_hash": hashlib.sha256(_canonical(body)).hexdigest()}


def seal_daily_manifest(data_root: str | Path, archive_dir: str | Path, day: str) -> Path:
    """Write a content-addressed manifest to a distinct archive directory, never overwrite."""
    root, archive = Path(data_root).expanduser().resolve(), Path(archive_dir).expanduser().resolve()
    if archive == root or root in archive.parents or archive in root.parents:
        raise DailyAuditError("archive_dir_must_be_separate_from_data_root")
    manifest = build_daily_manifest(root, day)
    archive.mkdir(parents=True, exist_ok=True)
    destination = archive / f"{day}-{manifest['manifest_hash']}.json"
    payload = _canonical(manifest) + b"\n"
    try:
        fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        if destination.read_bytes() != payload:
            raise DailyAuditError("existing_content_addressed_manifest_mismatch")
        return destination
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if os.name == "posix":
            directory_fd = os.open(archive, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return destination


def verify_daily_manifest(data_root: str | Path, manifest_path: str | Path) -> dict[str, Any]:
    """Verify manifest identity and every referenced partition digest/hash chain."""
    root = Path(data_root).expanduser().resolve()
    document = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise DailyAuditError("manifest_must_be_object")
    expected = document.pop("manifest_hash", None)
    if expected != hashlib.sha256(_canonical(document)).hexdigest():
        raise DailyAuditError("daily_manifest_hash_mismatch")
    checked = 0
    for source in document.get("source_partitions", []):
        path = (root / source["path"]).resolve()
        if root not in path.parents or not path.is_file():
            raise DailyAuditError("source_partition_missing_or_escaped")
        valid, count, head = HashChainJSONLStore.verify_file(path)
        if not valid or count != source["records"] or head != source["chain_head"] or _sha256(path) != source["sha256"]:
            raise DailyAuditError("source_partition_integrity_mismatch:" + source["path"])
        checked += 1
    return {"valid": True, "manifest_hash": expected, "partitions_verified": checked, "records": document["total_records"]}


def _main() -> None:
    parser = argparse.ArgumentParser(description="Seal and verify independent Binance daily audit manifests.")
    parser.add_argument("--data-dir", default="./data/binance_raw")
    sub = parser.add_subparsers(dest="command", required=True)
    seal = sub.add_parser("seal")
    seal.add_argument("--day", required=True)
    seal.add_argument("--archive-dir", required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("--manifest", required=True)
    args = parser.parse_args()
    result = seal_daily_manifest(args.data_dir, args.archive_dir, args.day) if args.command == "seal" else verify_daily_manifest(args.data_dir, args.manifest)
    print(json.dumps({"manifest_path": str(result)} if isinstance(result, Path) else result, indent=2))


if __name__ == "__main__":
    _main()
