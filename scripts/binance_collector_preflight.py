"""Live connectivity and host-capacity preflight for the public Binance collector.

This is a readiness probe only. It does not write collector records or start a holdout.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import platform
import shutil
import sys
import time
from pathlib import Path
from typing import Any

try:
    from websockets.asyncio.client import connect
    from websockets.exceptions import WebSocketException
except ImportError as exc:  # pragma: no cover - runtime diagnostic
    raise SystemExit("Missing dependency: install requirements.txt (websockets>=15,<16)") from exc


MARKETS = {
    "spot": "wss://stream.binance.com:9443/ws/btcusdt@trade",
    "usdm": "wss://fstream.binance.com/ws/btcusdt@trade",
}


async def probe_market(name: str, uri: str, timeout: float) -> dict[str, Any]:
    started = time.perf_counter()
    async with connect(uri, open_timeout=timeout, close_timeout=5, ping_interval=20) as ws:
        raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
    payload = json.loads(raw)
    if not isinstance(payload, dict) or payload.get("e") != "trade":
        raise RuntimeError(f"{name}: unexpected trade payload")
    if payload.get("s") != "BTCUSDT":
        raise RuntimeError(f"{name}: unexpected symbol {payload.get('s')!r}")
    return {
        "market": name,
        "status": "PASS",
        "symbol": payload["s"],
        "event_time_ms": payload.get("T"),
        "receive_probe_latency_ms": round((time.perf_counter() - started) * 1000, 2),
        "raw_frame_bytes": len(raw) if isinstance(raw, (str, bytes)) else None,
    }


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("./data/binance_raw"))
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--min-free-gib", type=float, default=10.0)
    args = parser.parse_args()
    if args.timeout <= 0 or args.min_free_gib < 0:
        parser.error("timeout must be > 0 and min-free-gib must be >= 0")

    args.data_dir.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(args.data_dir)
    free_gib = usage.free / (1024**3)
    results: dict[str, Any] = {
        "host": platform.node(),
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "data_dir": str(args.data_dir.resolve()),
        "disk": {
            "total_gib": round(usage.total / (1024**3), 2),
            "free_gib": round(free_gib, 2),
            "minimum_free_gib": args.min_free_gib,
            "status": "PASS" if free_gib >= args.min_free_gib else "FAIL",
        },
        "markets": [],
    }
    failures = free_gib < args.min_free_gib
    for name, uri in MARKETS.items():
        try:
            result = await probe_market(name, uri, args.timeout)
        except (OSError, TimeoutError, ValueError, RuntimeError, WebSocketException) as exc:
            result = {"market": name, "status": "FAIL", "error": type(exc).__name__, "detail": str(exc)}
            failures = True
        results["markets"].append(result)
    results["overall"] = "FAIL" if failures else "PASS"
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
