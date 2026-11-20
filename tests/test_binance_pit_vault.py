# ruff: noqa: I001
# Ruff 0.17.6 flags this mixed-case imported symbol block despite explicit grouping.
import json


import pytest

from binance_pit_vault import (
    build_session_manifest,
    iter_session_records,
    PITVaultError,
    persist_session_manifest,
    replay_orderbook_session,
    reference_replay_orderbook_session,
    verify_session_manifest,
)
from binance_stream_collector import HashChainJSONLStore


SESSION = "capture-session-001"
START = "2026-11-20T12:00:00+00:00"


def create_session(root, *, first_update=101, final_update=102, terminal_state="ended"):
    store = HashChainJSONLStore(root, fsync_each_event=False)
    store.append(
        "spot",
        "session_manifest",
        {
            "state": "started",
            "streams": ["btcusdt@depth@100ms", "btcusdt@trade"],
            "started_at": START,
        },
        received_at=START,
        session_id=SESSION,
    )
    store.append(
        "spot",
        "orderbook_depth",
        {
            "U": first_update,
            "u": final_update,
            "b": [["100.5", "3"]],
            "a": [],
        },
        received_at="2026-11-20T12:00:00.100000+00:00",
        stream_name="btcusdt@depth@100ms",
        sequence={"U": first_update, "u": final_update},
        session_id=SESSION,
    )
    store.append(
        "spot",
        "orderbook_snapshots",
        {
            "symbol": "BTCUSDT",
            "lastUpdateId": 100,
            "bids": [[100.0, 2.0], [99.0, 3.0]],
            "asks": [[101.0, 1.0], [102.0, 4.0]],
        },
        received_at="2026-11-20T12:00:00.200000+00:00",
        session_id=SESSION,
    )
    store.append(
        "spot",
        "session_manifest",
        {
            "state": terminal_state,
            "ended_at": "2026-11-20T12:01:00+00:00",
            "termination": "stream_ended" if terminal_state == "ended" else "transport_error",
        },
        received_at="2026-11-20T12:01:00+00:00",
        session_id=SESSION,
    )
    store.close()


def test_manifest_is_deterministic_and_verifies_source_partitions(tmp_path):
    create_session(tmp_path)
    first = build_session_manifest(tmp_path, "spot", SESSION)
    second = build_session_manifest(tmp_path, "spot", SESSION)
    assert first["manifest_hash"] == second["manifest_hash"]
    assert first["symbol"] == "BTCUSDT"
    assert first["total_session_records"] == 4
    assert first["ended_at"] == "2026-11-20T12:01:00+00:00"

    path = persist_session_manifest(tmp_path, "spot", SESSION)
    result = verify_session_manifest(tmp_path, path)
    assert result["valid"] is True
    assert result["manifest_hash"] == first["manifest_hash"]
    assert result["source_files"] == 3


def test_session_record_iterator_merges_partitions_in_receive_time_order(tmp_path):
    create_session(tmp_path)
    manifest = build_session_manifest(tmp_path, "spot", SESSION)
    records = list(iter_session_records(tmp_path, manifest, SESSION))
    received = [record["received_at"] for record in records]
    assert received == sorted(received)
    assert len(records) == manifest["total_session_records"]


def test_session_record_iterator_rejects_non_monotonic_partition_time(tmp_path):
    store = HashChainJSONLStore(tmp_path, fsync_each_event=False)
    store.append(
        "spot", "session_manifest",
        {"state": "started", "streams": ["btcusdt@trade"], "started_at": START},
        received_at=START, session_id=SESSION,
    )
    store.append(
        "spot", "trades", {"p": "100", "q": "1", "m": False},
        received_at="2026-11-20T12:00:03+00:00", session_id=SESSION,
    )
    store.append(
        "spot", "trades", {"p": "101", "q": "1", "m": True},
        received_at="2026-11-20T12:00:02+00:00", session_id=SESSION,
    )
    store.append(
        "spot", "session_manifest",
        {"state": "ended", "ended_at": "2026-11-20T12:00:04+00:00"},
        received_at="2026-11-20T12:00:04+00:00", session_id=SESSION,
    )
    store.close()
    manifest = build_session_manifest(tmp_path, "spot", SESSION)
    with pytest.raises(PITVaultError, match="non_monotonic_received_at"):
        list(iter_session_records(tmp_path, manifest, SESSION))


