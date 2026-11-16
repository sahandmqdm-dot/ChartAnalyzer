#!/usr/bin/env python3
"""
apply_patch_v2.py - Ramzinex corrections on top of ChartAnalyzer v3.   Run from the ChartAnalyzer folder:

    python apply_patch_v2.py            apply (idempotent: a second run changes nothing)
    python apply_patch_v2.py --dry      only check that every patch point is found

What it does
  app.py          get_fee + fee_by_market, filter_restricted_bars / is_restricted, proj_param (per-timeframe warm/lambda/block),
                  _rolling_mad (sliding_window_view) for CCI, one fee convention in backtest (round-trip = 2 x per-side),
                  symbol passed to forecast / backtest / make_gate, edge_stats(mask=...)
  config.json     fee_by_market, restricted_hours, projection with per-timeframe warm / lambda / block
  history_check.py  ThreadPoolExecutor over (symbol, tf), cached compute()/vol_state(), per-symbol fee, restricted-bar mask;
                  the --trend mode and _trend_scores are NOT touched
  trend_engine.py NEVER touched (sha256 checked before and after)
  finally runs tests/test_trend.py (+ tests/test_ramzinex_v2.py written by this script)

Every file is backed up once as <name>.bak_v2 before the first change.
"""
import hashlib, json, os, re, shutil, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
P = lambda *a: os.path.join(HERE, *a)
DRY = "--dry" in sys.argv


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def read(path):
    return open(path, encoding="utf-8").read()


class Patcher:
    def __init__(self, path):
        self.path, self.s, self.changed, self.skipped = path, read(path), 0, 0

    def rep(self, old, new, marker=None):
        """Replace `old` (must occur exactly once). Skipped when `marker` (text only the patched file has) is present."""
        if marker and marker in self.s:
            self.skipped += 1
            return
        n = self.s.count(old)
        if n != 1:
            sys.exit(f"[{os.path.basename(self.path)}] patch point found {n}x (expected 1):\n    {old[:90]!r}\n"
                     f"The file differs from ChartAnalyzer v3 - nothing was written.")
        self.s = self.s.replace(old, new)
        self.changed += 1

    def save(self):
        if DRY or not self.changed:
            return
        bak = self.path + ".bak_v2"
        if not os.path.exists(bak):
            shutil.copy2(self.path, bak)
        open(self.path, "w", encoding="utf-8").write(self.s)


# ============================================================================ trend_engine.py guard
TE = P("trend_engine.py")
te_hash = sha(TE)

# ============================================================================ app.py
a = Patcher(P("app.py"))
a.rep("import numpy as np\n", "import numpy as np\nfrom numpy.lib.stride_tricks import sliding_window_view\n",
      marker="sliding_window_view")

a.rep('''    "fee_roundtrip": 0.004,   # PLACEHOLDER: real round-trip fee + spread as a fraction. Set it in config.json.
''', '''    "fee_roundtrip": 0.004,   # fallback ROUND-TRIP fee (fraction) when the symbol's market is unknown
    # ROUND-TRIP fee per quote market (buy + sell together). tether = *USDT symbols, rial = *IRR / *IRT symbols.
    "fee_by_market": {"tether": 0.001, "rial": 0.004},
    # entries inside this local-time window are not tradable -> excluded from edge statistics, gate says NO_TRADE.
    # Applied only to timeframes <= tf_max_min (a 4h/12h/1d candle spans the window, so it cannot be filtered by open time).
    "restricted_hours": {"enabled": False, "start": "23:30", "end": "11:30", "tz_offset_min": 210, "tf_max_min": 60},
''', marker='"fee_by_market"')

