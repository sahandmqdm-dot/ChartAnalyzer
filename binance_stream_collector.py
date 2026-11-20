"""Local-first, read-only Binance Spot + USD-M Futures event capture.

Exact WebSocket frames are append-only persisted before JSON parsing. Typed events
are derived records; raw frame retention allows independent re-parsing. This module
does not place orders and invalidates order books after any sequence gap.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import shutil
import time
import urllib.parse
import urllib.request
import uuid
from collections import deque
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
        received_value = received_at or utc_now()
        try:
            received_dt = datetime.fromisoformat(received_value)
        except (TypeError, ValueError) as exc:
            raise ValueError("received_at must be an ISO-8601 timestamp") from exc
        if received_dt.tzinfo is None:
            raise ValueError("received_at must include a timezone")
        received = received_dt.astimezone(UTC).isoformat()
        day = received[:10]
        directory = self.root / market / stream
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{day}.jsonl"
        previous = self._heads.get(path)
        if previous is None:
            previous = self._last_hash(path)
            if path.exists():
                valid, existing_count, _ = self.verify_file(path)
                if not valid:
                    raise CollectorError(
                        f"refusing to append to corrupt partition: {path.name}"
                    )
                self._counts[path] = existing_count
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
        valid, _, head = HashChainJSONLStore.verify_file(path)
        if not valid:
            raise CollectorError(f"refusing to extend corrupt partition: {path.name}")
        return head

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
                    if not isinstance(record, dict):
                        return False, count, "unreadable_or_malformed"
                    digest = record.pop("event_hash")
                    canonical = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                    expected = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
                    if digest != expected or record.get("previous_hash") != previous:
                        return False, count, "hash_chain_mismatch"
                    previous = digest
                    count += 1
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
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
                 connector: Callable[..., Any] | None = None,
                 snapshot_fetch: Callable[[MarketSpec], Any] | None = None,
                 max_buffered_depth_events: int = 10000,
                 health_interval_events: int = 1000) -> None:
        if reconnect_min <= 0 or reconnect_max < reconnect_min:
            raise ValueError("invalid reconnect backoff")
        if health_interval_events < 1:
            raise ValueError("health_interval_events must be >= 1")
        self.store = store or HashChainJSONLStore(root)
        self.proxy = proxy
        self.reconnect_min = reconnect_min
        self.reconnect_max = reconnect_max
        self.connect_timeout = connect_timeout
        self.connector = connector
        self.snapshot_fetch = snapshot_fetch
        self.max_buffered_depth_events = max_buffered_depth_events
        self.health_interval_events = health_interval_events
        self._active_websockets: dict[str, Any] = {}

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
        reconnect_count = 0
        streams = "/".join(spec.streams)
        uri = f"{spec.ws_url}?{urllib.parse.urlencode({'streams': streams})}"
        while not stop.is_set():
            session_id = None
            session_started = None
            stream_counts: dict[str, int] = {}
            raw_frame_count = 0
            typed_event_count = 0
            collector_error_count = 0
            persistence_failure_count = 0
            latency_samples: deque[float] = deque(maxlen=512)
            try:
                async with self._connect(uri) as websocket:
                    session_id = uuid.uuid4().hex
                    session_started = utc_now()
                    self.store.append(spec.market, "session_manifest",
                                      {"state": "started", "endpoint": spec.ws_url,
                                       "streams": list(spec.streams),
                                       "started_at": session_started,
                                       "reconnect_count": reconnect_count},
                                      received_at=session_started, session_id=session_id)
                    self.store.append(spec.market, "connection",
                                      {"state": "connected", "endpoint": spec.ws_url},
                                      session_id=session_id)
                    delay = self.reconnect_min
                    from binance_orderbook import AsyncOrderBookCapture

                    capture = AsyncOrderBookCapture(
                        spec, self.store,
                        snapshot_fetch=(lambda: self.snapshot_fetch(spec))
                        if self.snapshot_fetch is not None else None,
                        max_buffered_events=self.max_buffered_depth_events,
                        session_id=session_id,
                    )
                    self._active_websockets[spec.market] = websocket

                    health_counters = {
                        "raw_frame_count": raw_frame_count,
                        "typed_event_count": typed_event_count,
                        "collector_error_count": collector_error_count,
                        "persistence_failure_count": persistence_failure_count,
                    }

                    def emit_health(
                        state: str,
                        _latency_samples=latency_samples,
                        _health_counters=health_counters,
                        _capture=capture,
                        _session_id=session_id,
                        _spec=spec,
                        _reconnect_count=reconnect_count,
                    ) -> None:
                        ordered = sorted(_latency_samples)
                        def percentile(fraction: float) -> float | None:
                            if not ordered:
                                return None
                            index = min(len(ordered) - 1, int((len(ordered) - 1) * fraction))
                            return round(ordered[index], 3)
                        try:
                            disk_usage = shutil.disk_usage(self.store.root)
                            disk = {
                                "total_bytes": disk_usage.total,
                                "used_bytes": disk_usage.used,
                                "free_bytes": disk_usage.free,
                                "used_percent": round(
                                    disk_usage.used / disk_usage.total * 100.0, 2
                                ) if disk_usage.total else None,
                            }
                        except OSError:
                            disk = None
                        if disk and (
                            disk["free_bytes"] < 5 * 1024**3
                            or (disk["used_percent"] is not None and disk["used_percent"] >= 90.0)
                        ):
                            logging.getLogger(__name__).warning(
                                "Low collector disk capacity for %s: %s", _spec.market, disk
                            )
                        self.store.append(
                            _spec.market,
                            "collector_health",
                            {
                                "state": state,
                                "disk": disk,
                                "raw_frame_count": _health_counters["raw_frame_count"],
                                "typed_event_count": _health_counters["typed_event_count"],
                                "collector_error_count": _health_counters["collector_error_count"],
                                "persistence_failure_count": _health_counters["persistence_failure_count"],
                                "reconnect_count": _reconnect_count,
                                "write_pipeline_latency_ms": {
                                    "sample_count": len(ordered),
                                    "p50": percentile(0.50),
                                    "p95": percentile(0.95),
                                    "max": round(ordered[-1], 3) if ordered else None,
                                },
                                "orderbook": _capture.metrics(),
                            },
                            received_at=utc_now(),
                            session_id=_session_id,
                        )

                    emit_health("connected")
                    try:
                        if stop.is_set():
                            await websocket.close()
                        async for message in websocket:
                            if stop.is_set():
                                break
                            received_at = utc_now()
                            stream = "unknown"
                            pipeline_started = time.perf_counter()
                            try:
                                # Preserve the exact provider frame before JSON parsing or
                                # downstream validation. The typed event below is a derived copy.
                                raw_frame = (
                                    {"encoding": "utf-8", "raw_message": message}
                                    if isinstance(message, str)
                                    else {"encoding": "base64", "raw_message": base64.b64encode(message).decode("ascii")}
                                )
                                self.store.append(
                                    spec.market, "raw_frames", raw_frame,
                                    received_at=received_at,
                                    stream_name="combined_websocket_frame",
                                    session_id=session_id,
                                )
                                raw_frame_count += 1
                                health_counters["raw_frame_count"] = raw_frame_count
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
                                typed_event_count += 1
                                health_counters["typed_event_count"] = typed_event_count
                                stream_counts[kind] = stream_counts.get(kind, 0) + 1
                                latency_samples.append((time.perf_counter() - pipeline_started) * 1000.0)
                                if kind == "orderbook_depth":
                                    await capture.ingest(payload)
                            except (ValueError, TypeError) as exc:
                                self.store.append(spec.market, "collector_errors",
                                                  {"error": type(exc).__name__, "message": str(exc)},
                                                  received_at=received_at,
                                                  stream_name=stream,
                                                  session_id=session_id)
                                collector_error_count += 1
                                health_counters["collector_error_count"] = collector_error_count
                                latency_samples.append((time.perf_counter() - pipeline_started) * 1000.0)
                            if raw_frame_count and raw_frame_count % self.health_interval_events == 0:
                                emit_health("running")
                    finally:
                        await capture.close()
                        emit_health("session_ending")
                        self._active_websockets.pop(spec.market, None)
                ended_at = utc_now()
                termination = "operator_stop" if stop.is_set() else "stream_ended"
                self.store.append(spec.market, "session_manifest",
                                  {"state": "ended", "started_at": session_started,
                                   "ended_at": ended_at, "event_count": sum(stream_counts.values()),
                                   "raw_frame_count": raw_frame_count,
                                   "stream_counts": stream_counts,
                                   "collector_error_count": collector_error_count,
                                   "persistence_failure_count": persistence_failure_count,
                                   "reconnect_count": reconnect_count,
                                   "termination": termination},
                                  received_at=ended_at, session_id=session_id)
                self.store.append(spec.market, "connection",
                                  {"state": "stopped" if stop.is_set() else "stream_ended",
                                   "retry_in_seconds": delay},
                                  session_id=session_id)
                if not stop.is_set():
                    reconnect_count += 1
                    try:
                        await asyncio.wait_for(stop.wait(), timeout=delay)
                    except TimeoutError:
                        pass
                    delay = min(self.reconnect_max, delay * 2)
            except asyncio.CancelledError:
                if session_id is not None:
                    interrupted_at = utc_now()
                    self.store.append(spec.market, "session_manifest",
                                      {"state": "interrupted", "started_at": session_started,
                                       "ended_at": interrupted_at,
                                       "event_count": sum(stream_counts.values()),
                                       "raw_frame_count": raw_frame_count,
                                       "stream_counts": stream_counts,
                                       "collector_error_count": collector_error_count,
                                       "persistence_failure_count": persistence_failure_count,
                                       "reconnect_count": reconnect_count,
                                       "termination": "cancelled"},
                                      received_at=interrupted_at, session_id=session_id)
                raise
            except Exception as exc:  # network libraries expose several transport exception types
                if isinstance(exc, (OSError, CollectorError)):
                    persistence_failure_count += 1
                reconnect_count += 1
                logging.getLogger(__name__).exception(
                    "Binance collector session failed for %s (%s)",
                    spec.market,
                    type(exc).__name__,
                )
                interrupted_at = utc_now()
                self.store.append(spec.market, "session_manifest",
                                  {"state": "interrupted", "started_at": session_started,
                                   "ended_at": interrupted_at,
                                   "event_count": sum(stream_counts.values()),
                                   "raw_frame_count": raw_frame_count,
                                   "stream_counts": stream_counts,
                                   "collector_error_count": collector_error_count,
                                   "persistence_failure_count": persistence_failure_count,
                                   "reconnect_count": reconnect_count,
                                   "termination": "transport_error",
                                   "error": type(exc).__name__},
                                  received_at=interrupted_at, session_id=session_id)
                self.store.append(spec.market, "connection",
                                  {"state": "disconnected", "error": type(exc).__name__,
                                   "retry_in_seconds": delay,
                                   "reconnect_count": reconnect_count,
                                   "persistence_failure_count": persistence_failure_count},
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
            if stop_task.done() and not stop_task.cancelled():
                # Close active sockets so their iterators finish and sessions receive
                # a normal operator-stop terminal record instead of a cancellation.
                for market, websocket in list(self._active_websockets.items()):
                    close = getattr(websocket, "close", None)
                    if close is not None:
                        try:
                            await close()
                        except Exception as exc:  # noqa: BLE001  # best-effort transport shutdown
                            logging.getLogger(__name__).warning(
                                "WebSocket close failed for %s: %s", market, type(exc).__name__
                            )
            else:
                for task in tasks:
                    task.cancel()
            stop_task.cancel()
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
    parser.add_argument(
        "--fsync-every-events",
        type=int,
        default=int(os.environ.get("CHARTANALYZER_BINANCE_FSYNC_EVERY_EVENTS", "100")),
        help="fsync each partition after this many appended records (1 = every record)",
    )
    parser.add_argument(
        "--fsync-each-event",
        action="store_true",
        help="fsync every record; strongest durability, potentially lower throughput",
    )
    parser.add_argument(
        "--health-every-events",
        type=int,
        default=int(os.environ.get("CHARTANALYZER_BINANCE_HEALTH_EVERY_EVENTS", "1000")),
        help="append collector health telemetry after this many raw frames",
    )
    args = parser.parse_args()
    store = HashChainJSONLStore(
        args.data_dir,
        fsync_each_event=args.fsync_each_event,
        fsync_every_events=args.fsync_every_events,
    )
    collector = BinanceDualMarketCollector(
        args.data_dir,
        proxy=args.proxy,
        store=store,
        health_interval_events=args.health_every_events,
    )
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
