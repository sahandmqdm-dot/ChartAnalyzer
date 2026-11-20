"""
agent_core.py - trade gate + prediction journal used by app.py.

REBUILT: the original agent_core.py was not inside the ChartAnalyzer.zip you sent, so app.py could not even start.
This version implements exactly the interface app.py calls (decide / log_prediction / resolve_due / evaluate / DB_PATH).
If you still have your original file, keep yours - the signatures below match app.py, nothing else depends on this file.

Safety properties: SQLite only through parameterised queries, DB file chmod 600, no network access, no secrets stored.
"""
import json, math, os, sqlite3, threading, time
import numpy as np
from decision_quality import DecisionQualityError, evaluate_decision_quality

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent_journal.db")
TF_SEC = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400, "12h": 43200, "1d": 86400}
_LOCK = threading.Lock()
Z80 = 1.2815515655446004


class _ClosingSQLiteConnection(sqlite3.Connection):
    """Commit/rollback through the context manager, then always close the handle."""

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()

SCHEMA = """CREATE TABLE IF NOT EXISTS predictions(
 id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT NOT NULL, tf TEXT NOT NULL, bar INTEGER NOT NULL,
 horizon_h REAL NOT NULL, last REAL NOT NULL, p_up REAL, q10 REAL, q50 REAL, q90 REAL, t_eff REAL, verdict TEXT,
 score REAL, gate TEXT, plan TEXT, extra TEXT, created REAL NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending', outcome REAL, resolved_at REAL,
 UNIQUE(key, tf, bar, horizon_h))"""


def _secure_sqlite_file(path):
    """Create/chmod the database before SQLite opens it, avoiding a permissive window."""
    if os.name != "posix":
        return
    if os.path.islink(path):
        raise OSError("SQLite database path must not be a symbolic link")
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    try:
        os.fchmod(fd, 0o600)
    finally:
        os.close(fd)


def _db():
    _secure_sqlite_file(DB_PATH)
    con = sqlite3.connect(DB_PATH, timeout=15, factory=_ClosingSQLiteConnection)
    con.row_factory = sqlite3.Row
    con.execute(SCHEMA)
    # SQLite may create WAL/SHM sidecars; keep all journal state owner-readable only.
    if os.name == "posix":
        for candidate in (DB_PATH, DB_PATH + "-wal", DB_PATH + "-shm"):
            try:
                if os.path.exists(candidate):
                    os.chmod(candidate, 0o600)
            except OSError:
                pass
    return con


