# Futures Directional Intelligence v1

## Scope

The module `futures_signal_engine.py` creates transparent LONG and SHORT
watch scenarios from completed, chronological OHLCV candles. It is a
research-facing candidate layer, not an order signal or a deployment gate.

## Output semantics

- `candidate`: LONG, SHORT, or NO_DIRECTIONAL_SETUP from a bounded heuristic.
- `scenarios.long` and `scenarios.short`: counterfactual reference entry,
  ATR stop and R-multiple target for both directions.
- `WATCH_ONLY`: scenario visualization only. `actionable` is always false.
- `decision`: always NO_TRADE until a separate futures-specific OOS workflow,
  execution model and deployment authorization exist.
- `directional_score`: heuristic score, not a probability or confidence claim.

## Inputs and integrity

At least 60 bars are required. Each bar must include OHLCV and a timezone-aware
close timestamp (`close_time` or `time`). Timestamps must be strictly
increasing, gap-free within the configured tolerance, not future-dated, and
fresh relative to `as_of`. The caller must supply completed candles only.
No sorting, deduplication, interpolation or synthetic timestamps are performed.

## Features

EMA20/EMA50 relationship, six-bar price momentum, RSI14 and ATR14 form a
transparent bounded score. Funding and open-interest change are freshness-
checked context only. OI change is never interpreted as long/short positioning.
Funding is not used as a directional vote.

## Important limitations

The directional engine does not consume L2 order-book imbalance, tick trades,
CVD, liquidation streams, mark/index premium, funding accrual over the holding
horizon, contract-specific maintenance tiers, or executable bid/ask prices.
It does not claim calibrated probabilities, positive expectancy, OOS validity,
or profitability. Stop/target references do not model intrabar ordering.

## Dashboard data integration

The local dashboard endpoint `GET /api/futures/scenarios?symbol=BTCUSDT&tf=1h&limit=240`
uses public Binance USD-M endpoints and returns:

- completed USD-M OHLCV candles with provider provenance;
- current indicative funding, mark price, index price and next funding time
  from the premium-index endpoint;
- recent five-minute open-interest history and a same-provider/unit baseline
  change when a sufficiently old baseline is available;
- one REST order-book snapshot with best bid/ask, spread and top-20 quantity
  imbalance;
- a bounded recent aggregate-trade sample with taker-buy/sell quantities.

The order-book REST result is a single snapshot, not a synchronized depth-diff
stream. The aggregate-trade result is a bounded sample, not complete tick-by-tick
history. Both are displayed as context and are deliberately excluded from the
directional score until their timestamp alignment, sampling bias and predictive
value have been validated. No live orders are placed.

In the dashboard, the two ATR scenarios are explicitly labeled WATCH ONLY.
The heuristic candidate is not a calibrated probability. The decision remains
NO TRADE and deployment authorization remains false.


## Live raw event capture and L2 reconstruction

Run the local-first public stream collector from the repository root:

```powershell
python binance_stream_collector.py --data-dir .\data\binance_raw --symbol BTCUSDT
```

The collector captures Spot and USD-M depth diffs, trades and USD-M force-order
liquidation messages. Each raw payload is hash-chained before downstream parsing,
with provider event time, trade time when supplied, local receive time, full
combined-stream name, native sequence/trade identifiers and connection session ID.

For each market connection, the collector buffers depth diffs while fetching a
REST depth snapshot asynchronously. It reconstructs the local L2 book only after
a buffered diff bridges the snapshot update ID. A sequence gap invalidates the
book and starts snapshot recovery; reconnects start a new synchronization cycle.
Snapshot payloads and synchronization state transitions are also persisted in
the hash-chained store. A bounded buffer prevents unbounded memory growth during
extended snapshot/network failures.

A valid in-memory book is a point-in-time reconstruction for that process/session,
not an exchange-certified archive. The raw stream and snapshot files are the
replay evidence. The collector does not yet perform a complete independent
offline replay audit or use reconstructed book features in the directional
candidate score. Do not treat a displayed or reconstructed imbalance as a
validated predictive feature.


## PIT evidence manifest and offline replay

After a collector session has ended, obtain its session ID from the session
manifest JSONL partition, then create a content-addressed evidence manifest:

```powershell
python binance_pit_vault.py --data-dir .\data\binance_raw manifest --market usdm --session-id <SESSION_ID>
python binance_pit_vault.py --data-dir .\data\binance_raw verify --manifest <MANIFEST_PATH>
python binance_pit_vault.py --data-dir .\data\binance_raw replay --market usdm --session-id <SESSION_ID>
```

The manifest binds the session to SHA-256 digests, byte counts and verified
per-partition hash-chain heads. Offline replay applies the captured depth
snapshots and raw diffs in receive-time order, checks the sequence bridge and
returns a deterministic replay hash plus final validity, gap and event counts.
A replay that cannot establish a contiguous sequence remains invalid.

The vault currently stores manifests and references the original local evidence
partitions. It does not copy data to independent immutable storage, sign
manifests with an external key, or guarantee protection against an attacker who
can rewrite both the data and all local hashes. For independent audit, preserve
the manifest and raw partitions in separately controlled storage. The 42-day
prospective evaluation must not start until live collection, reconnect recovery,
hash verification and replay checks have been exercised in the intended
deployment environment.
