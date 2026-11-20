"""
trend_engine.py - causal trend-structure analysis, built for 12h and 1d (works on any timeframe).

Ideas taken from public chart-analysis repos, re-implemented here (no code copied, no new dependencies):
  * smart-money-concepts (swing highs/lows, BOS / CHoCH, fair-value gaps, retracements)
        -> BUT its swing detector uses shift(-n): the swing at bar t needs bars t+n. Here pivots are CONFIRMED
           by an ATR reversal and only become visible at their confirmation bar (conf), never earlier.
  * TrendLineAutomation (log-price support/resistance lines that keep all prices on one side, slope as a signal)
        -> here the lines come from the lower/upper convex hull (the exact optimum of the same objective), are fitted on
           bars [t-w, t-1] and extrapolated to bar t, so a break of the line is visible instead of being absorbed.
  * freqtrade merge_informative_pair (a higher timeframe is only usable once its candle is CLOSED)
        -> analyse() drops the still-forming candle; resample_ohlcv() builds 12h from 4h/1h with a day anchor.
  * variance-ratio (Lo-MacKinlay) + efficiency ratio + ADX as an explicit "is this a trend at all" gate.

Everything is a DESCRIPTION of the chart. Whether it PREDICTS anything is measured separately (app.edge_stats /
history_check.py --trend). On the user's own history_check.csv no signal passed, so nothing here is presented as a forecast.
"""
import math
import numpy as np
import pandas as pd

TF_SEC = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400, "12h": 43200, "1d": 86400}

DEFAULT = {
    "atr_n": 14, "zz_mult": 2.5,
    "windows": [20, 50, 100],          # momentum windows (bars)
    "tl_window": 60, "tl_recent": 0.35,
    "vr_window": 120, "vr_q": 5,
    "ema": [20, 50], "sma": 100,
    "lam": 0.94,
    "fib": [0.382, 0.5, 0.618, 0.786], "fib_ext": [1.272, 1.618],
    "fvg_keep": 3, "fvg_min_atr": 0.3, "fvg_max_age": 120,
    "weights": {"structure": 1.5, "momentum": 1.0, "ma": 1.0, "trendline": 1.0},
    "thr": 0.20,
    "bars": 1000,
}
# per-timeframe overrides: windows are chosen to cover roughly the same CALENDAR spans (10d / 30d / 90d on 12h).
TF_OVERRIDE = {
    "12h": {"windows": [20, 60, 180], "tl_window": 50, "ema": [20, 60], "sma": 120, "vr_window": 160},
    "1d": {"windows": [10, 30, 90], "tl_window": 40, "ema": [10, 30], "sma": 100, "vr_window": 120},
    "4h": {"windows": [30, 90, 270], "tl_window": 60, "ema": [20, 50], "sma": 200},
}


def tcfg(tf, user=None):
    c = {k: (dict(v) if isinstance(v, dict) else list(v) if isinstance(v, list) else v) for k, v in DEFAULT.items()}
    for src in (TF_OVERRIDE.get(tf, {}), user or {}, (user or {}).get("tf", {}).get(tf, {}) if user else {}):
        for k, v in src.items():
            if k == "tf":
                continue
            c[k] = {**c[k], **v} if isinstance(v, dict) and isinstance(c.get(k), dict) else v
    return c


# ------------------------------------------------------------------ resampling (12h from 4h / 1h)
def resample_ohlcv(df, hours, anchor_sec=0, now=None):
    """Aggregate candles (time = open, epoch seconds) into `hours`-hour buckets aligned to `anchor_sec` (seconds after
    UTC midnight at which a bucket may start, e.g. a daily candle's open offset). Buckets with missing base candles are
    dropped, except the last one when it is still forming (its end is in the future)."""
    if len(df) < 2:
        raise ValueError("not enough candles to resample")
    t = df.time.to_numpy(dtype=float)
    base = float(np.median(np.diff(t)))
    bsec = hours * 3600.0
    if base <= 0 or bsec % base > 1e-6 or bsec / base < 1:
        raise ValueError("target bucket is not a multiple of the base timeframe")
    need = int(round(bsec / base))
    off = (anchor_sec - t[0]) % base
    anchor_sec = anchor_sec - off            # snap the anchor onto the base candle grid
    key = np.floor((t - anchor_sec) / bsec).astype(np.int64)
    g = df.assign(_k=key).groupby("_k", sort=True)
    out = g.agg(time=("time", "first"), open=("open", "first"), high=("high", "max"), low=("low", "min"),
                close=("close", "last"), volume=("volume", "sum"), _n=("time", "size"))
    out["time"] = out["time"].astype(float)
    now = float(now) if now is not None else None
    keep = out._n >= need
    if now is not None and len(out):
        if out.time.iloc[-1] + bsec > now:      # forming bucket: keep it (flagged by the caller via time + tsec > now)
            keep.iloc[-1] = True
    dropped = int((~keep).sum())
    out = out[keep].drop(columns="_n").reset_index(drop=True)
    out.attrs["dropped_incomplete"] = dropped
    return out


