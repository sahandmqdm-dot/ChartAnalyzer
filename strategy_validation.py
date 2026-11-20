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
            "status": "MISSING", "decision_class": "UNVERIFIED", "eligible": False,
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

    data_invalid = (
        not record.get("data_integrity_valid", False)
        or int(record.get("data_gap_count", 0) or 0) > 0
        or record.get("cohort_integrity") != "PASS"
    )
    sample_complete = (
        n >= int(p["min_oos_trades"])
        and folds >= int(p["min_validation_folds"])
        and ci_lower is not None
        and q_value is not None
        and bool(record.get("strategy_frozen", False))
        and not bool(record.get("parameter_optimization", True))
        and 0.0 <= age_hours <= float(p["validation_max_age_hours"])
        and int(record.get("data_gap_count", 0) or 0) == 0
    )
    if data_invalid:
        decision_class = "DATA_INVALID"
    elif not sample_complete:
        decision_class = "INSUFFICIENT_EVIDENCE"
    elif expectancy is not None and (expectancy <= 0.0 or (pf is not None and pf <= 1.0)):
        decision_class = "NO_EDGE"
    elif not reasons:
        decision_class = "PAPER_CANDIDATE"
    else:
        decision_class = "UNVERIFIED"

    return {
        "status": "ELIGIBLE" if not reasons else "BLOCKED",
        "decision_class": decision_class,
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


def evaluate_strategy_lifecycle(
    windows: list[Dict[str, Any]], *, min_trades_per_window: int = 200,
    required_bad_windows: int = 2,
) -> Dict[str, Any]:
    """Assess retirement evidence from consecutive, disjoint, frozen OOS windows.

    A single weak run, stale record, invalid dataset, or underpowered window can
    never retire a strategy. Windows must be supplied oldest-to-newest and carry
    numeric start/end timestamps. This function proposes a lifecycle state; it
    does not mutate a registry or disable a strategy.
    """
    if required_bad_windows < 2:
        raise ValueError("required_bad_windows must be at least 2")
    if min_trades_per_window < 1:
        raise ValueError("min_trades_per_window must be positive")
    if not isinstance(windows, list) or len(windows) < required_bad_windows:
        return {"lifecycle": "WATCH", "reason": "Not enough independent OOS windows."}

    ordered = windows[-required_bad_windows:]
    previous_end = None
    expected_config = ordered[0].get("config_fingerprint") if isinstance(ordered[0], dict) else None
    if not expected_config:
        return {"lifecycle": "WATCH", "reason": "Strategy configuration identity is missing."}
    for item in ordered:
        if not isinstance(item, dict):
            return {"lifecycle": "WATCH", "reason": "Malformed OOS window record."}
        start = _finite(item.get("start_at"))
        end = _finite(item.get("end_at"))
        n = _finite(item.get("oos_trades"))
        expectancy = _finite(item.get("net_expectancy_pct"))
        pf = _finite(item.get("net_profit_factor"))
        ci = item.get("bootstrap_ci_95_pct")
        ci_upper = _finite(ci[1]) if isinstance(ci, (list, tuple)) and len(ci) >= 2 else None
        if (start is None or end is None or start >= end or
                (previous_end is not None and start < previous_end)):
            return {"lifecycle": "WATCH", "reason": "OOS windows are not valid and disjoint."}
        previous_end = end
        if (item.get("config_fingerprint") != expected_config
                or item.get("data_integrity_valid") is not True
                or item.get("cohort_integrity") != "PASS"
                or item.get("strategy_frozen") is not True
                or item.get("parameter_optimization") is not False
                or n is None or n < min_trades_per_window
                or expectancy is None or pf is None or ci_upper is None):
            return {"lifecycle": "WATCH", "reason": "A window lacks sufficient trusted evidence."}
        if not (expectancy < 0.0 and pf < 1.0 and ci_upper < 0.0):
            return {"lifecycle": "ACTIVE_REVIEW", "reason": "Consecutive negative-edge retirement condition not met."}
    return {
        "lifecycle": "RETIRE_CANDIDATE",
        "reason": f"{required_bad_windows} consecutive disjoint, sufficiently powered OOS windows show negative net expectancy, PF below 1, and a negative 95% CI upper bound.",
        "windows_evaluated": required_bad_windows,
        "automatic_deactivation": False,
    }


KEY_FILE_ENV = "CHARTANALYZER_EVIDENCE_KEY_FILE"
_KEY_CACHE: Dict[str, Any] = {"path": None, "key": None}


def _default_key_path() -> Path:
    override = os.environ.get(KEY_FILE_ENV, "").strip()
    if override:
        return Path(override)
    return Path(__file__).resolve().parent / "data" / ".evidence_signing_key"


def _load_or_create_key_file(path: Path) -> Optional[bytes]:
    """Return a persistent local signing key, creating it once (mode 0600).

    The environment variable remains the preferred source. The key file only
    prevents the previous silent failure where every record was saved unsigned
    and then rejected on read. It protects against accidental or casual edits
    of the evidence store, not against an attacker who can already read this
    user's files; use the environment variable for stronger separation.
    """
    with _LOCK:
        try:
            if path.exists():
                raw = path.read_text(encoding="utf-8").strip().encode("utf-8")
                return raw if len(raw) >= 32 else None
            path.parent.mkdir(parents=True, exist_ok=True)
            token = os.urandom(48).hex()
            fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(token)
            return token.encode("utf-8")
        except FileExistsError:
            try:
                raw = path.read_text(encoding="utf-8").strip().encode("utf-8")
                return raw if len(raw) >= 32 else None
            except OSError:
                return None
        except OSError:
            return None


def signing_key_source() -> str:
    """'env', 'key_file' or 'none' - what is active in this process now."""
    raw = os.environ.get(SIGNATURE_ENV, "")
    if len(raw.encode("utf-8")) < 32:
        return "none"
    path = _default_key_path()
    try:
        if path.exists() and path.read_text(encoding="utf-8").strip() == raw:
            return "key_file"
    except OSError:
        pass
    return "env"


def provision_signing_key() -> str:
    """Make a signing key available to this process; returns its source.

    Called once by the application at start-up (NOT implicitly by the library,
    so the library contract "no configured key => evidence is never deployable"
    stays intact). Order: environment variable, else a persistent local key
    file that is created on first use. Returns 'env', 'key_file' or 'none'.
    """
    if len(os.environ.get(SIGNATURE_ENV, "").encode("utf-8")) >= 32:
        return "env"
    key = _load_or_create_key_file(_default_key_path())
    if key:
        os.environ[SIGNATURE_ENV] = key.decode("utf-8")
        return "key_file"
    return "none"


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


def _classify_untrusted(record: Dict[str, Any], *, source: str, symbol: str,
                        timeframe: str, config_hash: str) -> str:
    """Why a stored record was not trusted - never evaluates its metrics."""
    if (record.get("key") != evidence_key(source, symbol, timeframe, config_hash)
            or record.get("config_fingerprint") != str(config_hash)):
        return "KEY_MISMATCH"
    sig = record.get("integrity_signature")
    if not isinstance(sig, dict):
        return "UNSIGNED_RECORD"
    if sig.get("algorithm") != SIGNATURE_ALGORITHM:
        return "BAD_ALGORITHM"
    return "SIGNATURE_MISMATCH"


_UNTRUSTED_TEXT = {
    "UNSIGNED_RECORD": "The stored evidence record has no signature (it was written while no signing key was available). It was not trusted; re-run Walk-Forward to regenerate it.",
    "SIGNATURE_MISMATCH": "The stored evidence record signature does not match (record edited, or the signing key changed since it was written). It was not trusted; re-run Walk-Forward to regenerate it.",
    "KEY_MISMATCH": "The stored evidence record does not match this market/configuration key; it was not trusted.",
    "BAD_ALGORITHM": "The stored evidence record uses an unsupported signature algorithm; it was not trusted.",
    "NO_SIGNING_KEY": "No evidence signing key is configured (set CHARTANALYZER_EVIDENCE_SIGNING_KEY to 32+ characters, or start the dashboard through app.py, which provisions a local key file). Evidence without a key can never be verified; re-run Walk-Forward after the key exists.",
}


def deployment_status(path: str | os.PathLike[str], *, source: str, symbol: str,
                      timeframe: str, config_hash: str, policy: Optional[Dict[str, Any]] = None,
                      now: Optional[float] = None) -> Dict[str, Any]:
    record = load_evidence(path, source=source, symbol=symbol, timeframe=timeframe,
                           config_hash=config_hash)
    if record is not None:
        out = evaluate_evidence(record, policy, now=now)
        out["evidence_trusted"] = True
        out["signing_key_source"] = signing_key_source()
        return out

    # Distinguish absent evidence from a present-but-untrusted record without
    # ever evaluating the unverified metrics themselves.
    key = evidence_key(source, symbol, timeframe, config_hash)
    stored = None
    try:
        store = json.loads(Path(path).read_text(encoding="utf-8"))
        if isinstance(store, dict) and isinstance(store.get(key), dict):
            stored = store[key]
    except (OSError, json.JSONDecodeError):
        stored = None
    out = evaluate_evidence(None, policy, now=now)
    out["evidence_trusted"] = False
    out["signing_key_source"] = signing_key_source()
    if stored is not None:
        code = _classify_untrusted(stored, source=source, symbol=symbol,
                                   timeframe=timeframe, config_hash=config_hash)
        if code in ("UNSIGNED_RECORD", "SIGNATURE_MISMATCH") and out["signing_key_source"] == "none":
            code = "NO_SIGNING_KEY"
        out.update(status="BLOCKED", eligible=False, reason_code=code,
                   reasons=[_UNTRUSTED_TEXT[code]],
                   untrusted_run_id=stored.get("run_id"))
    else:
        out.update(reason_code="NO_EVIDENCE")
    return out


__all__ = ["ENGINE_VERSION", "DEFAULT_POLICY", "evidence_key", "build_evidence_record",
           "evaluate_evidence", "evaluate_strategy_lifecycle", "save_evidence", "load_evidence", "deployment_status", "verify_evidence_signature",
           "signing_key_source", "provision_signing_key"]
