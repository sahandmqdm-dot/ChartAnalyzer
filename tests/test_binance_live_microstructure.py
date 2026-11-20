from datetime import datetime, timedelta

from binance_live_microstructure import (
    build_active_session_snapshot,
    write_snapshot,
)
from binance_stream_collector import HashChainJSONLStore


BASE = datetime(2026, 11, 20, 12, 0, tzinfo=__import__("datetime").UTC)
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
        "spot", "trades",
        {"s": "BTCUSDT", "p": "90000", "q": "0.2", "m": False, "T": 1795176001000, "t": 1},
        received_at=_stamp(1), event_time=_stamp(0.5),
        stream_name="btcusdt@trade", sequence={"t": 1}, session_id=SESSION,
    )
    # This event is present in the copied partition but was not available at
    # the decision time. Exchange event time alone must not make it eligible.
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
    assert len(result["source_snapshot"]["source_files"]) == 2
    assert result["dataset_hash"]


def test_active_snapshot_writes_atomic_artifact(tmp_path):
    _active_session(tmp_path / "raw")
    result = build_active_session_snapshot(
        tmp_path / "raw", "spot", SESSION, _stamp(3), window_seconds=(5,)
    )
    output = write_snapshot(result, tmp_path / "research" / "snapshot.json")
    assert output.is_file()
    assert output.read_text(encoding="utf-8").endswith("\\n")
