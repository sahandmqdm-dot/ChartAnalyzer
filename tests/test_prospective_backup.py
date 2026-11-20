import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from prospective_backup import backup_ledgers


def _make_ledger(path: Path):
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY, value TEXT)")
        connection.execute("INSERT INTO sample(value) VALUES ('holdout-evidence')")


def test_online_backup_is_integrity_checked_and_has_sha_manifest(tmp_path):
    source = tmp_path / "source"
    target = tmp_path / "backups"
    source.mkdir()
    ledger = source / "cohort.sqlite3"
    _make_ledger(ledger)

    results = backup_ledgers(source, target)
    assert len(results) == 1
    manifest = results[0]
    backup_path = target / manifest["backup"]
    assert manifest["integrity_check"] == "ok"
    assert backup_path.exists()
    assert len(manifest["sha256"]) == 64
    with sqlite3.connect(backup_path) as connection:
        assert connection.execute("SELECT value FROM sample").fetchone()[0] == "holdout-evidence"
    saved_manifest = json.loads(backup_path.with_suffix(".bak.json").read_text(encoding="utf-8"))
    assert saved_manifest["sha256"] == manifest["sha256"]


def test_backup_uses_sqlite_online_backup_for_wal_database(tmp_path):
    source = tmp_path / "source"
    target = tmp_path / "backups"
    source.mkdir()
    ledger = source / "cohort.sqlite3"
    connection = sqlite3.connect(ledger)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("CREATE TABLE sample (value TEXT)")
    connection.execute("INSERT INTO sample VALUES ('wal-record')")
    connection.commit()

    results = backup_ledgers(source, target)
    connection.close()
    with sqlite3.connect(target / results[0]["backup"]) as copied:
        assert copied.execute("SELECT value FROM sample").fetchone()[0] == "wal-record"


def test_backup_rejects_invalid_retention_and_missing_source(tmp_path):
    with pytest.raises(ValueError, match="retention_days"):
        backup_ledgers(tmp_path, tmp_path / "backups", 0)
    with pytest.raises(FileNotFoundError):
        backup_ledgers(tmp_path / "missing", tmp_path / "backups")


def test_backup_prunes_expired_backup_and_manifest(tmp_path):
    source = tmp_path / "source"
    target = tmp_path / "backups"
    source.mkdir()
    target.mkdir()
    old = target / "old.sqlite3.20000101T000000Z.bak"
    old.write_bytes(b"old")
    old_manifest = old.with_suffix(old.suffix + ".json")
    old_manifest.write_text("{}", encoding="utf-8")
    past = (datetime.now(UTC) - timedelta(days=60)).timestamp()
    import os
    os.utime(old, (past, past))
    os.utime(old_manifest, (past, past))
    _make_ledger(source / "new.sqlite3")

    backup_ledgers(source, target, retention_days=30)
    assert not old.exists()
    assert not old_manifest.exists()
    assert list(target.glob("*.bak"))