HELPERS = '''

# ---------------------------------------------------------------- Ramzinex helpers (apply_patch_v2)
def get_fee(C, symbol=""):
    """ROUND-TRIP fee (fraction of price) for the symbol's quote market. Per side = get_fee / 2."""
    s = str(symbol or "").upper().split(":")[-1]
    mk = "rial" if s.endswith(("IRR", "IRT")) else "tether" if s.endswith("USDT") else None
    tbl = C.get("fee_by_market") or {}
    v = tbl.get(mk) if mk else None
    return float(v if v is not None else C.get("fee_roundtrip", 0.004))


def proj_param(pc, key, tf, default=None):
    """projection[key] may be a number or {"default": x, "1h": y, ...} (per-timeframe override)."""
    v = (pc or {}).get(key, default)
    if isinstance(v, dict):
        v = v[tf] if tf in v else v.get("default", default)
    return default if v is None else v


def _hhmm(x):
    h, m = str(x).split(":")
    return int(h) * 60 + int(m)


def _restricted_cfg(C, tf):
    r = C.get("restricted_hours")
    if not isinstance(r, dict) or not r.get("enabled", False) or tf not in TF_MIN:
        return None
    if TF_MIN[tf] > int(r.get("tf_max_min", 60)):
        return None
    return _hhmm(r.get("start", "23:30")), _hhmm(r.get("end", "11:30")), int(r.get("tz_offset_min", 210))


def _in_window(minute_of_day, s, e):
    return (minute_of_day >= s) | (minute_of_day < e) if s > e else (minute_of_day >= s) & (minute_of_day < e)


def filter_restricted_bars(df, C, tf):
    """Boolean array aligned to df rows: True = the bar may be used as an ENTRY (open time outside restricted hours).
    All True when restricted_hours is disabled or the timeframe is longer than tf_max_min."""
    cfg_ = _restricted_cfg(C, tf)
    n = len(df)
    if cfg_ is None:
        return np.ones(n, dtype=bool)
    s, e, off = cfg_
    mod = ((df.time.to_numpy(dtype=float) + off * 60) // 60) % 1440
    return ~_in_window(mod, s, e)


def is_restricted(ts, C, tf):
    cfg_ = _restricted_cfg(C, tf)
    if cfg_ is None:
        return False
    s, e, off = cfg_
    return bool(_in_window(((float(ts) + off * 60) // 60) % 1440, s, e))
'''
a.rep('''

def params_for(tf):''', HELPERS + '''

def params_for(tf):''', marker="def get_fee(")

# CCI: sliding_window_view instead of rolling().apply(lambda)
a.rep('''@reg("cci", "extremes beyond +-100")''', '''def _rolling_mad(x, n):
    """Rolling mean absolute deviation (same result as rolling(n).apply(mean |a-mean(a)|), but vectorised)."""
    a = np.asarray(x, dtype=float)
    out = np.full(len(a), np.nan)
    if len(a) >= n:
        w = sliding_window_view(a, n)
        out[n - 1:] = np.abs(w - w.mean(axis=1, keepdims=True)).mean(axis=1)
    return pd.Series(out, index=getattr(x, "index", None))


@reg("cci", "extremes beyond +-100")''', marker="def _rolling_mad")
a.rep("    mad = X.tp.rolling(n).apply(lambda a: np.abs(a - a.mean()).mean(), raw=True)\n",
      "    mad = _rolling_mad(X.tp, n)\n", marker="mad = _rolling_mad(X.tp, n)")

# backtest: one fee convention (fee = PER SIDE; round trip = 2 x fee everywhere, default from get_fee/2)
a.rep('def backtest(df, tf, fee=0.002, start=120):', 'def backtest(df, tf, fee=None, start=120, symbol=""):',
      marker='def backtest(df, tf, fee=None')
a.rep('''    fee = max(0.0, min(float(fee), 0.05))
''', '''    # fee is PER SIDE. Default = half of the market's round-trip fee, so one round trip costs exactly get_fee(C, symbol).
    fee = get_fee(C, symbol) / 2.0 if fee is None else float(fee)
    fee = max(0.0, min(fee, 0.05))
''', marker="Default = half of the market's round-trip fee")
a.rep('''    eq = np.exp(logeq)
''', '''    eq = np.exp(logeq)
    if len(w) and w[-1]:
        eq[-1] *= (1 - fee)          # a position still open is marked net of its exit fee (same 2*fee as the trade list)
''', marker="marked net of its exit fee")
a.rep('''"max_dd_pct": round(100 * dd, 2), "fee_per_side": fee,''',
      '''"max_dd_pct": round(100 * dd, 2), "fee_per_side": fee, "fee_roundtrip": round(2 * fee, 6),''',
      marker='"fee_roundtrip": round(2 * fee, 6)')

# edge_stats: optional tradable-bar mask
a.rep('def edge_stats(S, groups, c, sigma, H, warm):', 'def edge_stats(S, groups, c, sigma, H, warm, mask=None):',
      marker="def edge_stats(S, groups, c, sigma, H, warm, mask=None)")
