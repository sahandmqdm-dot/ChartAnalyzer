"""Reproducible run identity and report-cohort integrity for ChartAnalyzer.

The same normalized OHLCV window and execution/indicator configuration produce
one stable run_id across the full-window and walk-forward evaluation endpoints.
The evaluation scope is kept separate so in-sample and OOS results cannot be
mistaken for one another.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Any, Dict, Iterable

import numpy as np

APP_VERSION = "7.2.0"


def _json_safe(value: Any):
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in sorted(value.items(), key=lambda x: str(x[0]))}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        x = float(value)
        return x if math.isfinite(x) else str(x)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    return str(value)


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical_digest(value: Any) -> str:
    encoded = json.dumps(_json_safe(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return _digest(encoded)


def config_fingerprint(strategy_config: Dict[str, Any]) -> str:
    """Full fingerprint including code release and normalized strategy settings."""
    return _canonical_digest({"app_version": APP_VERSION, "strategy_config": strategy_config})


def dataset_fingerprint(df, *, source: str, symbol: str, timeframe: str) -> str:
    """Hash the exact normalized OHLCV window, plus its market identity."""
    cols = ("time", "open", "high", "low", "close", "volume")
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise ValueError("cannot fingerprint OHLCV data; missing: " + ", ".join(missing))
    values = np.asarray(df.loc[:, cols], dtype="<f8")
    if values.ndim != 2 or not np.isfinite(values).all():
        raise ValueError("cannot fingerprint non-finite OHLCV data")
    identity = f"{source}|{symbol}|{timeframe}|{len(values)}|".encode("utf-8")
    return _digest(identity + np.ascontiguousarray(values).tobytes())


def build_provenance(
    df, *, source: str, symbol: str, timeframe: str, requested_candles: int,
    market_type: str, strategy_config: Dict[str, Any], data_integrity: Dict[str, Any],
    evaluation_scope: str,
) -> Dict[str, Any]:
    """Build a deterministic experiment identity and explicit sample lineage."""
    dataset_id = dataset_fingerprint(df, source=source, symbol=symbol, timeframe=timeframe)
    config_hash = config_fingerprint(strategy_config)
    config_id = config_hash
    run_id = _digest(f"{APP_VERSION}|{dataset_id}|{config_hash}".encode("utf-8"))[:20]
    time_values = np.asarray(df["time"], dtype=float)
    first_ts = float(time_values[0]) if len(time_values) else None
    last_ts = float(time_values[-1]) if len(time_values) else None

    def iso(ts):
        if ts is None:
            return None
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")

    return {
        "app_version": APP_VERSION,
        "run_id": run_id,
        "dataset_id": dataset_id[:20],
        "config_id": config_id[:20],
        "config_fingerprint": config_hash,
        "source": str(source),
        "symbol": str(symbol),
        "timeframe": str(timeframe),
        "market_type": str(market_type),
        "evaluation_scope": str(evaluation_scope),
        "requested_candles": int(requested_candles),
        "actual_candles": int(len(df)),
        "first_timestamp": first_ts,
        "last_timestamp": last_ts,
        "first_time_utc": iso(first_ts),
        "last_time_utc": iso(last_ts),
        "data_integrity": {
            "state": data_integrity.get("state", "UNKNOWN"),
            "valid": bool(data_integrity.get("valid", False)),
            "gap_count": int(data_integrity.get("gap_count", 0) or 0),
            "warnings": list(data_integrity.get("warnings", []) or []),
            "errors": list(data_integrity.get("errors", []) or []),
        },
        "strategy_config": _json_safe(strategy_config),
        "identity_policy": "Same run_id means identical normalized OHLCV, strategy/indicator settings and execution-cost assumptions; evaluation_scope distinguishes full-window from OOS metrics.",
    }


def audit_research_cohort(trades: Iterable[Dict[str, Any]], *, trade_intelligence=None,
                           exit_path_intelligence=None, trade_failure_intelligence=None,
                           entry_edge_research=None) -> Dict[str, Any]:
    """Cross-check that all OOS diagnostic modules audited the same trade cohort."""
    trade_rows = [t for t in (trades or []) if isinstance(t, dict)]
    expected = len(trade_rows)
    counts = {"trade_log": expected}
    checks = {}

    ti_n = (trade_intelligence or {}).get("trades")
    if ti_n is not None:
        counts["trade_intelligence"] = int(ti_n)
        checks["trade_intelligence_count_matches"] = int(ti_n) == expected

    xp = exit_path_intelligence or {}
    if xp.get("status") not in (None, "unavailable"):
        xp_n = int(xp.get("trades_analyzed", 0)) + int(xp.get("trades_invalid", 0))
        counts["exit_path_total"] = xp_n
        checks["exit_path_count_matches"] = xp_n == expected

    fi = trade_failure_intelligence or {}
    if fi.get("status") not in (None, "unavailable"):
        fi_n = int(fi.get("trades_analyzed", 0)) + int(fi.get("trades_invalid", 0))
        counts["trade_failure_total"] = fi_n
        checks["trade_failure_count_matches"] = fi_n == expected

    ee = entry_edge_research or {}
    if ee.get("status") not in (None, "unavailable"):
        ee_n = int(ee.get("samples", 0)) + int(ee.get("invalid_or_missing", 0))
        counts["entry_edge_total"] = ee_n
        checks["entry_edge_count_matches"] = ee_n == expected

    # The two OHLC forensic engines must identify the same valid global bars.
    xp_rows = [r for r in (xp.get("trades") or []) if isinstance(r, dict)]
    fi_rows = [r for r in (fi.get("trades") or []) if isinstance(r, dict) and r.get("audit_status", "ok") == "ok"]
    if xp_rows and fi_rows:
        xp_keys = [(int(r.get("entry_bar", -1)), int(r.get("exit_bar", -1))) for r in xp_rows]
        fi_keys = [(int(r.get("entry_bar", -1)), int(r.get("exit_bar", -1))) for r in fi_rows]
        checks["forensic_trade_indices_match"] = xp_keys == fi_keys

    mismatch = [name for name, ok in checks.items() if not ok]
    return {
        "status": "PASS" if not mismatch else "MISMATCH",
        "expected_trade_count": expected,
        "module_counts": counts,
        "checks": checks,
        "mismatches": mismatch,
        "policy": "A mismatch is a report-integrity defect, not a strategy-performance result. Investigate before comparing or exporting affected diagnostics.",
    }


__all__ = ["APP_VERSION", "build_provenance", "dataset_fingerprint", "config_fingerprint", "audit_research_cohort"]
