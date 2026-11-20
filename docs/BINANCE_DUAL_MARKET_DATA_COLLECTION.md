# Binance Dual-Market Data Collection

## Scope

This branch introduces a local-first, read-only raw event collector for:

- Binance Spot BTCUSDT public `@trade` and `@depth@100ms` streams.
- Binance USD-M perpetual BTCUSDT public `@trade`, `@depth@100ms`, and `@forceOrder` streams.

Spot and USD-M are stored under separate market namespaces. They must never be merged into one order book or one instrument identity. No API key or trading permission is used.

## Local Windows setup

Install the project requirements in the active Python environment:

```powershell
python -m pip install -r requirements.txt
```

Run from the repository root:

```powershell
$env:CHARTANALYZER_DATA_DIR = "F:\\ChartAnalyzerData\\binance_raw"
python -m binance_stream_collector --symbol BTCUSDT
```

If the machine requires an explicitly configured HTTPS/SOCKS proxy supported by the installed websockets runtime, set it deliberately:

```powershell
$env:CHARTANALYZER_BINANCE_PROXY = "http://127.0.0.1:7890"
python -m binance_stream_collector --symbol BTCUSDT
```

Leave the proxy variable unset for the library's default environment proxy behavior. Do not commit proxy credentials or place them in config files. The collector does not attempt to evade geographic or legal access restrictions.

## Durability and recovery behavior

- Each received provider payload is appended to a daily JSONL partition before downstream interpretation.
- Each partition has a SHA-256 hash chain. Verification detects payload edits, deletions in the middle, and broken ordering.
- Each append is flushed to the OS on receipt; fsync is batched every 100 events by default and forced on clean shutdown. Set `fsync_each_event=True` when constructing the store for stricter per-event power-loss durability at a throughput cost. Benchmark write throughput and disk latency on the target machine.
- Spot and USD-M use independent WebSocket sessions and independent reconnect loops with exponential backoff.
- Connection transitions and collector parse errors are written as events.
- Reconnect does not fabricate missing trades or historical depth updates. A collection gap is part of the dataset and must remain visible.
- Proxy/VPN availability is a transport concern, not proof that Binance is reachable. Repeated failures remain visible in the connection event log.

## Current implementation boundary

This first implementation is a durable raw-stream capture foundation, not yet a complete reconstructed market-data product.

- `binance_orderbook.py` now provides a public REST snapshot fetcher, a bounded-by-provider-depth in-memory bid/ask map, buffered diff synchronization, sequence-gap rejection, and top-of-book/depth-imbalance metrics. The caller must buffer WebSocket depth events while fetching the snapshot, then pass that buffer to `synchronize_buffered`. The long-running collector is not yet orchestrating this bootstrap/rebootstrap loop or periodically reconciling snapshots.
- Raw trade events preserve Binance payload fields, including trade IDs, event/trade timestamps, price, quantity and maker-side flag where supplied.
- Historical public trade endpoints can sometimes backfill trade IDs, subject to endpoint retention and API limits. Historical L2 order-book deltas cannot be reconstructed after a disconnect. The collector must record those intervals as unrecoverable depth gaps.
- The next implementation stage must connect the collector's live depth stream to the snapshot/buffer synchronizer, persist explicit depth-gap/rebootstrap lifecycle events, add deterministic replay, schema validation, and a Parquet compaction path. Do not mark an order book valid until snapshot and diff sequence synchronization succeeds.
- Liquidation stream events are sampled/aggregated provider events, not a complete record of every liquidation.

## Integrity and holdout boundary

Raw event files are operationally append-only and tamper-evident, not administrator-proof immutable storage. Daily manifests should be copied to a separately controlled backup target. Raw collection must not be used to tune the frozen prospective model during its holdout period.

The 42-day holdout clock is NOT started by running this collector. Start only after:
1. snapshot/diff reconstruction and replay pass deterministic tests;
2. Spot and USD-M stream schemas and event timestamps are validated;
3. reconnect, proxy failure, process restart, disk-full and sequence-gap tests pass;
4. local storage capacity and sustained write throughput are measured;
5. forecast/outcome ledger and market identity are bound to the exact frozen release;
6. a separate backup/restore drill succeeds.

## Tests

```powershell
python -m pytest tests/test_binance_stream_collector.py -q
```


## Cloud archival

The collector is not tied to a workstation path. CHARTANALYZER_DATA_DIR can point to a local directory or a persistent volume on a cloud VM/container. Optional S3-compatible archival is documented in docs/CLOUD_READY_DATA_ARCHITECTURE.md. It uploads only closed UTC-day partitions after hash-chain verification; it never deletes the local source and never merges Spot with USD-M.