a.rep('''    ok = np.isfinite(fwd)
    idx, fwd = idx[ok], fwd[ok]''', '''    ok = np.isfinite(fwd)
    if mask is not None:
        ok &= np.asarray(mask, dtype=bool)[idx]      # restricted-hours entries are not tradable -> not counted
    idx, fwd = idx[ok], fwd[ok]''', marker="restricted-hours entries are not tradable")

# forecast: per-tf params, symbol, mask
a.rep('def forecast(df, tf, hours, paths, C):', 'def forecast(df, tf, hours, paths, C, symbol=""):',
      marker='def forecast(df, tf, hours, paths, C, symbol="")')
a.rep('    warm, L, block = int(pc["warm"]), int(pc["lookback"]), int(pc["block"])\n',
      '    warm, L = int(proj_param(pc, "warm", tf, 120)), int(proj_param(pc, "lookback", tf, 300))\n'
      '    block = int(proj_param(pc, "block", tf, 5))\n', marker='block = int(proj_param(pc, "block", tf, 5))')
a.rep('    r, vs, z = vol_state(c, float(pc["lambda"]))\n    sigma = np.concatenate([[np.nan], np.sqrt(vs)])\n    edge = edge_stats(res["S"].values, {g: s.values for g, s in res["gs"].items()}, c, sigma, H, warm)',
      '    r, vs, z = vol_state(c, float(proj_param(pc, "lambda", tf, 0.94)))\n    sigma = np.concatenate([[np.nan], np.sqrt(vs)])\n'
      '    tradable = filter_restricted_bars(df, C, tf)\n'
      '    edge = edge_stats(res["S"].values, {g: s.values for g, s in res["gs"].items()}, c, sigma, H, warm,\n'
      '                      mask=None if tradable.all() else tradable)',
      marker="tradable = filter_restricted_bars(df, C, tf)")
a.rep('    summary["gate"] = make_gate(summary, plan, last, C)',
      '    summary["gate"] = make_gate(summary, plan, last, C, symbol)', marker="make_gate(summary, plan, last, C, symbol)")

# make_gate: per-symbol fee + restricted-hours rule
a.rep('''def make_gate(summary, plan, last, C):
    g = C.get("gate") or {}
    kw = dict(fee_rt=float(C.get("fee_roundtrip", 0.004)),''', '''def make_gate(summary, plan, last, C, symbol=""):
    d = _make_gate(summary, plan, last, C, symbol)
    tf = summary.get("tf")
    if tf in TF_MIN and is_restricted(time.time(), C, tf):
        r = C["restricted_hours"]
        d = dict(d, state="NO_TRADE", confidence_cap="low",
                 reasons=list(d.get("reasons") or []) + [f"inside restricted trading hours ({r['start']}-{r['end']}, UTC{int(r.get('tz_offset_min', 210)) / 60:+g})"])
    return d


def _make_gate(summary, plan, last, C, symbol=""):
    g = C.get("gate") or {}
    kw = dict(fee_rt=get_fee(C, symbol),''', marker="def _make_gate(")

# journal / api callers
a.rep('    out = forecast(df, tf, float(w.get("hours", 12)), int(C["projection"]["paths"]), C)',
      '    out = forecast(df, tf, float(w.get("hours", 12)), int(C["projection"]["paths"]), C, symbol=symbol)',
      marker="int(C[\"projection\"][\"paths\"]), C, symbol=symbol)")
a.rep('    c, lam = S["c"], float(pc["lambda"])', '    c, lam = S["c"], float(proj_param(pc, "lambda", tf, 0.94))', marker='proj_param(pc, "lambda", tf, 0.94))\n    r, vs, z = vol_state(c, lam)')
a.rep('    warm = int(pc["warm"])\n    try:\n        a = edge_stats(S["score"]', '    warm = int(proj_param(pc, "warm", tf, 120))\n    try:\n        a = edge_stats(S["score"]',
      marker='warm = int(proj_param(pc, "warm", tf, 120))\n    try:')
a.rep('def api_backtest(source: str = "ramzinex", symbol: str = "USDTIRR", tf: str = "1h", n: int = 800,\n                 fee: float = 0.002,',
      'def api_backtest(source: str = "ramzinex", symbol: str = "USDTIRR", tf: str = "1h", n: int = 800,\n                 fee: float = -1.0,',
      marker="fee: float = -1.0")
a.rep('        return backtest(load_cached(source, symbol, tf, n, exchange, udf_url, csv_path), tf, fee)',
      '        return backtest(load_cached(source, symbol, tf, n, exchange, udf_url, csv_path), tf,\n'
      '                        None if fee < 0 else fee, symbol=symbol)', marker="None if fee < 0 else fee, symbol=symbol)")