# ------------------------------------------------------------------ trade gate
def decide(t_eff, last, stop, t1, p_t1, p_stop, fee_rt=0.004, min_t=2.0, min_rr=1.5,
           spread_slippage_rt=0.0, min_probability_samples=30, edge_n=None):
    """Conservative long-trade gate.

    fee_rt and spread_slippage_rt are ROUND-TRIP fractions. The gate never
    interprets a directional score as a probability or as evidence of edge.
    """
    reasons = []
    ev = None
    try:
        t_eff = float(t_eff)
        fee_rt = float(fee_rt)
        spread_slippage_rt = float(spread_slippage_rt)
        min_t, min_rr = float(min_t), float(min_rr)
        p_t1, p_stop = float(p_t1), float(p_stop)
    except (TypeError, ValueError):
        t_eff, fee_rt, spread_slippage_rt = 0.0, 0.004, 0.0
        min_t, min_rr, p_t1, p_stop = 2.0, 1.5, 0.0, 0.0
        reasons.append("invalid gate inputs")

    if not all(math.isfinite(x) for x in (t_eff, fee_rt, spread_slippage_rt, min_t, min_rr, p_t1, p_stop)):
        reasons.append("non-finite gate inputs")
    if fee_rt < 0 or spread_slippage_rt < 0:
        reasons.append("execution costs cannot be negative")
    if min_t <= 0 or min_rr <= 0:
        reasons.append("gate thresholds must be positive")
    if p_t1 < 0 or p_stop < 0 or p_t1 + p_stop > 1.000001:
        reasons.append("invalid or non-mutually-exclusive plan outcome probabilities")

    valid_plan = False
    try:
        last, stop, t1 = float(last), float(stop), float(t1)
        valid_plan = all(math.isfinite(x) and x > 0 for x in (last, stop, t1)) and stop < last < t1
    except (TypeError, ValueError):
        pass
    if not valid_plan:
        reasons.append("no valid long plan (requires positive stop < price < target)")

    if valid_plan and not reasons:
        gross_reward = (t1 - last) / last
        gross_risk = (last - stop) / last
        friction = fee_rt + spread_slippage_rt
        net_reward = gross_reward - friction
        net_risk = gross_risk + friction
        gross_rr = gross_reward / gross_risk if gross_risk > 0 else None
        net_rr = net_reward / net_risk if net_risk > 0 else None
        p_neither = max(0.0, 1.0 - p_t1 - p_stop)
        evv = p_t1 * net_reward - p_stop * net_risk - p_neither * friction
        be = net_risk / (net_reward + net_risk) if net_reward + net_risk > 0 else None
        ev = {"ev": float(evv), "rr_net": None if net_rr is None else float(net_rr),
              "rr_gross": None if gross_rr is None else float(gross_rr),
              "gross_reward_pct": float(gross_reward * 100), "gross_risk_pct": float(gross_risk * 100),
              "net_reward_pct": float(net_reward * 100), "net_risk_pct": float(net_risk * 100),
              "breakeven_winrate": None if be is None else float(be),
              "p_t1": p_t1, "p_stop": p_stop, "p_neither": p_neither,
              "round_trip_cost": float(friction)}
        if net_reward <= 0:
            reasons.append("target reward does not cover round-trip fees, spread and slippage")
        if net_rr is None or net_rr < min_rr:
            reasons.append(f"net risk/reward after fees and execution costs is {net_rr:.2f} (< {min_rr})" if net_rr is not None else "bad net risk/reward")
        if evv <= 0:
            reasons.append(f"expected value after execution costs is {evv * 100:.2f}% (<= 0)")

    if t_eff < min_t:
        reasons.append(f"score has no demonstrated edge here (t_eff {t_eff:.2f} < {min_t})")
    if edge_n is not None:
        try:
            n = int(edge_n)
            if n < int(min_probability_samples):
                reasons.append(f"edge sample is too small (n={n} < {int(min_probability_samples)})")
        except (TypeError, ValueError):
            reasons.append("edge sample count is invalid")

    ok = not reasons
    return {"state": "ALLOWED" if ok else "NO_TRADE", "reasons": reasons,
            "confidence_cap": "high" if ok and t_eff >= min_t * 1.5 else ("medium" if ok else "low"),
            "ev": ev, "fee_rt": float(fee_rt), "spread_slippage_rt": float(spread_slippage_rt),
            "min_t": float(min_t), "min_rr": float(min_rr),
            "edge_n": None if edge_n is None else int(edge_n)}


# ------------------------------------------------------------------ journal
def log_prediction(key, tf, bar, horizon_h, last, p_up, q10, q50, q90, t_eff, verdict,
                   score=None, gate=None, plan=None, extra=None):
    with _LOCK, _db() as con:
        con.execute("INSERT OR IGNORE INTO predictions(key,tf,bar,horizon_h,last,p_up,q10,q50,q90,t_eff,verdict,score,gate,plan,extra,created)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (key, tf, int(bar), float(horizon_h), float(last), float(p_up), float(q10), float(q50), float(q90),
                     float(t_eff), str(verdict), None if score is None else float(score), None if gate is None else str(gate),
                     json.dumps(plan, default=str) if plan is not None else None,
                     json.dumps(extra, default=str) if extra is not None else None, time.time()))


def _target(row):
    tsec = TF_SEC[row["tf"]]
    H = max(1, int(round(row["horizon_h"] * 3600 / tsec)))
    return row["bar"] + H * tsec, tsec, H


