import asyncio
import json

import pytest

from binance_orderbook import DepthSnapshot
from binance_stream_collector import (
    BinanceDualMarketCollector,
    CollectorError,
    HashChainJSONLStore,
    MarketSpec,
    OrderBookSequence,
    SequenceGap,
    extract_event_metadata,
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


def test_hash_chain_verifier_rejects_non_object_jsonl_records(tmp_path):
    path = tmp_path / "malformed.jsonl"
    path.write_text("[]\n", encoding="utf-8")
    valid, count, error = HashChainJSONLStore.verify_file(path)
    assert valid is False
    assert count == 0
    assert error == "unreadable_or_malformed"


def test_hash_chain_detects_payload_tampering(tmp_path):
    store = HashChainJSONLStore(tmp_path, fsync_each_event=False)
    store.append("usdm", "orderbook_depth", {"U": 1, "u": 2},
                 received_at="2026-11-20T10:00:00+00:00")
    path = tmp_path / "usdm" / "orderbook_depth" / "2026-11-20.jsonl"
    record = json.loads(path.read_text(encoding="utf-8"))
    record["payload"]["u"] = 99
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    valid, count, error = store.verify_file(path)
    assert not valid
    assert count == 0
    assert error == "hash_chain_mismatch"


def test_hash_chain_store_refuses_to_extend_a_corrupt_existing_partition(tmp_path):
    path = tmp_path / "spot" / "trades" / "2026-11-20.jsonl"
    store = HashChainJSONLStore(tmp_path, fsync_each_event=False)
    store.append("spot", "trades", {"t": 1}, received_at="2026-11-20T10:00:00+00:00")
    store.close()
    record = json.loads(path.read_text(encoding="utf-8"))
    record["payload"]["t"] = 999
    path.write_text(json.dumps(record) + "\\n", encoding="utf-8")

    restarted = HashChainJSONLStore(tmp_path, fsync_each_event=False)
    with pytest.raises(CollectorError, match="corrupt partition"):
        restarted.append("spot", "trades", {"t": 2}, received_at="2026-11-20T10:00:01+00:00")
    restarted.close()


def test_store_normalizes_received_timestamp_to_utc(tmp_path):
    store = HashChainJSONLStore(tmp_path, fsync_each_event=False)
    record = store.append(
        "spot", "trades", {"t": 1},
        received_at="2026-11-20T13:30:00+03:30",
    )
    assert record["received_at"] == "2026-11-20T10:00:00+00:00"
    assert (tmp_path / "spot" / "trades" / "2026-11-20.jsonl").exists()
    store.close()


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



def test_event_metadata_preserves_exchange_time_stream_and_native_sequence():
    metadata = extract_event_metadata(
        "btcusdt@depth@100ms",
        {"E": 1795176000000, "U": 120, "u": 125, "s": "BTCUSDT"},
    )
    assert metadata["stream_name"] == "btcusdt@depth@100ms"
    assert metadata["event_time"] == "2026-11-20T12:00:00+00:00"
    assert metadata["trade_time"] is None
    assert metadata["sequence"] == {"U": 120, "u": 125}


def test_trade_metadata_uses_trade_time_when_event_time_is_absent():
    metadata = extract_event_metadata(
        "btcusdt@trade", {"T": 1795176000000, "t": 9876, "a": 4321}
    )
    assert metadata["event_time"] == "2026-11-20T12:00:00+00:00"
    assert metadata["trade_time"] == "2026-11-20T12:00:00+00:00"
    assert metadata["sequence"] == {"t": 9876, "a": 4321}


def test_hash_chain_persists_provenance_fields(tmp_path):
    store = HashChainJSONLStore(tmp_path, fsync_each_event=False)
    record = store.append(
        "usdm", "orderbook_depth", {"U": 100, "u": 102},
        received_at="2026-11-20T12:00:01+00:00",
        event_time="2026-11-20T12:00:00+00:00",
        trade_time=None,
        stream_name="btcusdt@depth@100ms",
        sequence={"U": 100, "u": 102},
        session_id="session-1",
    )
    path = tmp_path / "usdm" / "orderbook_depth" / "2026-11-20.jsonl"
    valid, count, head = store.verify_file(path)
    assert valid and count == 1 and head == record["event_hash"]
    assert record["event_time"] == "2026-11-20T12:00:00+00:00"
    assert record["trade_time"] is None
    assert record["stream_name"] == "btcusdt@depth@100ms"
    assert record["sequence"] == {"U": 100, "u": 102}
    assert record["session_id"] == "session-1"



def test_collector_run_gracefully_closes_both_markets_on_operator_stop(tmp_path):
    async def scenario():
        stop = asyncio.Event()
        sockets = []

        class IdleWebSocket:
            def __init__(self):
                self.closed = asyncio.Event()
                sockets.append(self)

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, traceback):
                return False

            def __aiter__(self):
                async def messages():
                    await self.closed.wait()
                    if False:
                        yield ""
                return messages()

            async def close(self):
                self.closed.set()

        store = HashChainJSONLStore(tmp_path, fsync_each_event=False)
        collector = BinanceDualMarketCollector(
            tmp_path,
            store=store,
            connector=lambda uri, **options: IdleWebSocket(),
        )
        task = asyncio.create_task(collector.run(stop, symbol="BTCUSDT"))
        for _ in range(200):
            if len(sockets) == 2:
                break
            await asyncio.sleep(0.001)
        assert len(sockets) == 2
        stop.set()
        await asyncio.wait_for(task, timeout=2)

        records = [
            json.loads(line)
            for path in tmp_path.rglob("*.jsonl")
            for line in path.read_text(encoding="utf-8").splitlines()
        ]
        terminals = [
            r for r in records
            if r["stream"] == "session_manifest" and r["payload"].get("state") == "ended"
        ]
        assert len(terminals) == 2
        assert {r["market"] for r in terminals} == {"spot", "usdm"}
        assert all(r["payload"]["termination"] == "operator_stop" for r in terminals)

    asyncio.run(scenario())