a.rep('        return forecast(df, tf, max(0.25, min(float(hours), 72.0)), max(100, min(int(paths), 2000)), C)',
      '        return forecast(df, tf, max(0.25, min(float(hours), 72.0)), max(100, min(int(paths), 2000)), C, symbol=symbol)',
      marker="max(100, min(int(paths), 2000)), C, symbol=symbol)")

# ============================================================================ history_check.py (NOT touching --trend / _trend_scores)
h = Patcher(P("history_check.py"))
h.rep("import csv, math, os, sys, time\n", "import csv, math, os, sys, time\nfrom concurrent.futures import ThreadPoolExecutor\n",
      marker="ThreadPoolExecutor")
h.rep('''def _edge(S, c, sigma, H, warm):
    try:
        return app.edge_stats(S, {}, c, sigma, H, warm)["aggregate"]''', '''def _edge(S, c, sigma, H, warm, mask=None):
    try:
        return app.edge_stats(S, {}, c, sigma, H, warm, mask=mask)["aggregate"]''', marker="def _edge(S, c, sigma, H, warm, mask=None)")

s0 = h.s
i, j = s0.find("def analyze("), s0.find("def _csv_loader(")
if "cache=None" not in s0:
    if i < 0 or j < 0 or j < i:
        sys.exit("[history_check.py] analyze()/run() block not found - nothing was written.")
    NEW_BLOCK = '''def analyze(df, tf, hours, C, kind="engine", symbol="", cache=None):
    """cache: per (symbol, tf) dict - compute() and vol_state() are done once, not once per horizon."""
    cache = {} if cache is None else cache
    if kind in ("trend", "naive"):
        df, S_override = _trend_scores(df, tf, C, kind)
    else:
        S_override = None
    c = df.close.values
    n = len(c)
    pc = C["projection"]
    H = int(min(pc["max_bars"], max(1, round(hours * 60 / app.TF_MIN[tf]))))
    warm = int(app.proj_param(pc, "warm", tf, 120))
    if n < warm + H + 200:
        return None
    if S_override is not None:
        S = S_override
    else:
        if "S" not in cache:
            cache["S"] = app.compute(df, tf)["S"].values
        S = cache["S"]
    lam = float(app.proj_param(pc, "lambda", tf, 0.94))
    vk = ("sigma", lam, n)
    if vk not in cache:
        r, vs, z = app.vol_state(c, lam)
        cache[vk] = np.concatenate([[np.nan], np.sqrt(vs)])
    sigma = cache[vk]
    ok_bars = app.filter_restricted_bars(df, C, tf)          # restricted hours: such entries are not tradable
    mask = None if ok_bars.all() else ok_bars
    full = _edge(S, c, sigma, H, warm, mask)
    m = n // 2
    h1 = _edge(S[:m], c[:m], sigma[:m], H, warm, None if mask is None else mask[:m])
    h2 = _edge(S[m:], c[m:], sigma[m:], H, 0, None if mask is None else mask[m:])
    if full is None:
        return None

    thr = float(C["verdict_threshold"])
    fee = app.get_fee(C, symbol)                              # ROUND-TRIP fee of this symbol's market
    idx = np.arange(warm, n - H)
    if mask is not None:
        idx = idx[mask[idx]]
    ret = c[idx + H] / c[idx] - 1
    bull = S[idx] >= thr
    row = {"H_bars": H, "n_bars": n, "n_eff": full.get("n_eff"), "ic": full["ic"], "t": full["t_eff"],
           "hit_pct": full["hit_pct"], "t_half1": None if h1 is None else h1["t_eff"],
           "t_half2": None if h2 is None else h2["t_eff"], "bull_n": int(bull.sum()),
           "bull_mean_pct": None, "excess_pct": None, "net_excess_pct": None, "t_excess": None, "fee_rt": fee}
    if bull.sum() >= 20:
        mb, ma = float(ret[bull].mean()), float(ret.mean())
        neb, nea = max(bull.sum() / H, 1.0), max(len(ret) / H, 1.0)
        se = float(ret.std()) * math.sqrt(1 / neb + 1 / nea)
        row.update(bull_mean_pct=round(100 * mb, 3), excess_pct=round(100 * (mb - ma), 3),
                   net_excess_pct=round(100 * (mb - ma - fee), 3),
                   t_excess=round((mb - ma) / se, 2) if se > 0 else None)
    return row


def run(symbols, grid, load=None, sleep=0.5, kinds=("engine",), workers=3):
    """One job per (symbol, timeframe): load once, compute once, then every horizon x kind. Jobs run in a thread pool."""
    load = load or (lambda s, tf: app.load(SOURCE, s, tf, BARS))
    C = app.conf()
    crit = NormalDist().inv_cdf(1 - 0.025 / max(1, len(symbols) * len(grid) * len(kinds)))
    pairs = []
    for sym in symbols:
        for tf, _ in grid:
            if (sym, tf) not in pairs:
                pairs.append((sym, tf))

    def job(pair):
        sym, tf = pair
        rows = []
        try:
            df = load(sym, tf)
            time.sleep(sleep)
        except Exception as e:
            print(f"{sym} {tf}: {type(e).__name__}: {str(e)[:120]}")
            return rows
        cache = {}
        for g_tf, hours in grid:
            if g_tf != tf:
                continue
            for kind in kinds:
                try:
                    row = analyze(df, tf, hours, C, kind, symbol=sym, cache=cache)
                except Exception as e:
                    print(f"{sym} {tf} {hours}h {kind}: {type(e).__name__}: {str(e)[:120]}")
                    continue
                if row is None:
                    print(f"{sym} {tf} {hours}h {kind}: not enough history")
                    continue
                row.update(symbol=sym, tf=tf, hours=hours, kind=kind)
                row["PASS"] = bool(row["t"] >= crit and (row["t_half1"] or 0) > 0 and (row["t_half2"] or 0) > 0
                                   and (row["net_excess_pct"] or 0) > 0)
                rows.append(row)
        return rows

    with ThreadPoolExecutor(max_workers=max(1, int(workers))) as ex:
        out = [r for rows in ex.map(job, pairs) for r in rows]
    return out, crit


'''
    h.s = s0[:i] + NEW_BLOCK + s0[j:]
    h.changed += 1