def resolve_due(price_at, limit=40):
    """price_at(key, tf, ts) -> close of the candle that opened at ts (None until it is complete)."""
    now = time.time()
    with _LOCK, _db() as con:
        rows = con.execute("SELECT * FROM predictions WHERE status='pending' ORDER BY bar LIMIT 500").fetchall()
        done = 0
        for r in rows:
            if r["tf"] not in TF_SEC:
                continue
            ts, tsec, _ = _target(r)
            if now < ts + tsec:
                continue
            if done >= limit:
                break
            done += 1
            px = None
            try:
                px = price_at(r["key"], r["tf"], ts)
            except Exception:
                px = None
            if px and px > 0:
                con.execute("UPDATE predictions SET status='resolved', outcome=?, resolved_at=? WHERE id=?", (float(px), now, r["id"]))
            elif now - ts > max(10 * tsec, 3 * 86400):
                con.execute("UPDATE predictions SET status='expired', resolved_at=? WHERE id=?", (now, r["id"]))


def calibrate_probability(key, tf, horizon_h, p_raw, min_samples=30, prior_strength=20.0):
    """Causal, per-instrument probability calibration from resolved historical forecasts.

    Pending forecasts are excluded. A local kernel estimate is shrunk toward the
    raw model probability; below min_samples the raw probability is preserved.
    This is a conservative online calibrator, not an autonomous strategy optimizer.
    """
    p_raw = float(np.clip(p_raw, 0.001, 0.999))
    with _LOCK, _db() as con:
        rows = con.execute(
            "SELECT p_up,last,outcome FROM predictions "
            "WHERE key=? AND tf=? AND ABS(horizon_h-?)<1e-6 AND status='resolved' "
            "AND p_up IS NOT NULL AND outcome IS NOT NULL ORDER BY resolved_at",
            (str(key), str(tf), float(horizon_h))).fetchall()
    n = len(rows)
    if n < int(min_samples):
        return {"probability": p_raw, "raw_probability": p_raw, "samples": n,
                "status": "learning_warmup", "min_samples": int(min_samples),
                "method": "causal_local_kernel_beta_shrinkage"}
    p = np.asarray([float(r["p_up"]) for r in rows], dtype=float)
    y = np.asarray([float(float(r["outcome"]) > float(r["last"])) for r in rows], dtype=float)
    # Gaussian kernel around the current raw probability; bandwidth is intentionally broad.
    bandwidth = 0.15
    w = np.exp(-0.5 * ((p - p_raw) / bandwidth) ** 2)
    effective_n = float(w.sum() ** 2 / max(np.dot(w, w), 1e-12))
    empirical = float(np.dot(w, y) / max(w.sum(), 1e-12))
    # Prior centred on the current model probability prevents unstable small-bin jumps.
    strength = float(prior_strength)
    calibrated = (effective_n * empirical + strength * p_raw) / (effective_n + strength)
    calibrated = float(np.clip(calibrated, 0.02, 0.98))
    return {"probability": calibrated, "raw_probability": p_raw, "empirical_local_rate": empirical,
            "samples": n, "effective_samples": round(effective_n, 1),
            "status": "adaptive_calibration_active", "method": "causal_local_kernel_beta_shrinkage",
            "bandwidth": bandwidth, "prior_strength": strength}


def _corr(a, b):
    if len(a) < 5 or np.std(a) < 1e-12 or np.std(b) < 1e-12:
        return 0.0
    return float(np.corrcoef(a, b)[0, 1])