def test_collector_reconnects_into_a_new_auditable_session(tmp_path):
    async def scenario():
        stop = asyncio.Event()
        connections = []

        class OneTradeWebSocket:
            def __init__(self, should_stop):
                self.should_stop = should_stop

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, traceback):
                return False

            def __aiter__(self):
                async def messages():
                    yield json.dumps({
                        "stream": "btcusdt@trade",
                        "data": {
                            "e": "trade", "E": 1795176000000,
                            "T": 1795176000000, "s": "BTCUSDT",
                            "t": 1, "p": "100", "q": "1", "m": False,
                        },
                    })
                    if self.should_stop:
                        stop.set()

                return messages()

        sockets = [OneTradeWebSocket(False), OneTradeWebSocket(True)]

        def connector(uri, **options):
            socket = sockets.pop(0)
            connections.append(socket)
            return socket

        store = HashChainJSONLStore(tmp_path, fsync_each_event=False)
        collector = BinanceDualMarketCollector(
            tmp_path,
            store=store,
            connector=connector,
            reconnect_min=0.001,
            reconnect_max=0.002,
        )
        await asyncio.wait_for(
            collector.run_market(MarketSpec.spot("BTCUSDT"), stop),
            timeout=2,
        )
        store.close()

        records = [
            json.loads(line)
            for path in tmp_path.rglob("*.jsonl")
            for line in path.read_text(encoding="utf-8").splitlines()
        ]
        trades = [r for r in records if r["stream"] == "trades"]
        terminals = [
            r for r in records
            if r["stream"] == "session_manifest" and r["payload"].get("state") == "ended"
        ]
        assert len(connections) == 2
        assert len(trades) == 2
        assert len({r["session_id"] for r in trades}) == 2
        assert len(terminals) == 2
        assert {r["payload"]["termination"] for r in terminals} == {
            "stream_ended", "operator_stop",
        }

    asyncio.run(scenario())


def test_live_collector_persists_raw_depth_and_recovers_l2_book(tmp_path):
    stop = asyncio.Event()

    class FakeWebSocket:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        def __aiter__(self):
            async def messages():
                yield json.dumps({
                    "stream": "btcusdt@depth@100ms",
                    "data": {
                        "e": "depthUpdate", "E": 1795176000000, "s": "BTCUSDT",
                        "U": 101, "u": 102, "b": [["100.5", "3"]], "a": [],
                    },
                })
                await asyncio.sleep(0.02)
                yield json.dumps({
                    "stream": "btcusdt@depth@100ms",
                    "data": {
                        "e": "depthUpdate", "E": 1795176000100, "s": "BTCUSDT",
                        "U": 103, "u": 103, "b": [], "a": [],
                    },
                })
                stop.set()

            return messages()

    store = HashChainJSONLStore(tmp_path, fsync_each_event=False)
    collector = BinanceDualMarketCollector(
        tmp_path,
        store=store,
        connector=lambda uri, **options: FakeWebSocket(),
        snapshot_fetch=lambda spec: DepthSnapshot(
            last_update_id=100,
            bids=((100.0, 2.0), (99.0, 3.0)),
            asks=((101.0, 1.0), (102.0, 4.0)),
            market="spot",
            symbol="BTCUSDT",
        ),
    )

    async def scenario():
        await collector.run_market(MarketSpec.spot("BTCUSDT"), stop)
        store.close()

    asyncio.run(scenario())

    records = []
    for path in tmp_path.rglob("*.jsonl"):
        records.extend(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines())

    raw_frames = [r for r in records if r["stream"] == "raw_frames"]
    depth = [r for r in records if r["stream"] == "orderbook_depth"]
    snapshots = [r for r in records if r["stream"] == "orderbook_snapshots"]
    states = [r for r in records if r["stream"] == "orderbook_state"]
    assert len(raw_frames) == 2
    assert all(r["payload"]["encoding"] == "utf-8" for r in raw_frames)
    assert all(r["payload"]["raw_message"].startswith("{") and '"stream"' in r["payload"]["raw_message"] for r in raw_frames)
    assert len(depth) == 2
    assert len(snapshots) == 1
    assert any(r["payload"]["state"] == "synchronized" for r in states)
    session_ids = {r["session_id"] for r in depth + snapshots + states}
    assert len(session_ids) == 1
    assert all(r["event_time"] for r in depth)
    terminal = [
        r for r in records
        if r["stream"] == "session_manifest" and r["payload"].get("state") == "ended"
    ]
    assert len(terminal) == 1
    assert terminal[0]["payload"]["termination"] == "operator_stop"