else:
    h.skipped += 1
h.rep('''    cols = ["symbol", "tf", "hours", "kind", "n_bars", "n_eff", "ic", "t", "hit_pct", "t_half1", "t_half2",
            "bull_n", "bull_mean_pct", "excess_pct", "net_excess_pct", "t_excess", "PASS"]''',
      '''    cols = ["symbol", "tf", "hours", "kind", "n_bars", "n_eff", "ic", "t", "hit_pct", "t_half1", "t_half2",
            "bull_n", "bull_mean_pct", "excess_pct", "fee_rt", "net_excess_pct", "t_excess", "PASS"]''',
      marker='"excess_pct", "fee_rt", "net_excess_pct"')

# ============================================================================ config.json
cfgp = P("config.json")
cfg = json.load(open(cfgp, encoding="utf-8"))
before = json.dumps(cfg, sort_keys=True)
cfg.setdefault("fee_by_market", {"tether": 0.001, "rial": 0.004})
cfg.setdefault("restricted_hours", {"enabled": True, "start": "23:30", "end": "11:30", "tz_offset_min": 210, "tf_max_min": 60,
                                    "_note": "Tehran time (UTC+3:30, no DST). Only timeframes <= tf_max_min are filtered."})
pr = cfg.setdefault("projection", {})
for k, v in (("warm", {"default": 120, "1h": 200, "4h": 200, "12h": 120, "1d": 120}),
             ("lambda", {"default": 0.94, "15m": 0.98, "1h": 0.97, "4h": 0.96, "12h": 0.95, "1d": 0.94}),
             ("block", {"default": 5, "15m": 8, "1h": 6, "4h": 5, "12h": 4, "1d": 3})):
    if not isinstance(pr.get(k), dict):
        pr[k] = v
pr.setdefault("_per_tf_note", "warm/lambda/block accept a number or {default, <tf>: value}. Values are heuristics, NOT tuned: check cone calibration.")
cfg_changed = json.dumps(cfg, sort_keys=True) != before