# ------------------------------------------------------------------ helpers
def _atr(h, l, c, n):
    pc = np.r_[c[0], c[:-1]]
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    a = pd.Series(tr).ewm(alpha=1.0 / n, adjust=False).mean().to_numpy()
    return np.maximum(a, c * 1e-9)


def _adx(h, l, c, n):
    up, dn = np.diff(h, prepend=h[0]), -np.diff(l, prepend=l[0])
    pdm = np.where((up > dn) & (up > 0), up, 0.0)
    mdm = np.where((dn > up) & (dn > 0), dn, 0.0)
    pc = np.r_[c[0], c[:-1]]
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    w = lambda x: pd.Series(x).ewm(alpha=1.0 / n, adjust=False).mean().to_numpy()
    a = np.maximum(w(tr), 1e-12)
    pdi, mdi = 100 * w(pdm) / a, 100 * w(mdm) / a
    dx = 100 * np.abs(pdi - mdi) / np.maximum(pdi + mdi, 1e-12)
    return w(dx)


def _ewm_vol(lc, lam):
    r = np.diff(lc, prepend=lc[0])
    v = np.empty(len(r))
    prev = max(float(np.var(r[1:21])) if len(r) > 21 else float(np.var(r)), 1e-18)
    for i in range(len(r)):
        v[i] = prev                      # variance known BEFORE bar i's return (causal)
        prev = max(lam * prev + (1 - lam) * r[i] ** 2, 1e-18)
    return np.sqrt(v), np.sqrt(np.maximum(prev, 1e-18))


# ------------------------------------------------------------------ market structure (causal ATR zig-zag + BOS/CHoCH)
def structure(h, l, c, atr, mult):
    n = len(c)
    piv, events = [], []
    struct, bias, ev = np.zeros(n), np.zeros(n), np.zeros(n)
    last_hi = prev_hi = last_lo = prev_lo = np.nan
    lvl_hi = lvl_lo = np.nan
    cur_bias = 0
    d = 0
    cand_hi, cand_lo = (h[0], 0), (l[0], 0)
    ext = (np.nan, 0)
    for i in range(n):
        thr = mult * atr[i]
        new = None
        if i > 0:
            if d == 0:
                if h[i] > cand_hi[0]:
                    cand_hi = (h[i], i)
                if l[i] < cand_lo[0]:
                    cand_lo = (l[i], i)
                if cand_lo[1] < i and h[i] - cand_lo[0] >= thr:
                    new, d, ext = (-1, cand_lo), 1, (h[i], i)
                elif cand_hi[1] < i and cand_hi[0] - l[i] >= thr:
                    new, d, ext = (1, cand_hi), -1, (l[i], i)
            elif d == 1:
                if h[i] > ext[0]:
                    ext = (h[i], i)
                elif ext[1] < i and ext[0] - l[i] >= thr:
                    new, d, ext = (1, ext), -1, (l[i], i)
            else:
                if l[i] < ext[0]:
                    ext = (l[i], i)
                elif ext[1] < i and h[i] - ext[0] >= thr:
                    new, d, ext = (-1, ext), 1, (h[i], i)
        if new:
            k, (p, idx) = new
            piv.append({"i": int(idx), "conf": int(i), "p": float(p), "kind": int(k)})
            if k == 1:
                prev_hi, last_hi, lvl_hi = last_hi, p, p
            else:
                prev_lo, last_lo, lvl_lo = last_lo, p, p
        if not math.isnan(lvl_hi) and c[i] > lvl_hi:
            events.append({"i": i, "type": "CHoCH" if cur_bias == -1 else "BOS", "dir": 1, "lvl": float(lvl_hi)})
            ev[i], cur_bias, lvl_hi = 1, 1, np.nan
        elif not math.isnan(lvl_lo) and c[i] < lvl_lo:
            events.append({"i": i, "type": "CHoCH" if cur_bias == 1 else "BOS", "dir": -1, "lvl": float(lvl_lo)})
            ev[i], cur_bias, lvl_lo = -1, -1, np.nan
        bias[i] = cur_bias
        f = lambda a, b, s: (0.0 if (math.isnan(a) or math.isnan(b)) else float(s * (a > b) - s * (a < b)))
        struct[i] = 0.5 * f(last_hi, prev_hi, 1) + 0.5 * f(last_lo, prev_lo, 1)
    tentative = {"i": int(ext[1]), "p": float(ext[0]), "kind": int(d)} if d != 0 and not math.isnan(ext[0]) else None
    return {"pivots": piv, "events": events, "struct": struct, "bias": bias, "ev": ev, "tentative": tentative,
            "levels": {"swing_high": None if math.isnan(last_hi) else float(last_hi),
                       "swing_low": None if math.isnan(last_lo) else float(last_lo)}}