def test_replay_reconstructs_book_from_snapshot_and_buffered_diffs(tmp_path):
    create_session(tmp_path)
    report = replay_orderbook_session(tmp_path, "spot", SESSION)
    assert report["integrity_valid"] is True
    assert report["final_valid"] is True
    assert report["final_update_id"] == 102
    assert report["applied_diffs"] == 1
    assert report["sequence_gaps"] == 0
    assert len(report["replay_hash"]) == 64
    assert report["reconstruction_agreement"] is True
    assert report["independent_reference"]["engine"] == "decimal_reference_replay_v1"
    reference = reference_replay_orderbook_session(tmp_path, "spot", SESSION)
    assert reference["final_valid"] is True
    assert reference["final_update_id"] == 102
    assert reference["best_bid"] == "100.5"
    assert reference["best_ask"] == "101.0"


def test_replay_fails_closed_when_snapshot_cannot_bridge_depth_sequence(tmp_path):
    create_session(tmp_path, first_update=103, final_update=104)
    report = replay_orderbook_session(tmp_path, "spot", SESSION)
    assert report["final_valid"] is False
    assert report["sequence_gaps"] == 1
    assert report["buffered_events"] == 1
    assert report["reconstruction_agreement"] is True
    reference = reference_replay_orderbook_session(tmp_path, "spot", SESSION)
    assert reference["final_valid"] is False
    assert reference["sequence_gaps"] == 1


def test_manifest_declares_receive_time_as_point_in_time_availability(tmp_path):
    create_session(tmp_path)
    manifest = build_session_manifest(tmp_path, "spot", SESSION)
    assert manifest["availability_contract"]["available_at_field"] == "received_at"
    assert manifest["availability_contract"]["event_time_is_not_availability_time"] is True
    assert manifest["event_time_quality"]["market_events"] == 1
    assert manifest["event_time_quality"]["missing_or_invalid_event_time"] == 1
    assert manifest["session_closed"] is True
    assert manifest["session_complete"] is True


def test_interrupted_capture_is_explicitly_incomplete_not_mislabelled_complete(tmp_path):
    create_session(tmp_path, terminal_state="interrupted")
    manifest = build_session_manifest(tmp_path, "spot", SESSION)
    assert manifest["session_state"] == "interrupted"
    assert manifest["session_complete"] is False
    report = replay_orderbook_session(tmp_path, "spot", SESSION)
    assert report["integrity_valid"] is True
    assert report["data_quality_status"] == "incomplete_capture"
    assert report["session_complete"] is False


def test_active_session_cannot_be_sealed_or_replayed(tmp_path):
    store = HashChainJSONLStore(tmp_path, fsync_each_event=False)
    store.append(
        "spot", "session_manifest",
        {"state": "started", "streams": ["btcusdt@depth@100ms"], "started_at": START},
        received_at=START, session_id=SESSION,
    )
    store.close()
    manifest = build_session_manifest(tmp_path, "spot", SESSION)
    assert manifest["session_state"] == "active"
    with pytest.raises(PITVaultError, match="active capture session"):
        persist_session_manifest(tmp_path, "spot", SESSION)
    with pytest.raises(PITVaultError, match="active capture session"):
        replay_orderbook_session(tmp_path, "spot", SESSION)


def test_manifest_detects_source_file_tampering(tmp_path):
    create_session(tmp_path)
    manifest = build_session_manifest(tmp_path, "spot", SESSION)
    depth_file = tmp_path / "spot" / "orderbook_depth" / "2026-11-20.jsonl"
    record = json.loads(depth_file.read_text(encoding="utf-8"))
    record["payload"]["u"] = 999
    depth_file.write_text(json.dumps(record) + "\n", encoding="utf-8")
    with pytest.raises(PITVaultError, match="source_file_digest_mismatch"):
        verify_session_manifest(tmp_path, manifest)
