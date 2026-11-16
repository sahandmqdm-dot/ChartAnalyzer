#!/usr/bin/env python3
"""Download long OHLCV history from the Ramzinex UDF endpoint (same one ChartAnalyzer uses)
and save it as CSV for ChartAnalyzer's `csv` source.

Usage (run inside the ChartAnalyzer folder, next to app.py):
    python download_history.py USDTIRR 4h 20000
    python download_history.py BTCIRR 1d 3000
Output: data/<SYMBOL>_<TF>.csv   (columns: time,open,high,low,close,volume)

Read-only: public market data, no API key, no orders.
"""
import os
import sys
import time

import pandas as pd
import requests

URL = "https://publicapi.ramzinex.ir/exchange/api/v1.0/exchange/chart/tv/v2.0/history"
TF_MIN = {"15m": 15, "1h": 60, "4h": 240, "1d": 1440}   # 12h: build from 4h in the analyzer
CHUNK = 1000                                             # bars per request (conservative)


def fetch_chunk(symbol, res_min, t_from, t_to):
    p = {"symbol": symbol, "resolution": res_min, "from": int(t_from), "to": int(t_to)}
    r = requests.get(URL, params=p, timeout=20, allow_redirects=False)
    r.raise_for_status()
    j = r.json()
    if j.get("s") != "ok":
        return pd.DataFrame()           # "no_data" for empty windows
    return pd.DataFrame({"time": j["t"], "open": j["o"], "high": j["h"], "low": j["l"],
                         "close": j["c"], "volume": j.get("v", [0] * len(j["t"]))})


def main():
    if len(sys.argv) != 4 or sys.argv[2] not in TF_MIN:
        sys.exit(__doc__)
    symbol, tf, total = sys.argv[1].upper(), sys.argv[2], int(sys.argv[3])
    res = TF_MIN[tf]
    step = res * 60 * CHUNK
    end = int(time.time())
    start = end - total * res * 60
    parts, t = [], start
    while t < end:
        df = fetch_chunk(symbol, res, t, min(t + step, end))
        if len(df):
            parts.append(df)
        print(f"  {time.strftime('%Y-%m-%d', time.gmtime(t))}  +{len(df)} bars")
        t += step
        time.sleep(0.5)                 # be polite to the public API
    if not parts:
        sys.exit("no data returned - check the symbol name / network")
    df = pd.concat(parts).drop_duplicates("time").sort_values("time")
    # drop the still-forming last candle: CSV files are NOT auto-filtered by the analyzer
    df = df[df["time"] + res * 60 <= end].reset_index(drop=True)
    gaps = (df["time"].diff().dropna() > res * 60 * 1.5).sum()
    os.makedirs("data", exist_ok=True)
    out = os.path.join("data", f"{symbol}_{tf}.csv")
    df.to_csv(out, index=False)
    print(f"saved {len(df)} bars -> {out} | gaps (missing bars): {gaps}")


if __name__ == "__main__":
    main()
