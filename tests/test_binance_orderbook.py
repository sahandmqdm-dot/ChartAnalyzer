import pytest

from binance_orderbook import (
    DepthSnapshot,
    LocalOrderBook,
    OrderBookError,
    synchronize_buffered,
)
from binance_stream_collector import SequenceGap


def snapshot(last_id=100):
    return DepthSnapshot(
        last_update_id=last_id,
        bids=((100.0, 2.0), (99.0, 3.0)),
        asks=((101.0, 1.0), (102.0, 4.0)),
        market="spot",
        symbol="BTCUSDT",
    )


def test_buffered_diffs_bridge_snapshot_and_build_valid_top():
    book = LocalOrderBook("spot", "BTCUSDT")
    count = synchronize_buffered(book, snapshot(), [
        {"U": 98, "u": 100, "b": [], "a": []},
        {"U": 101, "u": 102, "b": [["100", "0"], ["100.5", "5"]],
         "a": [["101", "2"]]},
    ])
    assert count == 1
    top = book.top(2)
    assert top["bid"] == 100.5
    assert top["ask"] == 101.0
    assert top["spread"] == 0.5
    assert top["valid"] is True


def test_book_rejects_reads_before_sequence_bridge():
    book = LocalOrderBook("spot", "BTCUSDT")
    book.install_snapshot(snapshot())
    with pytest.raises(OrderBookError, match="not sequence-valid"):
        book.top()


def test_buffered_sequence_gap_fails_closed():
    book = LocalOrderBook("spot", "BTCUSDT")
    with pytest.raises(SequenceGap):
        synchronize_buffered(book, snapshot(), [
            {"U": 103, "u": 104, "b": [], "a": []}
        ])
    assert not book.valid
    with pytest.raises(OrderBookError):
        book.top()


def test_snapshot_identity_cannot_cross_market():
    book = LocalOrderBook("usdm", "BTCUSDT")
    with pytest.raises(OrderBookError, match="identity mismatch"):
        book.install_snapshot(snapshot())


def test_zero_quantity_removes_level_and_imbalance_is_bounded():
    book = LocalOrderBook("spot", "BTCUSDT")
    synchronize_buffered(book, snapshot(), [
        {"U": 101, "u": 101, "b": [["99", "0"]], "a": []}
    ])
    top = book.top()
    assert 99.0 not in dict(top["bids"])
    assert -1.0 <= top["depth_imbalance"] <= 1.0