# ============================================================================ tests file
TESTS = '''"""Tests for apply_patch_v2 (Ramzinex corrections). Synthetic data only.  Run: python tests/test_ramzinex_v2.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np, pandas as pd
import app


def test_get_fee():
    C = {"fee_roundtrip": 0.004, "fee_by_market": {"tether": 0.001, "rial": 0.004}}
    assert app.get_fee(C, "BTCUSDT") == 0.001 and app.get_fee(C, "ramzinex:BTCUSDT") == 0.001
    assert app.get_fee(C, "USDTIRR") == 0.004 and app.get_fee(C, "BTCIRR") == 0.004   # USDTIRR is a RIAL market
    C["fee_roundtrip"] = 0.007
    assert app.get_fee(C, "weird.csv") == 0.007


def test_proj_param():
    pc = {"warm": {"default": 120, "1d": 100}, "lambda": 0.94}
    assert app.proj_param(pc, "warm", "1d", 1) == 100 and app.proj_param(pc, "warm", "1h", 1) == 120
    assert app.proj_param(pc, "lambda", "4h", 0.5) == 0.94 and app.proj_param(pc, "block", "4h", 5) == 5


def test_rolling_mad_matches_old():
    x = pd.Series(np.random.default_rng(0).normal(size=300).cumsum())
    old = x.rolling(20).apply(lambda a: np.abs(a - a.mean()).mean(), raw=True)
    new = app._rolling_mad(x, 20)
    assert np.allclose(old.to_numpy(), new.to_numpy(), equal_nan=True)


def test_restricted_window_wraps_midnight():
    C = {"restricted_hours": {"enabled": True, "start": "23:30", "end": "11:30", "tz_offset_min": 210, "tf_max_min": 60}}
    # Tehran 12:00 = 08:30 UTC (allowed), Tehran 00:00 = 20:30 UTC (restricted), Tehran 11:30 = 08:00 UTC (allowed)
    day = 86400 * 20000
    t = [day + 8 * 3600 + 1800, day + 20 * 3600 + 1800, day + 8 * 3600, day + 20 * 3600]
    df = pd.DataFrame({"time": t})
    assert list(app.filter_restricted_bars(df, C, "1h")) == [True, False, True, False]  # 11:30 Tehran = allowed (end is exclusive), 23:30 Tehran = restricted (start is inclusive)
    assert app.filter_restricted_bars(df, C, "4h").all()          # longer timeframes are never filtered
    assert app.is_restricted(day + 20 * 3600 + 1800, C, "1h") and not app.is_restricted(day + 8 * 3600 + 1800, C, "1h")


def test_backtest_one_fee_convention():
    r = np.random.default_rng(2)
    c = 100 * np.exp(np.cumsum(r.normal(0.001, 0.01, 600)))
    df = pd.DataFrame({"time": np.arange(600) * 3600.0, "open": c, "high": c * 1.002, "low": c * 0.998, "close": c, "volume": 1.0})
    out = app.backtest(df, "1h", symbol="BTCUSDT")
    assert abs(out["fee_roundtrip"] - app.get_fee(app.conf(), "BTCUSDT")) < 1e-9 and abs(out["fee_per_side"] * 2 - out["fee_roundtrip"]) < 1e-9


if __name__ == "__main__":
    for f in (test_get_fee, test_proj_param, test_rolling_mad_matches_old, test_restricted_window_wraps_midnight,
              test_backtest_one_fee_convention):
        f(); print("ok", f.__name__)
'''

# ============================================================================ write
print(f"app.py: {a.changed} patched, {a.skipped} already applied | history_check.py: {h.changed} patched, {h.skipped} already applied | "
      f"config.json: {'updated' if cfg_changed else 'already up to date'}")
if DRY:
    print("--dry: nothing written")
    sys.exit(0)
a.save()
h.save()
if cfg_changed:
    if not os.path.exists(cfgp + ".bak_v2"):
        shutil.copy2(cfgp, cfgp + ".bak_v2")
    json.dump(cfg, open(cfgp, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
os.makedirs(P("tests"), exist_ok=True)
open(P("tests", "test_ramzinex_v2.py"), "w", encoding="utf-8").write(TESTS)

# ============================================================================ guard + tests
if sha(TE) != te_hash:
    sys.exit("trend_engine.py changed during the patch - this must never happen. Restore it and report.")
print("trend_engine.py untouched, sha256", te_hash[:16], "...")
rc = 0
for t in ("test_trend.py", "test_ramzinex_v2.py"):
    r = subprocess.run([sys.executable, "-B", P("tests", t)], cwd=HERE, capture_output=True, text=True)
    print(f"--- tests/{t}: {'PASSED' if r.returncode == 0 else 'FAILED'}")
    print((r.stdout + r.stderr).strip()[-1500:])
    rc |= r.returncode
sys.exit(rc)
