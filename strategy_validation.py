"""Fail-closed deployment gate based on recent, net-of-cost OOS evidence.

A directional score or a Monte Carlo path frequency is not sufficient to
authorize a trade. This module records the latest chronological walk-forward
result for a precise market/configuration key and re-checks its evidence before
an actionable gate can be returned. It does not tune or modify signals.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from research_provenance import config_fingerprint

_LOCK = threading.RLock()
ENGINE_VERSION = "StrategyValidationGate V1.1"
SIGNATURE_ENV = "CHARTANALYZER_EVIDENCE_SIGNING_KEY"
SIGNATURE_ALGORITHM = "HMAC-SHA256"

DEFAULT_POLICY = {
    "validation_max_age_hours": 24.0,
    "min_oos_trades": 200,
    "min_validation_folds": 5,
    "min_oos_net_profit_factor": 1.20,
    "min_oos_net_expectancy_pct": 0.0,
    "min_positive_fold_ratio": 0.70,
    "max_oos_drawdown_pct": 15.0,
    "max_adjusted_p_value": 0.05,
    "require_positive_cost_stress_1_5x": True,
    "require_positive_oos_ci": True,
    "require_gap_free_data": True,
}


def evidence_key(source: str, symbol: str, timeframe: str, config_hash: str) -> str:
    return "|".join((str(source), str(symbol).upper(), str(timeframe), str(config_hash)))


def _finite(value: Any) -> Optional[float]:
    try:
        result = float(value)
        if math.isfinite(result):
            return result
        return math.inf if result > 0 else None
    except (TypeError, ValueError, OverflowError):
        if str(value).upper() in {"INF", "INFINITY"}:
            return math.inf
        return None


def build_evidence_record(result: Dict[str, Any], *, source: str, symbol: str,
                          timeframe: str, config_hash: str,
                          created_at: Optional[float] = None) -> Dict[str, Any]:
    """Extract only OOS trade/fold evidence; no rejected-label or in-sample data."""
    er = result.get("entry_edge_research") or {}
    overall = er.get("overall") or {}
    folds = result.get("folds") or []
    positive_folds = sum(
        1 for fold in folds
        if (_finite(fold.get("strategy_pct")) is not None and float(fold["strategy_pct"]) > 0)
    )
    total_folds = len(folds)
    data_integrity = result.get("data_integrity") or {}
    provenance = result.get("provenance") or {}
    cohort = result.get("research_cohort_integrity") or {}
    profit_factor = _finite(result.get("net_profit_factor"))
    if profit_factor == math.inf:
        profit_factor = "INF"  # JSON-safe representation
    return {
        "engine": ENGINE_VERSION,
        "key": evidence_key(source, symbol, timeframe, config_hash),
        "source": str(source),
        "symbol": str(symbol),
        "timeframe": str(timeframe),
        "config_fingerprint": str(config_hash),
        "run_id": provenance.get("run_id"),
        "dataset_id": provenance.get("dataset_id"),
        "created_at": float(created_at if created_at is not None else time.time()),
        "data_last_timestamp": provenance.get("last_timestamp"),
        "data_integrity_valid": bool(data_integrity.get("valid", False)),
        "data_gap_count": int(data_integrity.get("gap_count", 0) or 0),
        "cohort_integrity": str(cohort.get("status", "UNKNOWN")),
        "strategy_frozen": bool(result.get("strategy_frozen", False)),
        "parameter_optimization": bool(result.get("parameter_optimization", True)),
        "oos_trades": int(result.get("trades", 0) or 0),
        "net_expectancy_pct": _finite(result.get("net_expectancy_pct")),
        "net_profit_factor": profit_factor,
        "bootstrap_ci_95_pct": (result.get("research_inference") or {}).get("bootstrap_ci_95_pct", overall.get("bootstrap_ci_95_pct")),
        "raw_p_value": (result.get("research_inference") or {}).get("p_value"),
        "adjusted_p_value": result.get("adjusted_p_value"),
        "max_oos_drawdown_pct": _finite(result.get("max_dd_pct")),
        "cost_stress_1_5x_positive": result.get("cost_stress_1_5x_positive"),
        "cost_stress_1_5x": result.get("cost_stress_1_5x"),
        "fold_count": total_folds,
        "positive_folds": positive_folds,
        "positive_fold_ratio": (positive_folds / total_folds) if total_folds else 0.0,
        "fold_returns_pct": [
            _finite(fold.get("strategy_pct")) for fold in folds
        ],
    }


def evaluate_evidence(record: Optional[Dict[str, Any]], policy: Optional[Dict[str, Any]] = None,
                      *, now: Optional[float] = None) -> Dict[str, Any]:
    p = dict(DEFAULT_POLICY)
    if isinstance(policy, dict):
        p.update(policy)
    # A local config may make the gate stricter, never weaker than the research policy.
    # Production policy is a hard floor/ceiling. User configuration may only
    # make these constraints stricter; it cannot disable a mandatory control.
    p["validation_max_age_hours"] = min(float(DEFAULT_POLICY["validation_max_age_hours"]), float(p["validation_max_age_hours"]))
    p["min_oos_trades"] = max(int(DEFAULT_POLICY["min_oos_trades"]), int(p["min_oos_trades"]))
    p["min_validation_folds"] = max(int(DEFAULT_POLICY["min_validation_folds"]), int(p["min_validation_folds"]))
    p["min_oos_net_profit_factor"] = max(float(DEFAULT_POLICY["min_oos_net_profit_factor"]), float(p["min_oos_net_profit_factor"]))
    p["min_oos_net_expectancy_pct"] = max(float(DEFAULT_POLICY["min_oos_net_expectancy_pct"]), float(p["min_oos_net_expectancy_pct"]))
    p["min_positive_fold_ratio"] = max(float(DEFAULT_POLICY["min_positive_fold_ratio"]), float(p["min_positive_fold_ratio"]))
    p["max_oos_drawdown_pct"] = min(float(DEFAULT_POLICY["max_oos_drawdown_pct"]), float(p["max_oos_drawdown_pct"]))
    p["max_adjusted_p_value"] = min(float(DEFAULT_POLICY["max_adjusted_p_value"]), float(p["max_adjusted_p_value"]))
    for mandatory in ("require_positive_cost_stress_1_5x", "require_positive_oos_ci", "require_gap_free_data"):
        p[mandatory] = True
    current = float(now if now is not None else time.time())
    if not isinstance(record, dict):
        return {
            "status": "MISSING", "eligible": False,
            "reasons": ["No matching walk-forward evidence exists for this market and strategy configuration."],
            "policy": p,
        }

    reasons = []
    created = _finite(record.get("created_at"))
    age_hours = (current - created) / 3600.0 if created is not None else math.inf
    if age_hours < -0.05:
        reasons.append("Validation evidence timestamp is in the future; evidence rejected.")
    elif age_hours > float(p["validation_max_age_hours"]):
        reasons.append(f"Walk-forward evidence expired ({age_hours:.1f}h old; limit {p['validation_max_age_hours']}h).")

    if not record.get("data_integrity_valid", False):
        reasons.append("The validation sample did not pass the OHLCV integrity gate.")
    if bool(p.get("require_gap_free_data", True)) and int(record.get("data_gap_count", 0) or 0) > 0:
        reasons.append("The validation sample contains candle gaps; gap-free evidence is required.")
    if record.get("cohort_integrity") != "PASS":
        reasons.append("OOS diagnostic cohort integrity did not PASS.")
    if not record.get("strategy_frozen", False) or record.get("parameter_optimization", True):
        reasons.append("Validation must use the frozen strategy with no parameter optimization.")

    n = int(record.get("oos_trades", 0) or 0)
    if n < int(p["min_oos_trades"]):
        reasons.append(f"Insufficient executed OOS trades ({n} < {int(p['min_oos_trades'])}).")
    expectancy = _finite(record.get("net_expectancy_pct"))
    if expectancy is None or expectancy <= float(p["min_oos_net_expectancy_pct"]):
        reasons.append("Net OOS expectancy is not above the required threshold.")
    pf = _finite(record.get("net_profit_factor"))
    if pf is None or pf < float(p["min_oos_net_profit_factor"]):
        reasons.append("Net OOS profit factor is below the required threshold.")

    folds = int(record.get("fold_count", 0) or 0)
    if folds < int(p["min_validation_folds"]):
        reasons.append(f"Too few walk-forward folds ({folds} < {int(p['min_validation_folds'])}).")
    positive_ratio = _finite(record.get("positive_fold_ratio"))
    if positive_ratio is None or positive_ratio < float(p["min_positive_fold_ratio"]):
        reasons.append("Positive-fold stability is below the required ratio.")

    dd = _finite(record.get("max_oos_drawdown_pct"))
    if dd is None or abs(dd) > float(p["max_oos_drawdown_pct"]):
        reasons.append(f"OOS maximum drawdown exceeds the {p['max_oos_drawdown_pct']:.1f}% limit or is unavailable.")
    q_value = _finite(record.get("adjusted_p_value"))
    if q_value is None or q_value > float(p["max_adjusted_p_value"]):
        reasons.append("Multiple-testing-adjusted p-value is missing or above the required threshold.")
    if bool(p.get("require_positive_cost_stress_1_5x", True)) and record.get("cost_stress_1_5x_positive") is not True:
        reasons.append("Exact 1.5x modeled-cost Walk-Forward stress did not retain positive expectancy.")

    ci = record.get("bootstrap_ci_95_pct")
    ci_lower = _finite(ci[0]) if isinstance(ci, (list, tuple)) and len(ci) >= 2 else None
    if bool(p.get("require_positive_oos_ci", True)) and (ci_lower is None or ci_lower <= 0):
        reasons.append("The moving-block bootstrap 95% CI does not have a strictly positive lower bound.")

    return {
        "status": "ELIGIBLE" if not reasons else "BLOCKED",
        "eligible": not reasons,
        "reasons": reasons,
        "age_hours": round(max(0.0, age_hours), 2) if math.isfinite(age_hours) else None,
        "oos_trades": n,
        "net_expectancy_pct": expectancy,
        "net_profit_factor": "INF" if pf == math.inf else pf,
        "positive_folds": int(record.get("positive_folds", 0) or 0),
        "fold_count": folds,
        "positive_fold_ratio": round(float(positive_ratio), 4) if positive_ratio is not None else None,
        "bootstrap_ci_95_pct": ci,
        "max_oos_drawdown_pct": dd,
        "adjusted_p_value": q_value,
        "cost_stress_1_5x_positive": record.get("cost_stress_1_5x_positive"),
        "cost_stress_1_5x": record.get("cost_stress_1_5x"),
        "run_id": record.get("run_id"),
        "dataset_id": record.get("dataset_id"),
        "policy": p,
    }


def _signing_key() -> Optional[bytes]:
    raw = os.environ.get(SIGNATURE_ENV, "")
    key = raw.encode("utf-8")
    return key if len(key) >= 32 else None


def _canonical_record(record: Dict[str, Any]) -> bytes:
    payload = {k: v for k, v in record.items() if k != "integrity_signature"}
    return json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _signature(record: Dict[str, Any], key: bytes) -> str:
    return hmac.new(key, _canonical_record(record), hashlib.sha256).hexdigest()


def verify_evidence_signature(record: Dict[str, Any], key: Optional[bytes] = None) -> bool:
    key = key or _signing_key()
    signature = record.get("integrity_signature")
    if not key or not isinstance(signature, dict):
        return False
    if signature.get("algorithm") != SIGNATURE_ALGORITHM:
        return False
    try:
        expected = _signature(record, key)
    except (TypeError, ValueError, OverflowError):
        return False
    return hmac.compare_digest(str(signature.get("value", "")), expected)


def save_evidence(path: str | os.PathLike[str], record: Dict[str, Any]) -> None:
    """Atomically persist signed evidence; unsigned records are never deployable."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    signed = dict(record)
    key = _signing_key()
    signed.pop("integrity_signature", None)
    if key:
        signed["integrity_signature"] = {"algorithm": SIGNATURE_ALGORITHM, "value": _signature(signed, key)}
    with _LOCK:
        store = {}
        if target.exists():
            try:
                loaded = json.loads(target.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    store = loaded
            except (OSError, json.JSONDecodeError):
                # A damaged evidence store is not trusted. Start a clean store;
                # absence of evidence causes the live gate to fail closed.
                store = {}
        store[str(signed["key"])] = signed
        fd, temp_name = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=str(target.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(store, stream, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, target)
            try:
                os.chmod(target, 0o600)
            except OSError:
                pass
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)


def load_evidence(path: str | os.PathLike[str], *, source: str, symbol: str,
                  timeframe: str, config_hash: str) -> Optional[Dict[str, Any]]:
    target = Path(path)
    if not target.exists():
        return None
    key = evidence_key(source, symbol, timeframe, config_hash)
    with _LOCK:
        try:
            store = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
    record = store.get(key) if isinstance(store, dict) else None
    if not isinstance(record, dict):
        return None
    # Verify the content, not just the dictionary key, before trusting it.
    if (record.get("key") != key or record.get("config_fingerprint") != str(config_hash)
            or record.get("source") != str(source)
            or str(record.get("symbol", "")).upper() != str(symbol).upper()
            or record.get("timeframe") != str(timeframe)):
        return None
    if not verify_evidence_signature(record):
        return None
    return record


def deployment_status(path: str | os.PathLike[str], *, source: str, symbol: str,
                      timeframe: str, config_hash: str, policy: Optional[Dict[str, Any]] = None,
                      now: Optional[float] = None) -> Dict[str, Any]:
    record = load_evidence(path, source=source, symbol=symbol, timeframe=timeframe,
                           config_hash=config_hash)
    return evaluate_evidence(record, policy, now=now)


__all__ = ["ENGINE_VERSION", "DEFAULT_POLICY", "evidence_key", "build_evidence_record",
           "evaluate_evidence", "save_evidence", "load_evidence", "deployment_status", "verify_evidence_signature"]
