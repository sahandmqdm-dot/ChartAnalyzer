from datetime import UTC, datetime

import pytest

from binance_daily_audit import (
    DailyAuditError,
    build_daily_manifest,
    seal_daily_manifest,
    verify_daily_manifest,
)
from binance_stream_collector import HashChainJSONLStore


DAY = "2026-11-19"
NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


def _session(root, *, terminal="ended"):
    store = HashChainJSONLStore(root, fsync_each_event=False)
    store.append(
        "spot", "session_manifest",
        {"state": "started", "streams": ["btcusdt@trade"], "started_at": f"{DAY}T10:00:00+00:00"},
        received_at=f"{DAY}T10:00:00+00:00", session_id="daily-session",
    )
    store.append(
        "spot", "trades",
        {"s": "BTCUSDT", "p": "90000", "q": "0.1", "m": False},
        received_at=f"{DAY}T10:00:01+00:00", session_id="daily-session",
    )
    store.append(
        "spot", "session_manifest",
        {"state": terminal, "ended_at": f"{DAY}T10:01:00+00:00", "termination": "operator_stop"},
        received_at=f"{DAY}T10:01:00+00:00", session_id="daily-session",
    )
    store.close()


def test_daily_manifest_is_content_addressed_and_verifies_external_archive(tmp_path):
    data = tmp_path / "raw"
    archive = tmp_path / "independent-archive"
    _session(data)
    manifest = build_daily_manifest(data, DAY, now=NOW)
    assert manifest["total_records"] == 3
    assert manifest["session_count"] == 1
    assert manifest["complete_sessions"] == 1
    assert manifest["stream_counts"] == {"session_manifest": 2, "trades": 1}

    path = seal_daily_manifest(data, archive, DAY)
    assert path.parent == archive
    assert path.name.startswith(DAY + "-")
    assert verify_daily_manifest(data, path) == {
        "valid": True,
        "manifest_hash": manifest["manifest_hash"],
        "partitions_verified": 2,
        "records": 3,
    }
    assert seal_daily_manifest(data, archive, DAY) == path


def test_daily_manifest_refuses_active_sessions(tmp_path):
    store = HashChainJSONLStore(tmp_path, fsync_each_event=False)
    store.append(
        "spot", "session_manifest",
        {"state": "started", "streams": ["btcusdt@trade"], "started_at": f"{DAY}T10:00:00+00:00"},
        received_at=f"{DAY}T10:00:00+00:00", session_id="active-session",
    )
    store.close()
    with pytest.raises(DailyAuditError, match="unsealed_sessions"):
        build_daily_manifest(tmp_path, DAY, now=NOW)


def test_daily_manifest_refuses_current_or_future_day(tmp_path):
    with pytest.raises(DailyAuditError, match="completed_utc_days"):
        build_daily_manifest(tmp_path, "2026-11-20", now=NOW)


def test_archive_must_be_outside_raw_data_root(tmp_path):
    data = tmp_path / "raw"
    _session(data)
    with pytest.raises(DailyAuditError, match="separate_from_data_root"):
        seal_daily_manifest(data, data / "archive", DAY)