def _hac_corr_t(x, y, max_lag=None):
    """Correlation and Newey-West t statistic for overlapping observations."""
    x = np.asarray(x, float); y = np.asarray(y, float)
    m = np.isfinite(x) & np.isfinite(y); x, y = x[m], y[m]
    n = len(x)
    if n < 8 or np.std(x) < 1e-12 or np.std(y) < 1e-12:
        return 0.0, 0.0
    x = (x-x.mean()) / x.std(ddof=1); y = (y-y.mean()) / y.std(ddof=1)
    rho = float(np.mean(x*y))
    # Influence function for sample correlation; HAC variance handles serial dependence.
    psi = x*y - 0.5*rho*(x*x+y*y)
    psi -= psi.mean()
    L = int(max_lag if max_lag is not None else min(20, max(1, round(n**(1/3)))))
    gamma0 = float(np.dot(psi, psi)/n)
    longvar = gamma0
    for lag in range(1, min(L, n-1)+1):
        weight = 1.0 - lag/(L+1.0)
        cov = float(np.dot(psi[lag:], psi[:-lag])/n)
        longvar += 2*weight*cov
    se = math.sqrt(max(longvar, 1e-18)/n)
    return rho, float(rho/se) if se > 0 else 0.0


def evaluate(key, tf, horizon=None):
    q = "SELECT * FROM predictions WHERE key=? AND tf=?"
    args = [key, tf]
    if horizon:
        q += " AND ABS(horizon_h-?)<1e-6"
        args.append(float(horizon))
    with _LOCK, _db() as con:
        rows = con.execute(q, args).fetchall()
    res = [r for r in rows if r["status"] == "resolved"]
    out = {"n": len(res), "pending": sum(r["status"] == "pending" for r in rows),
           "expired": sum(r["status"] == "expired" for r in rows)}
    if not res:
        out["verdict"] = "no resolved predictions yet - the journal needs time (one prediction per new bar)"
        return out
    last = np.array([r["last"] for r in res]); fin = np.array([r["outcome"] for r in res])
    p = np.array([r["p_up"] for r in res]); ret = fin / last - 1
    y = (fin > last).astype(float)
    bm, base = float(np.mean((p - y) ** 2)), float(y.mean())
    out.update(brier_model=bm, brier_half=0.25, brier_base_rate=float(np.mean((base - y) ** 2)),
               skill_vs_half=1 - bm / 0.25, base_rate_up=base)
    q10, q90 = np.array([r["q10"] for r in res]), np.array([r["q90"] for r in res])
    out["band80_coverage"] = float(np.mean((fin >= q10) & (fin <= q90)))
    sc = np.array([np.nan if r["score"] is None else r["score"] for r in res])
    sig = np.log(np.maximum(q90, 1e-12) / np.maximum(q10, 1e-12)) / (2 * Z80)
    z = np.log(fin / last) / np.maximum(sig, 1e-9)
    m = np.isfinite(sc) & np.isfinite(z)
    if m.sum() >= 5:
        H = _target(res[0])[2]
        ic, hac_t = _hac_corr_t(sc[m], z[m], max_lag=max(1, min(50, H)))
        out.update(score_ic=ic, score_hac_t=hac_t,
                   score_neff_note="HAC/Newey-West t-statistic; overlapping forecast dependence partially addressed")
    bull = np.array([r["verdict"] == "Bullish" for r in res])
    out["bullish_n"] = int(bull.sum())
    if bull.sum():
        out.update(bullish_up_rate=float(y[bull].mean()), bullish_avg_ret=float(ret[bull].mean()))
    al = np.array([r["gate"] == "ALLOWED" for r in res])
    out["allowed_n"] = int(al.sum())
    if al.sum():
        out["allowed_avg_ret"] = float(ret[al].mean())
    if len(res) < 30:
        out["verdict"] = f"only {len(res)} resolved predictions - too few for any conclusion (need >= 30, ideally 100+)"
    elif out["skill_vs_half"] > 0 and out.get("score_hac_t", 0) >= 2:
        out["verdict"] = "live evidence of an edge so far - keep monitoring, do not size up yet"
    else:
        out["verdict"] = "no live edge demonstrated"
    return out



