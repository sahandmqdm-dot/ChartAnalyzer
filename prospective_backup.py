"""Consistent online SQLite backups for prospective holdout ledgers."""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sqlite3
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

LOG = logging.getLogger("chartanalyzer.prospective_backup")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def backup_ledgers(source_dir: str | Path, backup_dir: str | Path, retention_days: int = 30) -> list[dict]:
    source_root = Path(source_dir).expanduser().resolve()
    backup_root = Path(backup_dir).expanduser().resolve()
    if not source_root.is_dir():
        raise FileNotFoundError(f"prospective ledger directory does not exist: {source_root}")
    if not 1 <= int(retention_days) <= 3650:
        raise ValueError("retention_days must be between 1 and 3650")
    sources = sorted(path for path in source_root.glob("*.sqlite3") if path.is_file() and not path.is_symlink())
    if not sources:
        LOG.warning("no prospective SQLite ledgers found in %s", source_root)
        return []
    backup_root.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        os.chmod(backup_root, 0o700)

    now = datetime.now(UTC)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    completed = []
    for source in sources:
        safe_name = source.name.replace("/", "_").replace("\\", "_")
        destination = backup_root / f"{safe_name}.{stamp}.bak"
        fd, temporary_name = tempfile.mkstemp(prefix=destination.name + ".", suffix=".tmp", dir=backup_root)
        os.close(fd)
        temporary = Path(temporary_name)
        try:
            source_uri = f"file:{source.as_posix()}?mode=ro"
            with sqlite3.connect(source_uri, uri=True, timeout=30) as src:
                with sqlite3.connect(temporary, timeout=30) as dst:
                    src.backup(dst, pages=256, sleep=0.05)
                    check = dst.execute("PRAGMA integrity_check").fetchone()
                    if not check or check[0] != "ok":
                        raise sqlite3.DatabaseError(f"backup integrity_check failed for {source.name}: {check}")
                    dst.commit()
            if os.name == "posix":
                os.chmod(temporary, 0o600)
            os.replace(temporary, destination)
            digest = _sha256(destination)
            manifest = {
                "source": source.name,
                "backup": destination.name,
                "created_at": now.isoformat(),
                "sha256": digest,
                "size_bytes": destination.stat().st_size,
                "integrity_check": "ok",
            }
            manifest_path = destination.with_suffix(destination.suffix + ".json")
            manifest_tmp = manifest_path.with_suffix(manifest_path.suffix + ".tmp")
            manifest_tmp.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")
            if os.name == "posix":
                os.chmod(manifest_tmp, 0o600)
            os.replace(manifest_tmp, manifest_path)
            completed.append(manifest)
            LOG.info("verified backup created: %s sha256=%s", destination, digest)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    cutoff = now - timedelta(days=int(retention_days))
    for item in backup_root.glob("*.bak"):
        if item.is_file() and datetime.fromtimestamp(item.stat().st_mtime, UTC) < cutoff:
            item.unlink()
            manifest = item.with_suffix(item.suffix + ".json")
            if manifest.exists():
                manifest.unlink()
    return completed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create verified online backups of prospective holdout SQLite ledgers")
    parser.add_argument("--source-dir", default=os.getenv(
        "CHARTANALYZER_DATA_DIR",
        str(Path.home() / ".local" / "state" / "chartanalyzer" / "prospective_holdouts"),
    ))
    parser.add_argument("--backup-dir", default=os.getenv(
        "CHARTANALYZER_BACKUP_DIR", "/var/backups/chartanalyzer/prospective_holdouts",
    ))
    parser.add_argument("--retention-days", type=int, default=30)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        results = backup_ledgers(args.source_dir, args.backup_dir, args.retention_days)
        LOG.info("backup run complete: %d ledger(s)", len(results))
        return 0
    except Exception:
        LOG.exception("prospective holdout backup failed")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
