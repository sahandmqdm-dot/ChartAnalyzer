import asyncio
import time

import pytest

from binance_orderbook import (
    AsyncOrderBookCapture,
    DepthSnapshot,
    LocalOrderBook,
    OrderBookError,
    synchronize_buffered,
)
from binance_stream_collector import HashChainJSONLStore, MarketSpec, SequenceGap


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



def test_async_capture_bootstraps_snapshot_and_applies_live_diffs(tmp_path):
    async def scenario():
        store = HashChainJSONLStore(tmp_path, fsync_each_event=False)
        capture = AsyncOrderBookCapture(
            MarketSpec.spot("BTCUSDT"), store,
            snapshot_fetch=lambda: snapshot(100),
        )
        first = await capture.ingest(
            {"U": 101, "u": 102, "b": [["100", "4"]], "a": []}
        )
        assert first == "buffering"
        for _ in range(100):
            if capture._snapshot_task is not None and capture._snapshot_task.done():
                break
            await asyncio.sleep(0.001)
        await capture.ingest(
            {"U": 103, "u": 103, "b": [["100.5", "3"]], "a": []}
        )
        assert capture.book.valid
        assert capture.book.sequence.last_update_id == 103
        assert capture.book.top()["bid"] == 100.5
        await capture.close()
        store.close()

    asyncio.run(scenario())


def test_async_capture_recovers_after_sequence_gap_with_fresh_snapshot(tmp_path):
    async def scenario():
        store = HashChainJSONLStore(tmp_path, fsync_each_event=False)
        snapshots = iter([snapshot(100), snapshot(102)])
        capture = AsyncOrderBookCapture(
            MarketSpec.spot("BTCUSDT"), store,
            snapshot_fetch=lambda: next(snapshots),
            retry_min=0.001,
            retry_max=0.01,
        )
        await capture.ingest(
            {"U": 103, "u": 104, "b": [], "a": []}
        )
        for _ in range(100):
            if capture._snapshot_task is not None and capture._snapshot_task.done():
                break
            await asyncio.sleep(0.001)
        await capture.ingest(
            {"U": 105, "u": 106, "b": [], "a": []}
        )
        assert not capture.book.valid
        retry_at = capture._retry_at
        assert retry_at > time.monotonic()
        deadline = time.monotonic() + 2.0
        while time.monotonic() < retry_at:
            remaining = retry_at - time.monotonic()
            if time.monotonic() >= deadline:
                raise AssertionError("order-book retry gate did not expire within 2 seconds")
            await asyncio.sleep(min(0.01, max(0.001, remaining)))
        await capture.ingest(
            {"U": 107, "u": 108, "b": [], "a": []}
        )
        for _ in range(100):
            if capture._snapshot_task is not None and capture._snapshot_task.done():
                break
            await asyncio.sleep(0.001)
        await capture.ingest(
            {"U": 109, "u": 110, "b": [], "a": []}
        )
        assert capture.book.valid
        assert capture.book.sequence.last_update_id == 110
        await capture.close()
        store.close()

    asyncio.run(scenario())