# ------------------------------------------------------------------ trend lines (hull-based, fitted on [t-w, t-1])
def _hull(y, upper):
    pts = []
    for i in range(len(y)):
        while len(pts) >= 2:
            a, b = pts[-2], pts[-1]
            cr = (b - a) * (y[i] - y[a]) - (y[b] - y[a]) * (i - a)
            if (cr >= 0) if upper else (cr <= 0):
                pts.pop()
            else:
                break
        pts.append(i)
    return pts


def _best_line(y, upper, recent):
    w = len(y)
    hp = _hull(y, upper)
    x = np.arange(w)
    best = None
    for a, b in zip(hp[:-1], hp[1:]):
        s = (y[b] - y[a]) / (b - a)
        line = y[a] + s * (x - a)
        err = float(((y - line) ** 2).sum())
        ok = b >= recent * w
        key = (0 if ok else 1, err)
        if best is None or key < best[0]:
            best = (key, a, b, s, y[a] - s * a)
    if best is None:
        return None
    _, a, b, s, icpt = best
    tol = 0.15 * float(np.std(np.diff(y))) * math.sqrt(w) + 1e-12
    touches = int((np.abs(y - (s * x + icpt)) <= tol).sum())
    return {"a": a, "b": b, "slope": s, "icpt": icpt, "touches": touches}


def trendlines(h, l, c, atr, w, recent):
    """Per bar t: slopes (log/bar) of support & resistance fitted on [t-w, t-1], and where close[t] sits vs both."""
    n = len(c)
    lh, ll, lc = np.log(h), np.log(l), np.log(c)
    ss, rs, pos, brk = (np.full(n, np.nan) for _ in range(4))
    last = None
    for t in range(w, n):
        S = _best_line(ll[t - w:t], False, recent)
        R = _best_line(lh[t - w:t], True, recent)
        if not S or not R:
            continue
        sup, res = S["icpt"] + S["slope"] * w, R["icpt"] + R["slope"] * w   # extrapolated to bar t
        ss[t], rs[t] = S["slope"], R["slope"]
        wid = res - sup
        if wid > 1e-9:
            pos[t] = (lc[t] - sup) / wid
        margin = 0.25 * atr[t] / c[t]
        brk[t] = 1.0 if lc[t] > res + margin else -1.0 if lc[t] < sup - margin else 0.0
        if t == n - 1:
            last = {"S": S, "R": R, "t0": t - w, "sup_now": float(math.exp(sup)), "res_now": float(math.exp(res))}
    return {"ss": ss, "rs": rs, "pos": pos, "brk": brk, "last": last}


# ------------------------------------------------------------------ zones: fair-value gaps, fibonacci of the last leg
def fvg_zones(o, h, l, c, atr, keep, min_atr, max_age):
    n = len(c)
    zones = []
    for i in range(max(2, n - max_age), n):
        if l[i] - h[i - 2] >= min_atr * atr[i]:
            lo_, hi_, d = h[i - 2], l[i], 1
            if not (l[i + 1:] <= lo_).any():      # unfilled: price has not traded back through the whole gap
                zones.append({"i": i - 1, "dir": d, "lo": float(lo_), "hi": float(hi_)})
        elif l[i - 2] - h[i] >= min_atr * atr[i]:
            lo_, hi_, d = h[i], l[i - 2], -1
            if not (h[i + 1:] >= hi_).any():
                zones.append({"i": i - 1, "dir": d, "lo": float(lo_), "hi": float(hi_)})
    up = [z for z in zones if z["dir"] == 1][-keep:]
    dn = [z for z in zones if z["dir"] == -1][-keep:]
    return up + dn


