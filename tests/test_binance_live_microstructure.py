# ruff: noqa: I001  # Flat-layout import ordering differs across Ruff versions.
from datetime import UTC, datetime, timedelta

import pytest

from binance_live_microstructure import (
    LiveSnapshotError,
    build_active_session_snapshot,
    find_latest_active_session,
    persist_snapshot_content_addressed,
    verify_snapshot_artifact,
    verify_snapshot_source_prefixes,
    write_snapshot,
)
from binance_stream_collector import HashChainJSONLStore


BASE = datetime(2020, 1, 1, 12, 0, tzinfo=UTC)
SESSION = "active-pit-snapshot"


def _stamp(seconds):
    return (BASE + timedelta(seconds=seconds)).isoformat()


def _active_session(root):
    store = HashChainJSONLStore(root, fsync_each_event=False)
    store.append(
        "spot", "session_manifest",
        {"state": "started", "streams": ["btcusdt@trade"], "started_at": _stamp(0)},
        received_at=_stamp(0), session_id=SESSION,
    )
    store.append(
        "spot", "raw_frames",
        {"encoding": "utf-8", "raw_message": '{"stream":"btcusdt@trade","data":{"t":1}}'},
        received_at=_stamp(1), session_id=SESSION,
    )
    store.append(
        "spot", "trades",
        {"s": "BTCUSDT", "p": "90000", "q": "0.2", "m": False, "T": 1795176001000, "t": 1},
        received_at=_stamp(1), event_time=_stamp(0.5),
        stream_name="btcusdt@trade", sequence={"t": 1}, session_id=SESSION,
    )
    # This event is present in the copied partition but was not available at
    # the decision time. Exchange event time alone must not make it eligible.
    store.append(
        "spot", "raw_frames",
        {"encoding": "utf-8", "raw_message": '{"stream":"btcusdt@trade","data":{"t":2}}'},
        received_at=_stamp(6), session_id=SESSION,
    )
    store.append(
        "spot", "trades",
        {"s": "BTCUSDT", "p": "90010", "q": "0.3", "m": True, "T": 1795176002000, "t": 2},
        received_at=_stamp(6), event_time=_stamp(2),
        stream_name="btcusdt@trade", sequence={"t": 2}, session_id=SESSION,
    )
    store.close()


def test_active_session_snapshot_is_verified_and_receive_time_causal(tmp_path):
    _active_session(tmp_path)
    result = build_active_session_snapshot(
        tmp_path, "spot", SESSION, _stamp(3), window_seconds=(5,)
    )
    feature = result["snapshots"][0]

    assert result["snapshot_mode"] == "verified_active_partition_copy"
    assert result["session_closed"] is False
    assert result["session_complete"] is False
    assert feature["point_in_time_valid"] is True
    assert feature["source_event_watermark"] == _stamp(1)
    assert feature["processed_event_counts"]["trades"] == 1
    assert feature["windows"]["5"]["trade_count"] == 1
    assert feature["windows"]["5"]["taker_buy_volume"] == "0.2"
    assert len(result["source_snapshot"]["source_files"]) == 3
    assert result["dataset_hash"]


def test_active_snapshot_writes_atomic_artifact(tmp_path):
    _active_session(tmp_path / "raw")
    result = build_active_session_snapshot(
        tmp_path / "raw", "spot", SESSION, _stamp(3), window_seconds=(5,)
    )
    output = write_snapshot(result, tmp_path / "research" / "snapshot.json")
    assert output.is_file()
    assert output.read_text(encoding="utf-8").endswith("\\n")



def test_latest_active_session_is_selected_only_when_started_by_cutoff(tmp_path):
    _active_session(tmp_path)
    selected = find_latest_active_session(
        tmp_path, "spot", "BTCUSDT", decision_time=_stamp(3)
    )
    assert selected == SESSION


def test_snapshot_artifact_is_content_addressed_and_never_overwritten(tmp_path):
    _active_session(tmp_path / "raw")
    snapshot = build_active_session_snapshot(
        tmp_path / "raw", "spot", SESSION, _stamp(3), window_seconds=(5,)
    )
    first = persist_snapshot_content_addressed(snapshot, tmp_path / "raw")
    second = persist_snapshot_content_addressed(snapshot, tmp_path / "raw")
    assert first == second
    artifact = tmp_path / "raw" / first["artifact_path"]
    assert artifact.is_file()
    assert len(first["artifact_sha256"]) == 64
    audit = verify_snapshot_artifact(
        tmp_path / "raw", first["artifact_path"], first["artifact_sha256"]
    )
    assert audit["valid"] is True
    assert audit["partitions_verified"] == 3



def test_snapshot_source_prefix_remains_verifiable_after_later_appends(tmp_path):
    _active_session(tmp_path)
    snapshot = build_active_session_snapshot(
        tmp_path, "spot", SESSION, _stamp(3), window_seconds=(5,)
    )
    before = verify_snapshot_source_prefixes(tmp_path, snapshot)
    assert before["valid"] is True

    later = HashChainJSONLStore(tmp_path, fsync_each_event=False)
    later_session = "later-session"
    later.append(
        "spot", "session_manifest",
        {"state": "started", "streams": ["btcusdt@trade"], "started_at": _stamp(10)},
        received_at=_stamp(10), session_id=later_session,
    )
    later.append(
        "spot", "raw_frames",
        {"encoding": "utf-8", "raw_message": '{"stream":"btcusdt@trade","data":{"t":3}}'},
        received_at=_stamp(11), session_id=later_session,
    )
    later.append(
        "spot", "trades",
        {"s": "BTCUSDT", "p": "90020", "q": "0.4", "m": False, "T": 1577880011000, "t": 3},
        received_at=_stamp(11), event_time=_stamp(10.5),
        stream_name="btcusdt@trade", sequence={"t": 3}, session_id=later_session,
    )
    later.close()

    after = verify_snapshot_source_prefixes(tmp_path, snapshot)
    assert after["valid"] is True
    assert after["partitions_verified"] == before["partitions_verified"]


def test_snapshot_source_prefix_verification_detects_prior_record_tampering(tmp_path):
    _active_session(tmp_path)
    snapshot = build_active_session_snapshot(
        tmp_path, "spot", SESSION, _stamp(3), window_seconds=(5,)
    )
    trade_source = next(
        item for item in snapshot["source_snapshot"]["source_files"]
        if item["path"].endswith("/trades/2020-01-01.jsonl")
    )
    path = tmp_path / trade_source["path"]
    path.write_bytes(path.read_bytes().replace(b"90000", b"90001", 1))
    with pytest.raises(LiveSnapshotError, match="prefix|chain"):
        verify_snapshot_source_prefixes(tmp_path, snapshot)
