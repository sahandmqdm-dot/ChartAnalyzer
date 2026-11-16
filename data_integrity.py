"""OHLCV integrity checks. No repair or synthetic candle filling is performed."""
import math
import numpy as np


def audit_ohlcv(df, timeframe_seconds: int, check_stale: bool = True):
    if timeframe_seconds <= 0:
        raise ValueError("timeframe_seconds must be positive")
    required = ("time", "open", "high", "low", "close", "volume")
    missing = [c for c in required if c not in df.columns]
    if missing:
        return {"state": "BLOCKED", "valid": False, "errors": [f"missing columns: {', '.join(missing)}"]}
    a = {k: np.asarray(df[k], dtype=float) for k in required}
    errors, warnings = [], []
    ingest = getattr(df, "attrs", {}).get("ingest_audit", {}) or {}
    if ingest.get("invalid_ohlc_rows", 0) or ingest.get("non_finite_rows", 0) or ingest.get("negative_volume_rows", 0):
        errors.append("source contained invalid rows that were discarded during normalization")
    if ingest.get("duplicate_timestamps", 0):
        warnings.append(f"{ingest['duplicate_timestamps']} duplicate timestamp row(s) removed during normalization")
    if ingest.get("out_of_order_timestamps", 0):
        warnings.append(f"{ingest['out_of_order_timestamps']} out-of-order timestamp step(s) sorted during normalization")
    n = len(df)
    if n < 60:
        errors.append(f"insufficient history: {n} candles (minimum 60)")
    finite = np.ones(n, dtype=bool)
    for k, v in a.items():
        finite &= np.isfinite(v)
    if not finite.all():
        errors.append(f"non-finite OHLCV rows: {int((~finite).sum())}")
    positive = np.ones(n, dtype=bool)
    for k in ("open", "high", "low", "close"):
        positive &= a[k] > 0
    if not positive.all():
        errors.append(f"non-positive price rows: {int((~positive).sum())}")
    if (a["volume"] < 0).any():
        errors.append(f"negative volume rows: {int((a['volume'] < 0).sum())}")
    shape = ((a["high"] >= a["low"]) &
             (a["high"] >= np.maximum(a["open"], a["close"])) &
             (a["low"] <= np.minimum(a["open"], a["close"])))
    if not shape.all():
        errors.append(f"impossible OHLC rows: {int((~shape).sum())}")
    t = a["time"]
    if n > 1:
        dt = np.diff(t)
        if (dt <= 0).any():
            errors.append("timestamps are not strictly increasing and unique")
        gaps = int(np.sum(dt > timeframe_seconds * 1.5))
        if gaps:
            warnings.append(f"{gaps} timestamp gap(s) larger than 1.5 candles")
        offgrid = int(np.sum(np.abs(dt - timeframe_seconds) > timeframe_seconds * .01))
        if offgrid:
            warnings.append(f"{offgrid} interval(s) differ from the nominal timeframe")
    else:
        gaps = 0
        offgrid = 0
    if n:
        age = max(0.0, __import__("time").time() - (float(t[-1]) + timeframe_seconds)) if check_stale else None
        if check_stale and age > timeframe_seconds * 2:
            warnings.append("latest candle appears stale relative to wall-clock time")
    else:
        age = None
        errors.append("empty candle set")
    return {
        "state": "PASS" if not errors else "BLOCKED",
        "valid": not errors,
        "candles": n,
        "ingest_audit": ingest,
        "timeframe_seconds": int(timeframe_seconds),
        "first_timestamp": float(t[0]) if n else None,
        "last_timestamp": float(t[-1]) if n else None,
        "latest_candle_age_seconds": round(age, 1) if age is not None else None,
        "gap_count": gaps,
        "off_grid_interval_count": offgrid,
        "errors": errors,
        "warnings": warnings,
        "policy": "No interpolation, forward-fill, or synthetic candle generation.",
    }