def fib_levels(piv, cfg):
    if len(piv) < 2 or piv[-1]["kind"] == piv[-2]["kind"]:
        return None
    a, b = piv[-2], piv[-1]
    up = b["kind"] == 1          # last leg went up (low -> high)
    lo_, hi_ = (a["p"], b["p"]) if up else (b["p"], a["p"])
    rng = hi_ - lo_
    if rng <= 0:
        return None
    pt = (lambda r: hi_ - r * rng) if up else (lambda r: lo_ + r * rng)
    ext = (lambda r: lo_ + r * rng) if up else (lambda r: hi_ - r * rng)
    return {"leg": "up" if up else "down", "from_i": a["i"], "to_i": b["i"], "low": float(lo_), "high": float(hi_),
            "retrace": [{"r": r, "p": float(pt(r))} for r in cfg["fib"]],
            "extension": [{"r": r, "p": float(ext(r))} for r in cfg["fib_ext"]]}


# ------------------------------------------------------------------ full causal series + latest-bar report
def series(df, tf, user=None):
    """All per-bar arrays (causal). Used by analyse() and by history_check.py to measure the score's real edge."""
    C = tcfg(tf, user)
    required = {"time", "open", "high", "low", "close"}
    missing = required.difference(df.columns)
    if missing:
        raise ValueError(f"trend series missing required columns: {', '.join(sorted(missing))}")
    timestamps = df["time"].to_numpy(dtype=float)
    if not np.isfinite(timestamps).all() or np.any(np.diff(timestamps) <= 0):
        raise ValueError("trend series time values must be finite, unique and strictly increasing")
    o, h, l, c = (df[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
    n = len(c)
    if n < 80:
        raise ValueError(f"need at least 80 closed candles for a trend read (got {n})")
    lc = np.log(c)
    atr = _atr(h, l, c, int(C["atr_n"]))
    sig, sig_last = _ewm_vol(lc, float(C["lam"]))
    st = structure(h, l, c, atr, float(C["zz_mult"]))

    # momentum: log move over W bars in units of its random-walk standard deviation
    zs = []
    for W in C["windows"]:
        W = int(W)
        z = np.zeros(n)
        if n > W:
            z[W:] = (lc[W:] - lc[:-W]) / (sig[W:] * math.sqrt(W))
        zs.append(z)
    mom = np.tanh(np.mean(zs, axis=0) / 1.5)

    # moving-average alignment
    s = pd.Series(c)
    ef = s.ewm(span=int(C["ema"][0]), adjust=False).mean().to_numpy()
    es = s.ewm(span=int(C["ema"][1]), adjust=False).mean().to_numpy()
    sm = s.rolling(min(int(C["sma"]), n - 1), min_periods=1).mean().to_numpy()
    ma = (np.tanh((ef - es) / atr) + np.tanh((c - sm) / (2 * atr)) + np.tanh((c - ef) / atr)) / 3

    tl = trendlines(h, l, c, atr, int(min(C["tl_window"], n // 2)), float(C["tl_recent"]))
    slope_mid = np.nan_to_num((tl["ss"] + tl["rs"]) / 2)
    atr_log = atr / c
    tl_c = np.clip(0.7 * np.tanh(slope_mid / (0.15 * atr_log)) + 0.3 * np.nan_to_num(tl["brk"]), -1, 1)

    comps = {"structure": 0.5 * st["struct"] + 0.5 * st["bias"], "momentum": mom, "ma": ma, "trendline": tl_c}
    W_ = C["weights"]
    score = sum(float(W_[k]) * v for k, v in comps.items()) / sum(float(W_[k]) for k in comps)

    # "is it a trend at all" gate
    Wm = int(C["windows"][1])
    er = np.zeros(n)
    if n > Wm:
        net = np.abs(lc[Wm:] - lc[:-Wm])
        path = pd.Series(np.abs(np.diff(lc, prepend=lc[0]))).rolling(Wm).sum().to_numpy()[Wm:]
        er[Wm:] = net / np.maximum(path, 1e-12)
    q, vw = int(C["vr_q"]), int(min(C["vr_window"], n // 2))
    r1 = pd.Series(np.diff(lc, prepend=lc[0]))
    rq = pd.Series(lc).diff(q)
    vr = (rq.rolling(vw).var() / (q * r1.rolling(vw).var()).replace(0, np.nan)).to_numpy()
    vr_z = (vr - 1) / math.sqrt(2 * (2 * q - 1) * (q - 1) / (3 * q * vw))
    adx = _adx(h, l, c, 14)
    trendiness = (np.clip(er / 0.4, 0, 1) + np.clip((adx - 15) / 20, 0, 1) + np.clip(np.nan_to_num(vr_z) / 2.5, 0, 1)) / 3
    return {"C": C, "atr": atr, "sig": sig, "sig_last": sig_last, "st": st, "tl": tl, "comps": comps, "score": score,
            "er": er, "vr": vr, "vr_z": vr_z, "adx": adx, "trendiness": trendiness, "zs": zs,
            "ema": (ef, es, sm), "o": o, "h": h, "l": l, "c": c}


def _label(score, T, thr):
    d = "Up" if score >= thr else "Down" if score <= -thr else "Sideways"
    if d == "Sideways":
        return "Sideways / no clear trend"
    strong = abs(score) >= 0.5 and T >= 0.5
    if T < 0.3:
        return f"Weak {d.lower()} bias inside chop"
    return f"{'Strong' if strong else 'Moderate'} {d.lower()}trend"


def drop_forming(df, tf, now):
    tsec = TF_SEC[tf]
    if now is not None and len(df) and float(df.time.iloc[-1]) + tsec > float(now):
        return df.iloc[:-1].reset_index(drop=True), True
    return df, False


def analyse(df, tf, now=None, user=None):
    """Latest-bar trend report (closed candles only) + overlay geometry for the chart."""
    df, forming = drop_forming(df, tf, now)
    S = series(df, tf, user)
    C, st, tl = S["C"], S["st"], S["tl"]
    n = len(df)
    t = df.time.to_numpy(dtype=float)
    last = n - 1
    score, T = float(S["score"][last]), float(S["trendiness"][last])
    comps = {k: round(float(v[last]), 3) for k, v in S["comps"].items()}
    act = [v for v in comps.values() if abs(v) >= 0.1]
    agree = sum(1 for v in act if score != 0 and np.sign(v) == np.sign(score))
    conf = round(min(1.0, abs(score) / 0.5) * (agree / len(act) if act else 0.0), 2)
    c = S["c"]
    piv = st["pivots"]
    tim = lambda i: int(t[i])
    pivots = [{"t": tim(p["i"]), "p": p["p"], "kind": p["kind"], "conf_t": tim(p["conf"])} for p in piv[-14:]]
    if st["tentative"]:
        p = st["tentative"]
        pivots.append({"t": tim(p["i"]), "p": p["p"], "kind": p["kind"], "tentative": True})
    events = [{"t": tim(e["i"]), "type": e["type"], "dir": e["dir"], "lvl": e["lvl"]} for e in st["events"][-6:]]
    lines = None
    if tl["last"]:
        L = tl["last"]
        mk = lambda ln: {"t1": tim(L["t0"] + ln["a"]), "p1": float(math.exp(ln["icpt"] + ln["slope"] * ln["a"])),
                         "t2": tim(L["t0"] + ln["b"]), "p2": float(math.exp(ln["icpt"] + ln["slope"] * ln["b"])),
                         "slope_pct_per_bar": round(100 * (math.exp(ln["slope"]) - 1), 4), "touches": ln["touches"]}
        lines = {"support": mk(L["S"]), "resistance": mk(L["R"]), "support_now": L["sup_now"], "res_now": L["res_now"],
                 "position": None if math.isnan(tl["pos"][last]) else round(float(tl["pos"][last]), 2),
                 "break": int(tl["brk"][last]) if not math.isnan(tl["brk"][last]) else 0}
    fib = fib_levels(piv, C)
    if fib:
        fib["from_t"], fib["to_t"] = tim(fib.pop("from_i")), tim(fib.pop("to_i"))
    zones = [{**z, "t": tim(z["i"])} for z in fvg_zones(S["o"], S["h"], S["l"], c, S["atr"], int(C["fvg_keep"]),
                                                       float(C["fvg_min_atr"]), int(C["fvg_max_age"]))]
    for z in zones:
        z.pop("i")
    lv = st["levels"]
    inval = None
    if score >= C["thr"] and lv["swing_low"]:
        inval = {"side": "long", "level": lv["swing_low"], "rule": "uptrend structure is invalid on a CLOSE below the last confirmed swing low"}
    elif score <= -C["thr"] and lv["swing_high"]:
        inval = {"side": "short", "level": lv["swing_high"], "rule": "downtrend structure is invalid on a CLOSE above the last confirmed swing high"}
    wins = C["windows"]
    zl = {f"z{int(w)}": round(float(z[last]), 2) for w, z in zip(wins, S["zs"])}
    bars_since = None
    if st["events"]:
        bars_since = int(last - st["events"][-1]["i"])
    return {
        "tf": tf, "bars_used": n, "last_closed_t": int(t[last]), "forming_bar_ignored": forming,
        "score": round(score, 3), "label": _label(score, T, C["thr"]),
        "direction": "Up" if score >= C["thr"] else "Down" if score <= -C["thr"] else "Sideways",
        "confidence": conf, "conf_label": "high" if conf >= 0.66 else "medium" if conf >= 0.33 else "low",
        "trendiness": round(T, 2), "components": comps,
        "structure": {"state": "HH+HL" if st["struct"][last] >= 1 else "LH+LL" if st["struct"][last] <= -1 else "mixed",
                      "bias_by_last_break": {1: "bullish", -1: "bearish", 0: "none"}[int(st["bias"][last])],
                      "last_event": events[-1] if events else None, "bars_since_last_break": bars_since,
                      "swing_high": lv["swing_high"], "swing_low": lv["swing_low"]},
        "momentum_z": zl,
        "quality": {"efficiency_ratio": round(float(S["er"][last]), 2), "adx": round(float(S["adx"][last]), 1),
                    "variance_ratio": None if math.isnan(S["vr"][last]) else round(float(S["vr"][last]), 2),
                    "variance_ratio_z": None if math.isnan(S["vr_z"][last]) else round(float(S["vr_z"][last]), 2),
                    "reading": "trending (positive autocorrelation)" if S["vr_z"][last] > 1.64 else
                               "mean-reverting" if S["vr_z"][last] < -1.64 else "indistinguishable from a random walk"},
        "atr_pct": round(100 * float(S["atr"][last] / c[last]), 2), "close": float(c[last]),
        "invalidation": inval,
        "overlay": {"pivots": pivots, "events": events, "lines": lines, "fib": fib, "fvg": zones},
        "_series": S,
        "notes": ["Closed candles only; confirmed pivots appear a few bars after the extreme (no look-ahead).",
                  "This is a description of the chart, not a forecast: check 'edge' for what the history says."],
    }


def combine(rows, weights, thr=0.20):
    """Multi-timeframe: weighted score + plain-language relation between the higher and lower timeframe."""
    ok = [r for r in rows if "score" in r]
    tw = sum(float(weights.get(r["tf"], 0)) for r in ok)
    comb = sum(float(weights.get(r["tf"], 0)) * r["score"] for r in ok) / tw if tw > 0 else None
    dirs = {r["tf"]: r["direction"] for r in ok}
    vals = list(dirs.values())
    if vals and all(v == "Up" for v in vals):
        rel = "aligned up"
    elif vals and all(v == "Down" for v in vals):
        rel = "aligned down"
    else:
        hi_tf = max(dirs, key=lambda k: TF_SEC[k]) if dirs else None
        lo_tf = min(dirs, key=lambda k: TF_SEC[k]) if dirs else None
        if hi_tf and dirs[hi_tf] == "Up" and dirs[lo_tf] == "Down":
            rel = f"pullback inside a {hi_tf} uptrend (a {lo_tf} CHoCH back up would end it)"
        elif hi_tf and dirs[hi_tf] == "Down" and dirs[lo_tf] == "Up":
            rel = f"bounce inside a {hi_tf} downtrend"
        else:
            rel = "mixed - no confluence"
    return {"combined": None if comb is None else round(comb, 3),
            "combined_direction": None if comb is None else ("Up" if comb >= thr else "Down" if comb <= -thr else "Sideways"),
            "relation": rel}
