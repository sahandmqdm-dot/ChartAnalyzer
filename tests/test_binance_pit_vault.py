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
    replay_historical_market_orders,
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
    with pytest.raises(PITVaultError, match="non_monotonic_received_at"):
        build_session_manifest(tmp_path, "spot", SESSION)


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



def _execution_fee_schedule():
    return {
        "venue": "binance", "account_tier": "standard", "market": "spot",
        "maker_rate": 0.001, "taker_rate": 0.001,
        "source": "captured official fee schedule",
        "effective_at": "2026-11-20T00:00:00+00:00",
    }


def _historical_order(**overrides):
    result = {
        "order_id": "historical-order-001",
        "decision_at": "2026-11-20T12:00:00.300000+00:00",
        "side": "buy", "quantity": 2.0, "impact_bps": 0.0,
        "impact_bps_source": "synthetic test model",
    }
    result.update(overrides)
    return result


def _run_historical_execution(root, orders, **kwargs):
    return replay_historical_market_orders(
        root, "spot", SESSION, orders,
        fee_schedule=_execution_fee_schedule(), **kwargs,
    )


def test_historical_execution_replay_uses_only_book_received_by_decision(tmp_path):
    create_session(tmp_path)
    report = _run_historical_execution(tmp_path, [_historical_order()])
    result = report["orders"][0]
    assert result["status"] == "filled"
    assert result["execution_estimate_kind"] == "displayed_depth_snapshot_estimate"
    assert result["market_sweep"]["book_provenance"]["last_update_id"] == 102
    assert result["execution"]["vwap"] == pytest.approx(101.5)
    assert result["cost_model_complete"] is True
    assert report["summary"]["orders"] == 1
    assert len(report["replay_hash"]) == 64


def test_historical_execution_blocks_decision_before_snapshot_arrives(tmp_path):
    create_session(tmp_path)
    report = _run_historical_execution(
        tmp_path, [_historical_order(
            decision_at="2026-11-20T12:00:00.150000+00:00"
        )],
    )
    assert report["orders"][0]["status"] == "blocked"
    assert report["orders"][0]["blocked_reason"] == "no_sequence_valid_book_at_decision"


def test_historical_execution_blocks_stale_order_book(tmp_path):
    create_session(tmp_path)
    report = _run_historical_execution(
        tmp_path, [_historical_order()], max_book_age_ms=50
    )
    assert report["orders"][0]["status"] == "blocked"
    assert report["orders"][0]["blocked_reason"] == "stale_order_book"


def test_historical_execution_partial_depth_and_missing_funding_are_explicit(tmp_path):
    create_session(tmp_path)
    report = _run_historical_execution(
        tmp_path, [_historical_order(quantity=8, funding_events=2)]
    )
    result = report["orders"][0]
    assert result["status"] == "partial"
    assert result["execution"]["filled_quantity"] == pytest.approx(5)
    assert result["execution"]["unfilled_quantity"] == pytest.approx(3)
    assert result["cost_model_complete"] is False
    assert "funding_rate" in result["execution"]["cost_breakdown"]["missing_inputs"]


def test_historical_execution_rejects_incomplete_capture_and_sequence_gap(tmp_path):
    create_session(tmp_path, terminal_state="interrupted")
    with pytest.raises(PITVaultError, match="closed, complete"):
        _run_historical_execution(tmp_path, [_historical_order()])

    other_root = tmp_path / "gap"
    other_root.mkdir()
    create_session(other_root, first_update=105, final_update=106)
    with pytest.raises(PITVaultError, match="integrity or sequence"):
        _run_historical_execution(other_root, [_historical_order()])


@pytest.mark.parametrize("overrides", [
    {"quantity": True},
    {"quantity": -1},
    {"impact_bps": -1},
    {"impact_bps_source": ""},
    {"side": "hold"},
])
def test_historical_execution_rejects_invalid_order_inputs(tmp_path, overrides):
    create_session(tmp_path)
    with pytest.raises((ValueError, TypeError)):
        _run_historical_execution(tmp_path, [_historical_order(**overrides)])


def test_historical_execution_rejects_duplicate_ids_and_future_decisions(tmp_path):
    create_session(tmp_path)
    with pytest.raises(ValueError, match="unique"):
        _run_historical_execution(
            tmp_path, [_historical_order(), _historical_order()]
        )
    with pytest.raises(ValueError, match="outside"):
        _run_historical_execution(
            tmp_path, [_historical_order(
                decision_at="2026-11-20T12:02:00+00:00"
            )]
        )


@pytest.mark.parametrize("overrides", [
    {"order_id": 7},
    {"side": 7},
    {"funding_source": 7},
])
def test_historical_execution_rejects_untyped_identifiers_and_provenance(tmp_path, overrides):
    create_session(tmp_path)
    with pytest.raises((ValueError, TypeError)):
        _run_historical_execution(tmp_path, [_historical_order(**overrides)])
