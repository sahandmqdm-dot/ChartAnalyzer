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

The live collector now connects the WebSocket depth stream to the snapshot/diff synchronizer and persists both raw events and reconstructed-book lifecycle evidence.

- `binance_orderbook.py` provides the public REST snapshot fetcher, bounded in-memory bid/ask map, buffered diff synchronization, sequence-gap rejection, automatic snapshot recovery with bounded retry/backoff, and top-of-book/depth-imbalance metrics. The local book is unreadable until a buffered diff bridges the snapshot sequence.
- `binance_stream_collector.py` captures Spot and USD-M independently. It records raw `@trade` events (tick-by-tick), `@depth@100ms` updates and USD-M `@forceOrder` events before downstream interpretation. Event time, trade time, receive time, full stream name, native sequence/trade identifiers and session ID are retained.
- Session manifests and connection transitions are recorded. Graceful stream completion records an ended session; cancellation or transport failure records an interrupted session. A stopped/interrupted session must never be silently treated as uninterrupted market coverage.
- `binance_pit_vault.py` creates content-addressed session manifests, verifies raw partition hash chains and file digests, and deterministically replays snapshots plus depth diffs. Replay output reports sequence gaps and final book validity.
- Point-in-time availability is defined by local `received_at`, not exchange `event_time`. An exchange timestamp does not prove that the strategy had received the event at that time. The vault reports missing/invalid event timestamps and event timestamps later than local receipt.
- The hash chain and content-addressed manifests are tamper-evident, not administrator-proof immutable storage, signed attestations or a substitute for independently controlled backups. The collector is designed for one writer per market/stream/day partition; do not run multiple collector processes against the same data directory.
- Historical L2 order-book deltas cannot be reconstructed after a disconnect. Snapshot recovery restores a current valid book, but the missing interval remains a recorded data gap. Historical trade backfill is endpoint- and retention-limited.
- Liquidation stream events are provider-published events, not a guaranteed complete record of every liquidation.

### Seal, verify and replay a closed session

Find the session ID in the raw `session_manifest` JSONL records, then run:

```powershell
python -m binance_pit_vault --data-dir $env:CHARTANALYZER_DATA_DIR manifest --market spot --session-id "<SESSION_ID>"
python -m binance_pit_vault --data-dir $env:CHARTANALYZER_DATA_DIR verify --manifest "<PATH_TO_MANIFEST_JSON>"
python -m binance_pit_vault --data-dir $env:CHARTANALYZER_DATA_DIR replay --market spot --session-id "<SESSION_ID>"
```

Use `--market usdm` for the Futures stream. Active sessions cannot be sealed or replayed because their source partitions are still changing. Interrupted sessions can be replayed for diagnostics, but the report labels them `incomplete_capture`; integrity success alone does not mean complete coverage or usable research data.

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
python -m pytest tests/test_binance_stream_collector.py tests/test_binance_orderbook.py tests/test_binance_pit_vault.py -q
```


## Cloud archival

The collector is not tied to a workstation path. CHARTANALYZER_DATA_DIR can point to a local directory or a persistent volume on a cloud VM/container. Optional S3-compatible archival is documented in docs/CLOUD_READY_DATA_ARCHITECTURE.md. It uploads only closed UTC-day partitions after hash-chain verification; it never deletes the local source and never merges Spot with USD-M.
