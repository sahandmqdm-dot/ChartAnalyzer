"""Local-first, read-only Binance Spot + USD-M Futures event capture.

Raw WebSocket messages are durably appended before parsing. This module does not
place orders and deliberately marks order books invalid after any sequence gap.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:
    from websockets.asyncio.client import connect
except ImportError:  # pragma: no cover - exercised by runtime configuration
    connect = None


class CollectorError(RuntimeError):
    """Base collector error."""


class SequenceGap(CollectorError):
    """Depth stream no longer follows the local snapshot sequence."""


@dataclass(frozen=True, slots=True)
class MarketSpec:
    market: str
    symbol: str
    ws_url: str
    rest_url: str
    streams: tuple[str, ...]

    @classmethod
    def spot(cls, symbol: str = "btcusdt") -> MarketSpec:
        symbol = _symbol(symbol)
        return cls("spot", symbol, "wss://stream.binance.com:9443/stream",
                   "https://api.binance.com", (f"{symbol}@depth@100ms", f"{symbol}@trade"))

    @classmethod
    def usdm(cls, symbol: str = "btcusdt") -> MarketSpec:
        symbol = _symbol(symbol)
        return cls("usdm", symbol, "wss://fstream.binance.com/stream",
                   "https://fapi.binance.com",
                   (f"{symbol}@depth@100ms", f"{symbol}@trade", f"{symbol}@forceOrder"))


def _symbol(value: str) -> str:
    normalized = str(value).strip().lower()
    if not normalized or not normalized.isalnum() or len(normalized) > 30:
        raise ValueError("symbol must be an alphanumeric exchange symbol")
    return normalized


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class HashChainJSONLStore:
    """Append-only daily JSONL partitions with per-partition SHA-256 chaining."""

    def __init__(self, root: str | Path, *, fsync_each_event: bool = False,
                 fsync_every_events: int = 100) -> None:
        if fsync_every_events < 1:
            raise ValueError("fsync_every_events must be >= 1")
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.fsync_each_event = fsync_each_event
        self.fsync_every_events = fsync_every_events
        self._heads: dict[Path, str] = {}
        self._handles: dict[Path, Any] = {}
        self._counts: dict[Path, int] = {}

    def append(self, market: str, stream: str, payload: dict[str, Any],
               *, received_at: str | None = None, event_time: str | None = None,
               trade_time: str | None = None, stream_name: str | None = None,
               sequence: dict[str, int] | None = None,
               session_id: str | None = None) -> dict[str, Any]:
        received = received_at or utc_now()
        day = received[:10]
        directory = self.root / market / stream
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{day}.jsonl"
        previous = self._heads.get(path)
        if previous is None:
            previous = self._last_hash(path)
        body = {
            "schema": "binance_raw_event_v1",
            "market": market,
            "stream": stream,
            "received_at": received,
            "event_time": event_time,
            "trade_time": trade_time,
            "stream_name": stream_name,
            "sequence": sequence,
            "session_id": session_id,
            "payload": payload,
            "previous_hash": previous,
        }
        canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        record = {**body, "event_hash": digest}
        handle = self._handles.get(path)
        if handle is None or handle.closed:
            handle = path.open("a", encoding="utf-8", newline="\n")
            self._handles[path] = handle
        handle.write(json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n")
        handle.flush()  # process-crash visibility; fsync below bounds power-loss exposure
        count = self._counts.get(path, 0) + 1
        self._counts[path] = count
        if self.fsync_each_event or count % self.fsync_every_events == 0:
            os.fsync(handle.fileno())
        self._heads[path] = digest
        return record

    def close(self) -> None:
        """Flush and fsync every open partition before a clean shutdown."""
        for handle in self._handles.values():
            if not handle.closed:
                handle.flush()
                os.fsync(handle.fileno())
                handle.close()
        self._handles.clear()

    @staticmethod
    def _last_hash(path: Path) -> str | None:
        if not path.exists():
            return None
        last = None
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    last = json.loads(line).get("event_hash")
        return last

    @staticmethod
    def verify_file(path: str | Path) -> tuple[bool, int, str | None]:
        previous = None
        count = 0
        try:
            with Path(path).open("r", encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    record = json.loads(line)
                    digest = record.pop("event_hash")
                    canonical = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                    expected = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
                    if digest != expected or record.get("previous_hash") != previous:
                        return False, count, "hash_chain_mismatch"
                    previous = digest
                    count += 1
        except (OSError, ValueError, KeyError, TypeError):
            return False, count, "unreadable_or_malformed"
        return True, count, previous


class OrderBookSequence:
    """Minimal Binance diff-depth sequence guard; never claims a stale book is valid."""

    def __init__(self) -> None:
        self.last_update_id: int | None = None
        self.valid = False

    def install_snapshot(self, last_update_id: int) -> None:
        if isinstance(last_update_id, bool) or int(last_update_id) < 0:
            raise ValueError("last_update_id must be a non-negative integer")
        self.last_update_id = int(last_update_id)
        self.valid = False

    def apply_diff(self, event: dict[str, Any]) -> bool:
        if self.last_update_id is None:
            raise SequenceGap("snapshot_required")
        first = int(event["U"])
        final = int(event["u"])
        if final <= self.last_update_id:
            return False
        if first > self.last_update_id + 1:
            self.valid = False
            raise SequenceGap(f"depth_gap:{self.last_update_id}->{first}-{final}")
        if first <= self.last_update_id + 1 <= final:
            self.last_update_id = final
            self.valid = True
            return True
        self.valid = False
        raise SequenceGap("depth_sequence_invalid")


def extract_event_metadata(stream: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Preserve provider event time and native identifiers without guessing timestamps."""
    def to_iso(value: Any) -> str | None:
        if value is None:
            return None
        try:
            return datetime.fromtimestamp(int(value) / 1000, UTC).isoformat()
        except (TypeError, ValueError, OverflowError, OSError):
            return None

    event_time = to_iso(payload.get("E"))
    trade_time = to_iso(payload.get("T"))
    if event_time is None:
        event_time = trade_time

    sequence = {}
    for key in ("U", "u", "t", "a", "f", "l"):
        value = payload.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            sequence[key] = value
    return {"event_time": event_time, "trade_time": trade_time,
            "stream_name": stream or "unknown", "sequence": sequence or None}