def evaluate_decision_journal(key, tf, horizon=None):
    """Evaluate resolved journal decisions using fixed-horizon, net-of-cost long returns.

    Only records with frozen action/reason/cost metadata are eligible. Overlapping
    horizons are deterministically thinned (earliest decision wins) before the
    single-unit diagnostic, and exclusions are reported. This is not a stop/target
    execution backtest or a deployment gate.
    """
    query = "SELECT * FROM predictions WHERE key=? AND tf=? AND status='resolved'"
    args = [str(key), str(tf)]
    if horizon is not None:
        query += " AND ABS(horizon_h-?)<1e-6"
        args.append(float(horizon))
    query += " ORDER BY created, id"
    with _LOCK, _db() as con:
        rows = con.execute(query, args).fetchall()

    candidates = []
    excluded = {"missing_decision_metadata": 0, "invalid_cost_metadata": 0}
    for row in rows:
        try:
            extra = json.loads(row["extra"] or "{}")
            if not isinstance(extra, dict):
                raise ValueError("extra metadata must be an object")
            action = str(extra["decision_action"]).upper()
            reason = str(extra["decision_reason_code"]).strip()
            fee = float(extra["fee_roundtrip"])
            slippage = float(extra["spread_slippage_roundtrip"])
            market_type = str(extra["market_type"])
            if action not in {"ACT", "NO_TRADE"} or not reason or not market_type:
                raise KeyError("decision fields")
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            excluded["missing_decision_metadata"] += 1
            continue
        if not all(math.isfinite(x) and x >= 0 for x in (fee, slippage)):
            excluded["invalid_cost_metadata"] += 1
            continue
        try:
            target_open, bar_seconds, horizon_bars = _target(row)
            decision_at = datetime.fromtimestamp(float(row["created"]), UTC)
            horizon_end = datetime.fromtimestamp(float(target_open + bar_seconds), UTC)
            resolved_at = datetime.fromtimestamp(float(row["resolved_at"]), UTC)
            gross_return = float(row["outcome"]) / float(row["last"]) - 1.0
            net_return = gross_return - fee - slippage
            if not all(math.isfinite(x) for x in (gross_return, net_return)):
                raise ValueError("non-finite return")
        except (KeyError, TypeError, ValueError, OverflowError, OSError):
            excluded["invalid_cost_metadata"] += 1
            continue
        candidates.append({
            "decision_id": str(row["id"]),
            "instrument_id": str(key),
            "market_type": market_type,
            "timeframe": str(tf),
            "horizon_bars": int(horizon_bars),
            "decision_at": decision_at,
            "horizon_end": horizon_end,
            "resolved_at": resolved_at,
            "action": action,
            "reason_code": reason,
            "counterfactual_net_return_if_traded": net_return,
        })

    # Keep the earliest non-overlapping opportunities; do not pretend overlapping
    # forecasts are independent observations or double-count unit-notional capital.
    selected = []
    last_end = None
    for record in candidates:
        if last_end is not None and record["decision_at"] < last_end:
            continue
        selected.append(record)
        last_end = record["horizon_end"]

    report = {
        "schema": "chartanalyzer_decision_journal_bridge_v1",
        "status": "INSUFFICIENT_DATA" if not selected else "DESCRIPTIVE_ONLY",
        "source": "resolved_prediction_journal",
        "resolved_rows_seen": len(rows),
        "metadata_exclusions": excluded,
        "overlap_exclusions": len(candidates) - len(selected),
        "limitations": [
            "fixed-horizon directional long counterfactual; not stop/target execution PnL",
            "costs are frozen round-trip fee and spread/slippage metadata; market impact and funding are not modeled here",
            "earliest non-overlapping opportunity sampling reduces the journal to a single-unit diagnostic",
            "descriptive only; not causal evidence, portfolio performance, or deployment authorization",
        ],
    }
    if not selected:
        report["reason"] = "no resolved decisions with complete frozen action, reason, and cost metadata"
        return report
    try:
        report["metrics"] = evaluate_decision_quality(selected)
        report["status"] = "DESCRIPTIVE_ONLY"
    except DecisionQualityError as exc:
        report["status"] = "INVALID_COHORT"
        report["reason"] = str(exc)
    return report
