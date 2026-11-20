"""Idempotent cloud archival of verified, closed Binance JSONL partitions."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Protocol

class ArchiveError(RuntimeError):
    """Raised when a raw partition is unsafe to archive."""

class ObjectStoreClient(Protocol):
    def upload_file(self, Filename: str, Bucket: str, Key: str,
                    ExtraArgs: dict[str, Any] | None = None) -> Any: ...

@dataclass(frozen=True, slots=True)
class ArchiveResult:
    source: str
    object_key: str
    sha256: str
    event_count: int
    bytes: int
    uploaded: bool

def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def discover_closed_partitions(root: str | Path, *, today: date | None = None) -> list[Path]:
    """Find dated JSONL partitions strictly older than today (UTC by default)."""
    base = Path(root).expanduser().resolve()
    current_day = today or datetime.now(UTC).date()
    if not base.exists():
        return []
    candidates = []
    for path in sorted(base.glob("*/*/*.jsonl")):
        try:
            partition_day = date.fromisoformat(path.stem)
        except ValueError:
            continue
        if partition_day < current_day and path.is_file():
            candidates.append(path)
    return candidates

def archive_partition(path: str | Path, *, root: str | Path, bucket: str,
                      client: ObjectStoreClient, prefix: str = "chartanalyzer/raw",
                      dry_run: bool = False) -> ArchiveResult:
    """Verify and upload one closed partition using a content-addressed object key."""
    base = Path(root).expanduser().resolve()
    source = Path(path).expanduser().resolve()
    try:
        relative = source.relative_to(base)
    except ValueError as exc:
        raise ArchiveError("partition must be inside the configured data root") from exc
    if len(relative.parts) != 3 or relative.suffix != ".jsonl":
        raise ArchiveError("expected market/stream/YYYY-MM-DD.jsonl")
    market, stream, filename = relative.parts
    if market not in {"spot", "usdm"}:
        raise ArchiveError("unknown market partition")
    try:
        date.fromisoformat(Path(filename).stem)
    except ValueError as exc:
        raise ArchiveError("partition filename must be an ISO UTC date") from exc
    from binance_stream_collector import HashChainJSONLStore
    valid, event_count, head_hash = HashChainJSONLStore.verify_file(source)
    if not valid or event_count < 1 or not head_hash:
        raise ArchiveError("partition hash chain is invalid or empty; refusing cloud upload")
    content_hash = _file_sha256(source)
    parts = [prefix.strip("/"), market, stream, f"date={Path(filename).stem}",
             f"sha256={content_hash}.jsonl"]
    object_key = "/".join(part for part in parts if part)
    size = source.stat().st_size
    if not dry_run:
        client.upload_file(
            str(source), bucket, object_key,
            ExtraArgs={"Metadata": {
                "sha256": content_hash, "event-count": str(event_count),
                "hash-chain-head": head_hash, "schema": "binance_raw_event_v1",
                "market": market, "stream": stream,
                "partition-date": Path(filename).stem,
            }},
        )
    return ArchiveResult(str(source), object_key, content_hash, event_count, size, not dry_run)

def sync_closed_partitions(root: str | Path, *, bucket: str,
                           client: ObjectStoreClient, prefix: str = "chartanalyzer/raw",
                           today: date | None = None, dry_run: bool = False) -> list[ArchiveResult]:
    """Archive eligible partitions; retries use deterministic content-addressed keys."""
    return [archive_partition(path, root=root, bucket=bucket, client=client,
                              prefix=prefix, dry_run=dry_run)
            for path in discover_closed_partitions(root, today=today)]

def _main() -> None:
    parser = argparse.ArgumentParser(description="Verify and mirror closed Binance partitions to S3-compatible storage.")
    parser.add_argument("--data-dir", default=os.environ.get("CHARTANALYZER_DATA_DIR", "./data/binance_raw"))
    parser.add_argument("--bucket", default=os.environ.get("CHARTANALYZER_ARCHIVE_BUCKET"))
    parser.add_argument("--prefix", default=os.environ.get("CHARTANALYZER_ARCHIVE_PREFIX", "chartanalyzer/raw"))
    parser.add_argument("--endpoint-url", default=os.environ.get("CHARTANALYZER_S3_ENDPOINT_URL"))
    parser.add_argument("--region", default=os.environ.get("AWS_DEFAULT_REGION"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not args.bucket:
        parser.error("set --bucket or CHARTANALYZER_ARCHIVE_BUCKET")
    try:
        import boto3
    except ImportError as exc:
        raise SystemExit("Cloud archival requires optional dependency: python -m pip install boto3") from exc
    client = boto3.client("s3", endpoint_url=args.endpoint_url, region_name=args.region)
    results = sync_closed_partitions(args.data_dir, bucket=args.bucket, client=client,
                                     prefix=args.prefix, dry_run=args.dry_run)
    print(json.dumps([asdict(item) for item in results], indent=2))
    print(f"partitions={len(results)} uploaded={sum(item.uploaded for item in results)}")

if __name__ == "__main__":
    _main()