class BinanceDualMarketCollector:
    """Capture combined public streams for Spot and USD-M Futures independently."""

    def __init__(self, root: str | Path, *, proxy: str | None = None,
                 reconnect_min: float = 1.0, reconnect_max: float = 60.0,
                 connect_timeout: float = 15.0,
                 store: HashChainJSONLStore | None = None,
                 connector: Callable[..., Any] | None = None) -> None:
        if reconnect_min <= 0 or reconnect_max < reconnect_min:
            raise ValueError("invalid reconnect backoff")
        self.store = store or HashChainJSONLStore(root)
        self.proxy = proxy
        self.reconnect_min = reconnect_min
        self.reconnect_max = reconnect_max
        self.connect_timeout = connect_timeout
        self.connector = connector

    def _connect(self, uri: str):
        factory = self.connector or connect
        if factory is None:
            raise CollectorError("Install websockets>=15.0 to run the live collector")
        options = {
            "open_timeout": self.connect_timeout, "ping_interval": 20,
            "ping_timeout": 20, "close_timeout": 10, "max_queue": 4096,
        }
        # Omit proxy when unset so websockets can honor system proxy environment;
        # explicit proxy overrides that discovery when the operator configures one.
        if self.proxy:
            options["proxy"] = self.proxy
        return factory(uri, **options)

    async def run_market(self, spec: MarketSpec, stop: asyncio.Event) -> None:
        delay = self.reconnect_min
        streams = "/".join(spec.streams)
        uri = f"{spec.ws_url}?{urllib.parse.urlencode({'streams': streams})}"
        while not stop.is_set():
            session_id = None
            try:
                async with self._connect(uri) as websocket:
                    session_id = uuid.uuid4().hex
                    session_started = utc_now()
                    stream_counts: dict[str, int] = {}
                    self.store.append(spec.market, "session_manifest",
                                      {"state": "started", "endpoint": spec.ws_url,
                                       "streams": list(spec.streams),
                                       "started_at": session_started},
                                      received_at=session_started, session_id=session_id)
                    self.store.append(spec.market, "connection",
                                      {"state": "connected", "endpoint": spec.ws_url},
                                      session_id=session_id)
                    delay = self.reconnect_min
                    async for message in websocket:
                        if stop.is_set():
                            break
                        received_at = utc_now()
                        try:
                            envelope = json.loads(message)
                            stream = str(envelope.get("stream", "unknown"))
                            payload = envelope.get("data", envelope)
                            if not isinstance(payload, dict):
                                raise TypeError("websocket data must be an object")
                            # Persist the provider payload first; downstream parsing is separate.
                            kind = _stream_kind(stream)
                            metadata = extract_event_metadata(stream, payload)
                            self.store.append(spec.market, kind, payload,
                                              received_at=received_at,
                                              event_time=metadata["event_time"],
                                              trade_time=metadata["trade_time"],
                                              stream_name=metadata["stream_name"],
                                              sequence=metadata["sequence"],
                                              session_id=session_id)
                            stream_counts[kind] = stream_counts.get(kind, 0) + 1
                        except (ValueError, TypeError) as exc:
                            self.store.append(spec.market, "collector_errors",
                                              {"error": type(exc).__name__, "message": str(exc)},
                                              received_at=received_at)
                if not stop.is_set():
                    ended_at = utc_now()
                    self.store.append(spec.market, "session_manifest",
                                      {"state": "ended", "started_at": session_started,
                                       "ended_at": ended_at, "event_count": sum(stream_counts.values()),
                                       "stream_counts": stream_counts,
                                       "termination": "stream_ended"},
                                      received_at=ended_at, session_id=session_id)
                    self.store.append(spec.market, "connection",
                                      {"state": "stream_ended", "retry_in_seconds": delay},
                                      session_id=session_id)
                    try:
                        await asyncio.wait_for(stop.wait(), timeout=delay)
                    except TimeoutError:
                        pass
                    delay = min(self.reconnect_max, delay * 2)
            except asyncio.CancelledError:
                if session_id is not None:
                    self.store.append(spec.market, "session_manifest",
                                      {"state": "interrupted", "ended_at": utc_now(),
                                       "termination": "cancelled"},
                                      session_id=session_id)
                raise
            except Exception as exc:  # noqa: BLE001  # network libraries expose several transport exception types
                self.store.append(spec.market, "session_manifest",
                                  {"state": "interrupted", "ended_at": utc_now(),
                                   "termination": "transport_error",
                                   "error": type(exc).__name__},
                                  session_id=session_id)
                self.store.append(spec.market, "connection",
                                  {"state": "disconnected", "error": type(exc).__name__,
                                   "retry_in_seconds": delay},
                                  session_id=locals().get("session_id"))
                try:
                    await asyncio.wait_for(stop.wait(), timeout=delay)
                except TimeoutError:
                    pass
                delay = min(self.reconnect_max, delay * 2)

    async def run(self, stop: asyncio.Event, *, symbol: str = "btcusdt") -> None:
        specs = (MarketSpec.spot(symbol), MarketSpec.usdm(symbol))
        tasks = [asyncio.create_task(self.run_market(spec, stop), name=f"binance-{spec.market}")
                 for spec in specs]
        stop_task = asyncio.create_task(stop.wait(), name="collector-stop")
        try:
            done, _ = await asyncio.wait([*tasks, stop_task],
                                         return_when=asyncio.FIRST_COMPLETED)
            if stop_task not in done:
                failed = next((task for task in done if task is not stop_task), None)
                if failed is not None:
                    error = failed.exception()
                    if error is not None:
                        raise error
                    raise CollectorError("market collection task stopped unexpectedly")
        finally:
            stop_task.cancel()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, stop_task, return_exceptions=True)
            self.store.close()


def _stream_kind(stream: str) -> str:
    if "@depth" in stream:
        return "orderbook_depth"
    if "@trade" in stream:
        return "trades"
    if "@forceOrder" in stream:
        return "liquidations"
    return "unknown"


__all__ = [
    "BinanceDualMarketCollector", "CollectorError", "HashChainJSONLStore",
    "MarketSpec", "OrderBookSequence", "SequenceGap", "extract_event_metadata",
]


async def _run_cli() -> None:
    import argparse
    import signal

    parser = argparse.ArgumentParser(description="Capture public Binance Spot and USD-M streams locally.")
    parser.add_argument("--data-dir", default=os.environ.get("CHARTANALYZER_DATA_DIR", "./data/binance_raw"))
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--proxy", default=os.environ.get("CHARTANALYZER_BINANCE_PROXY"))
    args = parser.parse_args()
    collector = BinanceDualMarketCollector(args.data_dir, proxy=args.proxy)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # Windows event-loop compatibility
            pass
    print(f"Binance local collector started: {args.symbol.upper()} Spot + USD-M; Ctrl+C to stop")
    await collector.run(stop, symbol=args.symbol)


if __name__ == "__main__":
    asyncio.run(_run_cli())
