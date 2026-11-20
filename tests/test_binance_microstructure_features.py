import pytest

from binance_microstructure_features import (
    PITFeatureError,
    build_pit_microstructure_features,
)
from binance_stream_collector import HashChainJSONLStore


SESSION = "pit-features-session"
BASE = "2026-11-20T12:00:00+00:00"


def _received(seconds):
    from datetime import datetime, timedelta

    return (datetime.fromisoformat(BASE) + timedelta(seconds=seconds)).isoformat()


def _create_session(root, *, terminal_state="ended"):
    store = HashChainJSONLStore(root, fsync_each_event=False)
    store.append(
        "spot", "session_manifest",
        {
            "state": "started",
            "streams": [
                "btcusdt@depth@100ms",
                "btcusdt@trade",
                "btcusdt@forceOrder",
            ],
            "started_at": BASE,
        },
        received_at=_received(0), session_id=SESSION,
    )
    store.append(
        "spot", "orderbook_depth",
        {
            "U": 101, "u": 101,
            "b": [["100", "3"]],
            "a": [],
        },
        received_at=_received(0.1),
        event_time=_received(0.09),
        stream_name="btcusdt@depth@100ms",
        sequence={"U": 101, "u": 101},
        session_id=SESSION,
    )
    store.append(
        "spot", "orderbook_snapshots",
        {
            "symbol": "BTCUSDT",
            "lastUpdateId": 100,
            "bids": [["99", "2"], ["98", "3"]],
            "asks": [["101", "2"], ["102", "4"]],
        },
        received_at=_received(0.2), session_id=SESSION,
    )
    store.append(
        "spot", "trades",
        {"p": "100", "q": "2", "m": False, "T": 1795176001000, "t": 1},
        received_at=_received(1), event_time=_received(1),
        stream_name="btcusdt@trade", sequence={"t": 1}, session_id=SESSION,
    )
    store.append(
        "spot", "liquidations",
        {"e": "forceOrder", "o": {"s": "BTCUSDT", "S": "SELL", "z": "0.5", "ap": "100.5"}},
        received_at=_received(2.5), event_time=_received(2.4),
        stream_name="btcusdt@forceOrder", session_id=SESSION,
    )
    store.append(
        "spot", "trades",
        {"s": "ETHUSDT", "p": "900", "q": "9", "m": False, "t": 99},
        received_at=_received(2.7), event_time=_received(2.6),
        stream_name="btcusdt@trade", session_id=SESSION,
    )
    store.append(
        "spot", "liquidations",
        {"e": "forceOrder", "o": {"s": "ETHUSDT", "S": "BUY", "z": "9", "ap": "900"}},
        received_at=_received(2.8), event_time=_received(2.7),
        stream_name="btcusdt@forceOrder", session_id=SESSION,
    )
    store.append(
        "spot", "trades",
        {"p": "101", "q": "1", "m": True, "T": 1795176002000, "t": 2},
        received_at=_received(3), event_time=_received(2),
        stream_name="btcusdt@trade", sequence={"t": 2}, session_id=SESSION,
    )
    store.append(
        "spot", "orderbook_depth",
        {"U": 102, "u": 102, "b": [], "a": [["100.5", "4"]]},
        received_at=_received(4), event_time=_received(3.9),
        stream_name="btcusdt@depth@100ms",
        sequence={"U": 102, "u": 102}, session_id=SESSION,
    )
    # This trade's exchange timestamp precedes the decision, but it was not
    # received until after the decision and must not leak into that snapshot.
    store.append(
        "spot", "trades",
        {"p": "102", "q": "5", "m": False, "T": 1795176004000, "t": 3},
        received_at=_received(6), event_time=_received(4),
        stream_name="btcusdt@trade", sequence={"t": 3}, session_id=SESSION,
    )
    store.append(
        "spot", "liquidations",
        {"e": "forceOrder", "o": {"s": "BTCUSDT", "S": "BUY", "z": "2", "ap": "103"}},
        received_at=_received(7), event_time=_received(5),
        stream_name="btcusdt@forceOrder", session_id=SESSION,
    )
    store.append(
        "spot", "session_manifest",
        {
            "state": terminal_state,
            "ended_at": _received(10),
            "termination": "stream_ended" if terminal_state == "ended" else "transport_error",
        },
        received_at=_received(10), session_id=SESSION,
    )
    store.close()


