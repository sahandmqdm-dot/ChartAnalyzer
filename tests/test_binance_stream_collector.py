import json

import pytest

from binance_stream_collector import (
    HashChainJSONLStore,
    MarketSpec,
    OrderBookSequence,
    SequenceGap,
)


def test_market_specs_keep_spot_and_usdm_endpoints_separate():
    spot = MarketSpec.spot("BTCUSDT")
    futures = MarketSpec.usdm("BTCUSDT")
    assert spot.market == "spot"
    assert futures.market == "usdm"
    assert spot.rest_url != futures.rest_url
    assert "btcusdt@depth@100ms" in spot.streams
    assert "btcusdt@forceOrder" in futures.streams


@pytest.mark.parametrize("symbol", ["", "BTC/USDT", "BTC-USDT", "BTC USDT"])
def test_market_spec_rejects_noncanonical_symbols(symbol):
    with pytest.raises(ValueError):
        MarketSpec.spot(symbol)


def test_hash_chain_store_appends_and_verifies(tmp_path):
    store = HashChainJSONLStore(tmp_path, fsync_each_event=False)
    first = store.append("spot", "trades", {"t": 10}, received_at="2026-11-20T10:00:00+00:00")
    second = store.append("spot", "trades", {"t": 11}, received_at="2026-11-20T10:00:01+00:00")
    assert second["previous_hash"] == first["event_hash"]
    path = tmp_path / "spot" / "trades" / "2026-11-20.jsonl"
    valid, count, head = store.verify_file(path)
    assert valid and count == 2 and head == second["event_hash"]


def test_hash_chain_detects_payload_tampering(tmp_path):
    store = HashChainJSONLStore(tmp_path, fsync_each_event=False)
    store.append("usdm", "orderbook_depth", {"U": 1, "u": 2},
                 received_at="2026-11-20T10:00:00+00:00")
    path = tmp_path / "usdm" / "orderbook_depth" / "2026-11-20.jsonl"
    record = json.loads(path.read_text(encoding="utf-8"))
    record["payload"]["u"] = 99
    path.write_text(json.dumps(record) + "\\n", encoding="utf-8")
    valid, count, error = store.verify_file(path)
    assert not valid
    assert count == 0
    assert error == "hash_chain_mismatch"


def test_orderbook_sequence_bootstrap_and_contiguous_updates():
    book = OrderBookSequence()
    with pytest.raises(SequenceGap, match="snapshot_required"):
        book.apply_diff({"U": 101, "u": 102})
    book.install_snapshot(100)
    assert book.apply_diff({"U": 99, "u": 101})
    assert book.valid
    assert book.last_update_id == 101
    assert not book.apply_diff({"U": 100, "u": 101})


def test_orderbook_sequence_gap_invalidates_book():
    book = OrderBookSequence()
    book.install_snapshot(100)
    with pytest.raises(SequenceGap, match="depth_gap"):
        book.apply_diff({"U": 102, "u": 104})
    assert not book.valid
