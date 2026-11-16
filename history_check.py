"""
history_check.py - replay the engine on up to 5000 past candles, per symbol x horizon.
Run from the ChartAnalyzer folder (next to app.py), app.py may stay running:
    python history_check.py
Output: table in console + history_check.csv

Trend mode (12h / 1d trend structure from trend_engine.py, with a naive-momentum control):
    python history_check.py --trend
    python history_check.py --trend --csv my_daily.csv --tf 1d        (offline: your own OHLC csv)
Output: history_check_trend.csv.  A trend row only matters if it beats the 'naive' row, not merely if t > 0.

Everything is causal: the score at bar t uses only data up to t (app.compute is causal).
This is HYPOTHESIS GENERATION (in-sample w.r.t. our own eyes). The live journal stays the judge.
"""
import csv, math, os, sys, time
from concurrent.futures import ThreadPoolExecutor
from statistics import NormalDist
import numpy as np
import app
import trend_engine as TE

SOURCE = "ramzinex"
BARS = 5000


def _edge(S, c, sigma, H, warm, mask=None):
    try:
        return app.edge_stats(S, {}, c, sigma, H, warm, mask=mask)["aggregate"]
    except ValueError:
        return None


def _trend_scores(df, tf, C, kind):
    df, _ = TE.drop_forming(df, tf, time.time())
    S = TE.series(df, tf, C.get("trend"))
    if kind == "naive":
        return df, np.tanh(S["zs"][len(S["zs"]) // 2] / 1.5)
    return df, S["score"]


def analyze(df, tf, hours, C, kind="engine", symbol="", cache=None):
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


def _csv_loader(path, tf):
    import pandas as pd
    d = pd.read_csv(path)
    d.columns = [str(x).strip().lower() for x in d.columns]
    if "date" in d.columns and "time" not in d.columns:
        d["time"] = pd.to_datetime(d["date"]).map(lambda x: x.timestamp())
    if "volume" not in d.columns:
        d["volume"] = 1.0
    d = d[["time", "open", "high", "low", "close", "volume"]].astype(float).dropna().sort_values("time")
    d = d.drop_duplicates("time").reset_index(drop=True)
    if tf == "12h":      # csv holds 1h/4h candles -> build 12h on the UTC grid
        d = TE.resample_ohlcv(d, 12, 0, now=time.time())
    return d


def main():
    C = app.conf()
    trend = "--trend" in sys.argv
    argv = sys.argv
    csv_path = argv[argv.index("--csv") + 1] if "--csv" in argv else None
    tf_csv = argv[argv.index("--tf") + 1] if "--tf" in argv else "1d"
    if trend:
        grid = [tuple(g) for g in (C.get("trend_grid") or [["12h", 72], ["12h", 168], ["1d", 72], ["1d", 168]])]
        kinds = ("trend", "naive")
    else:
        grid = [tuple(g) for g in (C.get("journal_grid") or [])]
        kinds = ("engine",)
    symbols = C.get("journal_symbols") or ["USDTIRR"]
    load = None
    if csv_path:
        symbols = [os.path.basename(csv_path)]
        grid = [g for g in grid if g[0] == tf_csv] if trend else grid
        load = lambda s_, tf: _csv_loader(csv_path, tf)
    out, crit = run(symbols, grid, load=load, kinds=kinds, sleep=0 if csv_path else 0.5)
    cols = ["symbol", "tf", "hours", "kind", "n_bars", "n_eff", "ic", "t", "hit_pct", "t_half1", "t_half2",
            "bull_n", "bull_mean_pct", "excess_pct", "fee_rt", "net_excess_pct", "t_excess", "PASS"]
    print(f"\nBonferroni critical t for {len(out)} tests: {crit:.2f}\n")
    print(" | ".join(cols))
    for r in out:
        print(" | ".join(str(r.get(k)) for k in cols))
    with open("history_check_trend.csv" if trend else "history_check.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader(); w.writerows(out)
    print("\nPASS rows:", [f"{r['symbol']}/{r['tf']}/{r['hours']}h" for r in out if r["PASS"]] or "none")


if __name__ == "__main__":
    main()