def test_features_use_receive_time_and_keep_orderbook_state_point_in_time(tmp_path):
    _create_session(tmp_path)
    result = build_pit_microstructure_features(
        tmp_path, "spot", SESSION,
        [_received(3), _received(5)],
        window_seconds=(1, 5, 60),
    )
    first, second = result["snapshots"]

    assert first["point_in_time_valid"] is True
    assert first["available_at"] == _received(3)
    assert first["availability_field"] == "received_at"
    assert first["book"]["valid"] is True
    assert first["book"]["best_bid"] == "100"
    assert first["book"]["best_ask"] == "101"
    assert first["windows"]["5"]["trade_count"] == 2
    assert first["windows"]["5"]["taker_buy_volume"] == "2"
    assert first["windows"]["5"]["taker_sell_volume"] == "1"
    assert first["windows"]["5"]["net_taker_volume"] == "1"
    assert first["windows"]["1"]["trade_count"] == 1
    assert first["windows"]["1"]["taker_sell_volume"] == "1"
    assert first["windows"]["5"]["long_liquidation_notional"] == "50.25"

    assert second["book"]["best_ask"] == "100.5"
    assert second["windows"]["5"]["trade_count"] == 2
    assert second["windows"]["5"]["taker_buy_volume"] == "2"
    assert second["processed_event_counts"]["trades"] == 2
    assert result["dataset_hash"]


def test_l2_bootstrap_buffer_is_bounded_and_reports_overflow(tmp_path):
    store = HashChainJSONLStore(tmp_path, fsync_each_event=False)
    store.append(
        "spot", "session_manifest",
        {"state": "started", "streams": ["btcusdt@depth@100ms"], "started_at": BASE},
        received_at=_received(0), session_id=SESSION,
    )
    for update_id in (101, 102):
        store.append(
            "spot", "orderbook_depth",
            {"U": update_id, "u": update_id, "b": [], "a": []},
            received_at=_received(update_id - 100),
            stream_name="btcusdt@depth@100ms",
            sequence={"U": update_id, "u": update_id},
            session_id=SESSION,
        )
    store.append(
        "spot", "orderbook_snapshots",
        {
            "symbol": "BTCUSDT", "lastUpdateId": 101,
            "bids": [["100", "2"]], "asks": [["101", "2"]],
        },
        received_at=_received(3), session_id=SESSION,
    )
    store.append(
        "spot", "session_manifest",
        {"state": "ended", "ended_at": _received(4), "termination": "operator_stop"},
        received_at=_received(4), session_id=SESSION,
    )
    store.close()

    result = build_pit_microstructure_features(
        tmp_path, "spot", SESSION, [_received(4)],
        max_buffered_depth_events=1,
    )
    feature = result["snapshots"][0]
    assert feature["book_buffer_overflows"] == 1
    assert feature["book"]["valid"] is True
    assert feature["book"]["last_update_id"] == 102


def test_l2_event_with_wrong_symbol_never_makes_book_valid(tmp_path):
    store = HashChainJSONLStore(tmp_path, fsync_each_event=False)
    store.append(
        "spot", "session_manifest",
        {"state": "started", "streams": ["btcusdt@depth@100ms"], "started_at": BASE},
        received_at=_received(0), session_id=SESSION,
    )
    store.append(
        "spot", "orderbook_depth",
        {"s": "ETHUSDT", "U": 101, "u": 101, "b": [], "a": []},
        received_at=_received(1), stream_name="btcusdt@depth@100ms",
        session_id=SESSION,
    )
    store.append(
        "spot", "orderbook_snapshots",
        {
            "symbol": "BTCUSDT", "lastUpdateId": 100,
            "bids": [["100", "1"]], "asks": [["101", "1"]],
        },
        received_at=_received(2), session_id=SESSION,
    )
    store.append(
        "spot", "session_manifest",
        {"state": "ended", "ended_at": _received(3), "termination": "operator_stop"},
        received_at=_received(3), session_id=SESSION,
    )
    store.close()

    result = build_pit_microstructure_features(
        tmp_path, "spot", SESSION, [_received(3)]
    )
    assert result["snapshots"][0]["book"]["valid"] is False
    assert result["snapshots"][0]["book_sequence_gaps"] == 1


def test_feature_builder_rejects_incomplete_capture_by_default(tmp_path):
    _create_session(tmp_path, terminal_state="interrupted")
    with pytest.raises(PITFeatureError, match="incomplete_capture"):
        build_pit_microstructure_features(
            tmp_path, "spot", SESSION, [_received(5)]
        )


def test_feature_builder_rejects_out_of_order_decision_times(tmp_path):
    _create_session(tmp_path)
    with pytest.raises(ValueError, match="ascending"):
        build_pit_microstructure_features(
            tmp_path, "spot", SESSION, [_received(5), _received(3)]
        )


def test_feature_builder_rejects_decisions_outside_capture_window(tmp_path):
    _create_session(tmp_path)
    with pytest.raises(PITFeatureError, match="outside_capture"):
        build_pit_microstructure_features(
            tmp_path, "spot", SESSION, ["2026-11-20T11:59:59+00:00"]
        )
