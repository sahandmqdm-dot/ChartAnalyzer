"""
ChartAnalyzer V7.2.0 - Reproducible Research & OOS Integrity.
Requires only:  pip install pandas numpy requests      (ccxt optional)

PC / local only :  python app.py                 ->  http://127.1.0.1:8765
Phone / LAN     :  python app.py --lan           ->  prints http://<ip>:8765/?t=<token>
Config          :  config.json next to this file (see config.example.json)

Pipeline per timeframe:
  indicators (params per timeframe) -> 4 groups (trend / momentum / volume / structure)
  -> regime-weighted aggregate score -> multi-timeframe aggregate
  -> forecast cone (block-bootstrap Monte Carlo, drift ONLY if statistically justified)
  -> walk-forward calibration of both the score and the cone.
The forecast is a probability cone, not a prediction of the future path.
"""
import os, re, sys, time, json, math, stat, socket, secrets, inspect, threading, copy
import urllib.parse
import posixpath
from collections import deque
from concurrent.futures import ThreadPoolExecutor
import numpy as np
from numpy.lib.stride_tricks import sliding_window_view
import pandas as pd
import requests
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import agent_core as AC
import trend_engine as TE
import predictive_intelligence as PMI
from iran_crypto_market import IranCryptoMarket


class HTTPException(Exception):
    def __init__(self, status_code, detail):
        super().__init__(detail)
        self.status_code, self.detail = status_code, detail


class App:
    def __init__(self):
        self.routes = {}

    def get(self, path, **kw):
        def d(f):
            self.routes[("GET", path)] = f
            return f
        return d

    def post(self, path, **kw):
        def d(f):
            self.routes[("POST", path)] = f
            return f
        return d


app = App()
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.path.join(BASE_DIR, "config.json")
VALIDATION_EVIDENCE_PATH = os.path.join(BASE_DIR, "strategy_validation_evidence.json")
RESEARCH_REGISTRY_PATH = os.path.join(BASE_DIR, "research_registry.db")
HOLDOUT_VAULT_PATH = os.path.join(BASE_DIR, "holdout_vault.json")
AC.DB_PATH = os.path.join(BASE_DIR, "agent_journal.db")
RAMZINEX = "https://publicapi.ramzinex.ir/exchange/api/v1.0/exchange/chart/tv/v2.0/history"
TF_MIN = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "12h": 720, "1d": 1440}
SYM_RE = re.compile(r"^[A-Za-z0-9_\-/:.]{1,24}$")
EX_RE = re.compile(r"^[a-z0-9_]{1,32}$")

# ---------------------------------------------------------------- config
# NOTE: the numbers below are starting points (heuristics), not tuned values.
DEFAULTS = {
    "bars": 500,
    "cache_ttl": 20,
    "verdict_threshold": 0.20,
    "regime_adx": [20, 25],
    "tf_list": ["15m", "1h", "4h", "12h", "1d"],
    "tf_weights": {"15m": 0.10, "1h": 0.25, "4h": 0.25, "12h": 0.20, "1d": 0.20},
    "groups": {
        "trend": {"ema": 1, "sma": 1, "macd": 1, "adx": 1, "ichimoku": 1, "supertrend": 1},
        "momentum": {"rsi": 1, "stoch": 1, "cci": 1, "willr": 1, "bb": 1},
        "volume": {"obv": 1, "mfi": 1, "cmf": 1, "vwap": 1},
        "structure": {"donchian": 1, "keltner": 1},
    },
    "regime_weights": {
        "trending": {"trend": 1.5, "momentum": 0.5, "volume": 1.0, "structure": 1.0},
        "ranging": {"trend": 0.5, "momentum": 1.5, "volume": 1.0, "structure": 1.0},
        "transition": {"trend": 1.0, "momentum": 1.0, "volume": 1.0, "structure": 1.0},
    },
    "params": {
        "atr": {"n": 14}, "ema": {"fast": 20, "slow": 50}, "sma": {"n": 200},
        "macd": {"fast": 12, "slow": 26, "sig": 9}, "adx": {"n": 14, "thr": 20},
        "ichimoku": {"t": 9, "k": 26, "b": 52}, "supertrend": {"n": 10, "mult": 3.0},
        "rsi": {"n": 14, "lo": 30, "hi": 70}, "stoch": {"n": 14, "d": 3, "lo": 20, "hi": 80},
        "cci": {"n": 20, "lo": -100, "hi": 100}, "willr": {"n": 14, "lo": -80, "hi": -20},
        "bb": {"n": 20, "k": 2.0, "lo": 0.1, "hi": 0.9}, "obv": {"n": 20},
        "mfi": {"n": 14, "lo": 20, "hi": 80}, "cmf": {"n": 20}, "vwap": {"n": 100},
        "donchian": {"n": 20}, "keltner": {"n": 20, "mult": 2.0},
    },
    # per-timeframe overrides (only the keys that differ from "params")
    "tf_params": {
        "15m": {"ema": {"fast": 12, "slow": 26}, "sma": {"n": 100}, "macd": {"fast": 8, "slow": 17, "sig": 9},
                "rsi": {"n": 9}, "donchian": {"n": 32}, "vwap": {"n": 96}},
        "1h": {},
        "4h": {"vwap": {"n": 60}, "donchian": {"n": 30}},
        "12h": {"ema": {"fast": 14, "slow": 40}, "sma": {"n": 120}, "vwap": {"n": 40}, "donchian": {"n": 24}},
        "1d": {"ema": {"fast": 10, "slow": 30}, "sma": {"n": 100}, "vwap": {"n": 30}, "donchian": {"n": 20}},
    },
    "projection": {
        "calib_bars": 1000, "warm": 120, "lookback": 300, "block": 5, "paths": 2000,
        "min_t": 2.0, "beta_shrink": 0.5, "max_bars": 300, "seed": 7, "lambda": 0.94,
    },
    "explain_per_hour": 40,
    # trend engine (trend_engine.py): 12h/1d structure, trend lines, zones. Per-timeframe overrides: {"tf": {"1d": {...}}}
    "trend": {"bars": 1000, "tf_list": ["4h", "12h", "1d"], "tf_weights": {"4h": 0.20, "12h": 0.35, "1d": 0.45},
              "edge_bars": 5, "day_anchor_sec": None},
    "fee_roundtrip": 0.004,   # fallback ROUND-TRIP fee (fraction) when the symbol's market is unknown
    # ROUND-TRIP fee per quote market (buy + sell together). tether = *USDT symbols, rial = *IRR / *IRT symbols.
    "fee_by_market": {"tether": 0.001, "rial": 0.004},
    "backtest": {"market_type": "spot", "allow_short_futures": True, "position_size": 1.0, "futures_leverage": 1.0, "spread_slippage_roundtrip": 0.001, "market_impact_roundtrip": 0.0, "execution_delay_bars": 0, "funding_fee_per_bar": 0.0, "stop_atr_mult": 1.5, "target_atr_mult": 2.0},
    "walk_forward": {"n_splits": 5, "warmup_bars": 120, "gap_bars": 0},
    "futures_margin": {
        "margin_mode": "isolated", "maintenance_margin_rate": 0.005,
        "liquidation_fee_rate": 0.005, "max_leverage": 125,
        "warning_distance_pct": 5.0, "critical_distance_pct": 2.0,
    },
    # entries inside this local-time window are not tradable -> excluded from edge statistics, gate says NO_TRADE.
    # Applied only to timeframes <= tf_max_min (a 4h/12h/1d candle spans the window, so it cannot be filtered by open time).
    "restricted_hours": {"enabled": False, "start": "23:30", "end": "11:30", "tz_offset_min": 210, "tf_max_min": 60},
    "gate": {
        "min_t": 2.0, "min_rr": 1.5, "spread_slippage_roundtrip": 0.001,
        "min_edge_samples": 30,
        # Fail closed until the exact strategy/execution configuration has
        # fresh, positive, net-of-cost chronological OOS evidence.
        "require_oos_validation": True,
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
    },
    # background journal: one prediction per new bar, independent of whether you press Analyze.
    # set to [] in config.json to disable. Only source "ramzinex" is supported.
    "journal_symbols": ["USDTIRR", "BTCIRR", "ETHIRR"],   # edit: must be valid ramzinex symbols
    "journal_grid": [["15m", 1], ["15m", 4], ["1h", 4], ["1h", 12], ["1h", 24],
                     ["4h", 24], ["4h", 72], ["12h", 72], ["12h", 168], ["1d", 72], ["1d", 168]],
    "journal_watch": [],   # optional extra explicit entries: {"source","symbol","tf","hours"}
    "journal_poll_sec": 120,
    "iran_crypto_mcp": {
        "enabled": False, "command": "uvx", "args": ["iran-crypto-mcp"],
        "timeout_sec": 25, "default_quote": "IRT", "default_exchange": "wallex",
        "proxy": "",
    },
}


def _merge(a, b):
    out = copy.deepcopy(a)
    if not isinstance(b, dict):
        return out
    for k, v in b.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else copy.deepcopy(v)
    return out


_cfg_cache = {"mtime": None, "data": {}}


def cfg():
    """Raw user config.json (hot-reloaded when the file changes)."""
    try:
        m = os.path.getmtime(CFG_PATH)
    except OSError:
        return {}
    if _cfg_cache["mtime"] != m:
        try:
            with open(CFG_PATH, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print(f"[config] config.json unreadable ({type(e).__name__}) - using defaults")
            data = {}
        _cfg_cache.update(mtime=m, data=data if isinstance(data, dict) else {})
    return _cfg_cache["data"]


def conf():
    """Defaults + user config (params/tf_params are layered separately, see params_for)."""
    u = {k: v for k, v in cfg().items() if k not in ("params", "tf_params", "tf_groups")}
    return _merge(DEFAULTS, u)


_iran_mcp_lock = threading.RLock()
_iran_mcp_instance = None
_iran_mcp_fingerprint = None

def iran_crypto_market():
    """Lazily manage the optional read-only MCP process; hot-reload its config safely."""
    global _iran_mcp_instance, _iran_mcp_fingerprint
    settings = conf().get("iran_crypto_mcp") or {}
    fingerprint = json.dumps(settings, sort_keys=True, ensure_ascii=False)
    with _iran_mcp_lock:
        if fingerprint != _iran_mcp_fingerprint:
            if _iran_mcp_instance is not None:
                _iran_mcp_instance.close()
            _iran_mcp_instance = IranCryptoMarket(settings)
            _iran_mcp_fingerprint = fingerprint
        return _iran_mcp_instance


# ---------------------------------------------------------------- Ramzinex helpers (apply_patch_v2)
def get_fee(C, symbol="", market_type=None):
    """Return configured ROUND-TRIP fee; the backtest engine receives half per side."""
    bt = C.get("backtest") or {}
    market_type = str(market_type or bt.get("market_type", "spot")).lower()
    if market_type == "futures":
        return float(bt.get("futures_fee_roundtrip", C.get("futures_fee_roundtrip", 0.001)))
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


def params_for(tf):
    u = cfg()
    p = _merge(DEFAULTS["params"], DEFAULTS["tf_params"].get(tf, {}))
    p = _merge(p, u.get("params"))
    return _merge(p, (u.get("tf_params") or {}).get(tf))


def groups_for(tf, C):
    return _merge(C["groups"], (cfg().get("tf_groups") or {}).get(tf))


# ---------------------------------------------------------------- security helpers
def _secret_values():
    c = cfg()
    vals = [c.get("openrouter_key"), os.getenv("OPENROUTER_API_KEY"), c.get("xai_key"),
            os.getenv("XAI_API_KEY"), c.get("custom_key"), c.get("access_token"), os.getenv("ACCESS_TOKEN")]
    return [v for v in vals if isinstance(v, str) and len(v) >= 6]


def redact(s):
    s = str(s)
    for v in _secret_values():
        s = s.replace(v, "***")
    return s


_EXPLAIN, _RL = deque(), threading.Lock()


def explain_rate_ok():
    lim, now = int(conf().get("explain_per_hour", 40)), time.time()
    with _RL:
        while _EXPLAIN and now - _EXPLAIN[0] > 3600:
            _EXPLAIN.popleft()
        if len(_EXPLAIN) >= lim:
            return False
        _EXPLAIN.append(now)
        return True


# ---------------------------------------------------------------- data sources
def _udf(url, symbol, tf, n, extra=None):
    res = TF_MIN[tf]
    now = int(time.time())
    p = {"symbol": symbol, "resolution": res, "from": now - n * res * 60, "to": now, "countback": n}
    p.update(extra or {})
    r = requests.get(url, params=p, timeout=20, allow_redirects=False)
    r.raise_for_status()
    j = r.json()
    if j.get("s") != "ok":
        raise ValueError(f"UDF response: {str(j)[:200]}")
    return pd.DataFrame({"time": j["t"], "open": j["o"], "high": j["h"], "low": j["l"],
                         "close": j["c"], "volume": j.get("v", [0] * len(j["t"]))})


def closed_candles(df, tf, now=None):
    """Return only candles whose full timeframe interval has elapsed."""
    if tf not in TF_MIN:
        raise ValueError("unknown timeframe")
    now_ts = float(time.time() if now is None else now)
    bar_sec = TF_MIN[tf] * 60
    return df.loc[df["time"].astype(float) + bar_sec <= now_ts].copy().reset_index(drop=True)


def _udf_url_allowed(url, allowlist):
    """Match a UDF URL against an exact trusted origin and a path-boundary prefix."""
    try:
        target = urllib.parse.urlsplit(str(url).strip())
        if target.scheme not in ("http", "https") or not target.hostname:
            return False
        if target.username is not None or target.password is not None or target.query or target.fragment:
            return False
        if "%" in target.path or "\\\\" in target.path:
            return False
        target_port = target.port or (443 if target.scheme == "https" else 80)
        target_host = target.hostname.rstrip(".").lower()
        target_path = posixpath.normpath("/" + target.path.lstrip("/")).rstrip("/")
        for item in allowlist:
            if not isinstance(item, str) or not item.strip():
                continue
            allowed = urllib.parse.urlsplit(item.strip())
            if allowed.scheme not in ("http", "https") or not allowed.hostname:
                continue
            if allowed.username is not None or allowed.password is not None or allowed.query or allowed.fragment:
                continue
            if "%" in allowed.path or "\\\\" in allowed.path:
                continue
            allowed_port = allowed.port or (443 if allowed.scheme == "https" else 80)
            allowed_host = allowed.hostname.rstrip(".").lower()
            allowed_path = posixpath.normpath("/" + allowed.path.lstrip("/")).rstrip("/")
            if (target.scheme == allowed.scheme and target_host == allowed_host
                    and target_port == allowed_port
                    and (not allowed_path or target_path == allowed_path
                         or target_path.startswith(allowed_path + "/"))):
                return True
    except (TypeError, ValueError):
        return False
    return False


def load(source, symbol, tf, n, exchange="coinex", udf_url="", csv_path=""):
    if tf not in TF_MIN:
        raise ValueError("unknown timeframe")
    if not SYM_RE.match(symbol or ""):
        raise ValueError("invalid symbol")
    n = max(100, min(int(n), 5000))
    C = conf()
    if tf == "12h" and source in ("ramzinex", "udf"):
        # the UDF feeds do not document a 12h resolution: build it from 4h candles on the exchange's day grid
        base = load(source, symbol, "4h", min(5000, n * 3 + 6), exchange, udf_url, csv_path)
        r = TE.resample_ohlcv(base, 12, day_anchor(source, symbol, exchange, udf_url, csv_path, C), now=time.time())
        if len(r) < 60:
            raise ValueError(f"only {len(r)} complete 12h candles could be built - not enough")
        return r.tail(n).reset_index(drop=True)
    if source == "ramzinex":
        df = _udf(RAMZINEX, symbol, tf, n)
    elif source == "udf":
        allow = [a for a in (C.get("udf_allowlist") or []) if isinstance(a, str) and a]
        if not allow:
            raise ValueError("udf disabled: add trusted base URLs to 'udf_allowlist' in config.json")
        if not _udf_url_allowed(udf_url, allow):
            raise ValueError("udf_url must match an exact trusted origin and path in udf_allowlist")
        base = udf_url.rstrip("/")
        path = urllib.parse.urlsplit(base).path.rstrip("/")
        endpoint = base if path.endswith("/history") or path == "history" else base + "/history"
        df = _udf(endpoint, symbol, tf, n)
    elif source == "ccxt":
        import ccxt
        if not EX_RE.match(exchange or "") or exchange not in ccxt.exchanges:
            raise ValueError("unknown ccxt exchange")
        ex = getattr(ccxt, exchange)({"enableRateLimit": True})
        o = ex.fetch_ohlcv(symbol, tf, limit=n)
        df = pd.DataFrame(o, columns=["time", "open", "high", "low", "close", "volume"])
        df["time"] //= 1000
    elif source == "csv":
        base = os.path.realpath(C.get("csv_dir") or os.path.join(BASE_DIR, "data"))
        name = os.path.basename(csv_path or "")
        if not name.lower().endswith(".csv"):
            raise ValueError("csv: type only a file NAME ending in .csv that lives in the csv_dir folder")
        full = os.path.realpath(os.path.join(base, name))
        if os.path.dirname(full) != base or not os.path.isfile(full):
            raise ValueError(f"csv: file not found in {base}")
        df = pd.read_csv(full)
        df.columns = [str(c).strip().lower() for c in df.columns]
        if "time" not in df.columns:
            for alt in ("date", "datetime", "timestamp"):
                if alt in df.columns:
                    df["time"] = pd.to_datetime(df[alt]).map(lambda x: x.timestamp()) if not pd.api.types.is_numeric_dtype(df[alt]) else df[alt]
                    break
    else:
        raise ValueError("unknown source")
    for col in ("time", "open", "high", "low", "close"):
        if col not in df.columns:
            raise ValueError(f"missing column: {col}")
    if "volume" not in df.columns:
        df["volume"] = 0.0
    df = df[["time", "open", "high", "low", "close", "volume"]].astype(float)
    if len(df) and df.time.max() > 1e11:
        df["time"] = df.time / 1000.0
    ingest_audit = {
        "input_rows": int(len(df)),
        "non_finite_rows": int((~np.isfinite(df.to_numpy(dtype=float)).all(axis=1)).sum()),
        "duplicate_timestamps": int(df.time.duplicated().sum()),
        "out_of_order_timestamps": int((np.diff(df.time.to_numpy(dtype=float)) < 0).sum()) if len(df) > 1 else 0,
    }
    raw_valid = ((df[["open", "high", "low", "close"]] > 0).all(axis=1)
                 & (df.high >= df.low)
                 & (df.high >= df[["open", "close"]].max(axis=1))
                 & (df.low <= df[["open", "close"]].min(axis=1)))
    ingest_audit["invalid_ohlc_rows"] = int((~raw_valid).sum())
    ingest_audit["negative_volume_rows"] = int((df.volume < 0).sum())
    df = df.dropna().sort_values("time").drop_duplicates("time")
    # V5.3: never analyse a live candle before its close. Historical CSV files are
    # left untouched; live exchange/UDF feeds are filtered using the timeframe grid.
    if source in ("ramzinex", "udf", "ccxt") and len(df):
        df = closed_candles(df, tf)
    # Reject malformed OHLC rows, not merely rows with positive prices.
    valid_ohlc = ((df[["open", "high", "low", "close"]] > 0).all(axis=1)
                  & (df.high >= df.low)
                  & (df.high >= df[["open", "close"]].max(axis=1))
                  & (df.low <= df[["open", "close"]].min(axis=1)))
    df = df[valid_ohlc].reset_index(drop=True)
    df.attrs["ingest_audit"] = ingest_audit
    if len(df) < 60:
        raise ValueError(f"only {len(df)} valid candles - not enough")
    return df


_ANCHOR = {}


def day_anchor(source, symbol, exchange, udf_url, csv_path, C):
    """Seconds after UTC midnight at which the exchange's daily candle opens (so 12h candles nest inside 1d candles)."""
    fixed = (C.get("trend") or {}).get("day_anchor_sec")
    if isinstance(fixed, (int, float)):
        return int(fixed) % 86400
    k = (source, symbol)
    if k not in _ANCHOR:
        try:
            d1 = load(source, symbol, "1d", 100, exchange, udf_url, csv_path)
            _ANCHOR[k] = int(d1.time.iloc[-1]) % 86400
        except Exception:
            return 0                      # not cached on failure -> retried next time
    return _ANCHOR[k]


_CACHE, _CL = {}, threading.Lock()


def load_cached(*key):
    ttl, now = float(conf().get("cache_ttl", 20)), time.time()
    with _CL:
        hit = _CACHE.get(key)
        if hit and now - hit[0] < ttl:
            cached = hit[1]
            if len(key) >= 3:
                _guard_holdout_research_access(cached, key[0], key[1], key[2])
            return cached
    df = load(*key)
    # Central guard: every dashboard API using load_cached is barred from a sealed holdout.
    if len(key) >= 3:
        _guard_holdout_research_access(df, key[0], key[1], key[2])
    with _CL:
        _CACHE[key] = (now, df)
        if len(_CACHE) > 48:
            for k in sorted(_CACHE, key=lambda k: _CACHE[k][0])[:16]:
                _CACHE.pop(k, None)
    return df


# ---------------------------------------------------------------- indicators
def ema(s, n): return s.ewm(span=int(n), adjust=False).mean()
def wilder(s, n): return s.ewm(alpha=1 / int(n), adjust=False).mean()
def I(p, k): return max(1, int(p[k]))


def _ext(x, lo, hi, w):
    """+1 when x is well below lo (oversold), -1 when well above hi (overbought), 0 in between."""
    return ((lo - x) / w).clip(0, 1) - ((x - hi) / w).clip(0, 1)


class Ctx:
    def __init__(s, df, P):
        s.c, s.h, s.l, s.v, s.o = df.close, df.high, df.low, df.volume, df.open
        s.tp = (s.h + s.l + s.c) / 3
        s.tr = pd.concat([s.h - s.l, (s.h - s.c.shift()).abs(), (s.l - s.c.shift()).abs()], axis=1).max(axis=1)
        floor = s.c * 1e-9
        s.atr = wilder(s.tr, I(P["atr"], "n"))
        s.atr = s.atr.where(s.atr > floor, floor)
        n = I(P["adx"], "n")
        up, dn = s.h.diff(), -s.l.diff()
        pdm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=df.index)
        mdm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=df.index)
        a = wilder(s.tr, n)
        a = a.where(a > floor, floor)
        s.pdi, s.mdi = 100 * wilder(pdm, n) / a, 100 * wilder(mdm, n) / a
        s.adx = wilder(100 * (s.pdi - s.mdi).abs() / (s.pdi + s.mdi).replace(0, np.nan), n).fillna(0)


IND = {}


def reg(name, note=""):
    def d(f):
        IND[name] = (f, note)
        return f
    return d


@reg("ema", "fast vs slow EMA (ATR-scaled)")
def i_ema(X, p):
    d = ema(X.c, p["fast"]) - ema(X.c, p["slow"])
    return np.tanh(d / X.atr), d, f"EMA {I(p, 'fast')}/{I(p, 'slow')}"


@reg("sma", "price vs long SMA (ATR-scaled)")
def i_sma(X, p):
    w = min(I(p, "n"), len(X.c) - 1)
    d = X.c - X.c.rolling(w).mean()
    return np.tanh(d / (2 * X.atr)), d, f"SMA {w}"


@reg("macd", "MACD histogram (ATR-scaled)")
def i_macd(X, p):
    m = ema(X.c, p["fast"]) - ema(X.c, p["slow"])
    h = m - ema(m, p["sig"])
    return np.tanh(h / (0.2 * X.atr)), h, f"MACD {I(p, 'fast')}/{I(p, 'slow')}/{I(p, 'sig')}"


@reg("adx", "DI direction, only when ADX above threshold")
def i_adx(X, p):
    strength = ((X.adx - p["thr"]) / 20).clip(0, 1)
    return np.sign(X.pdi - X.mdi) * strength, X.adx, f"ADX {I(p, 'n')}"


@reg("ichimoku", "price vs cloud, 0 inside cloud")
def i_ichi(X, p):
    t, k, b = I(p, "t"), I(p, "k"), I(p, "b")
    ten = (X.h.rolling(t).max() + X.l.rolling(t).min()) / 2
    kij = (X.h.rolling(k).max() + X.l.rolling(k).min()) / 2
    sa = ((ten + kij) / 2).shift(k)
    sb = ((X.h.rolling(b).max() + X.l.rolling(b).min()) / 2).shift(k)
    cl = pd.concat([sa, sb], axis=1)
    top, bot = cl.max(axis=1, skipna=False), cl.min(axis=1, skipna=False)
    d = pd.Series(np.where(X.c > top, X.c - top, np.where(X.c < bot, X.c - bot, 0.0)), index=X.c.index)
    d = d.where(top.notna(), np.nan)
    return np.tanh(d / X.atr), d, f"Ichimoku {t}/{k}/{b}"


@reg("supertrend", "SuperTrend direction")
def i_st(X, p):
    n, m = I(p, "n"), float(p["mult"])
    a = wilder(X.tr, n).values
    hl, cc = ((X.h + X.l) / 2).values, X.c.values
    N = len(cc)
    fub, flb, st, dr = np.zeros(N), np.zeros(N), np.zeros(N), np.ones(N)
    for i in range(N):
        ub, lb = hl[i] + m * a[i], hl[i] - m * a[i]
        if i == 0:
            fub[i], flb[i] = ub, lb
        else:
            fub[i] = ub if (ub < fub[i - 1] or cc[i - 1] > fub[i - 1]) else fub[i - 1]
            flb[i] = lb if (lb > flb[i - 1] or cc[i - 1] < flb[i - 1]) else flb[i - 1]
            if dr[i - 1] == 1 and cc[i] < flb[i]:
                dr[i] = -1
            elif dr[i - 1] == -1 and cc[i] > fub[i]:
                dr[i] = 1
            else:
                dr[i] = dr[i - 1]
        st[i] = flb[i] if dr[i] == 1 else fub[i]
    dist = np.abs(cc - st) / X.atr.values
    sig = pd.Series(dr * np.clip(0.3 + dist / 4, 0, 1), index=X.c.index)
    return sig, pd.Series(st, index=X.c.index), f"SuperTrend {n}x{m:g}"


@reg("rsi", "oversold/overbought (mean reversion)")
def i_rsi(X, p):
    n = I(p, "n")
    d = X.c.diff()
    g, lo_ = wilder(d.clip(lower=0), n), wilder(-d.clip(upper=0), n)
    rsi = (100 - 100 / (1 + g / lo_.clip(lower=1e-12))).where((g + lo_) > 1e-12, 50.0)
    return _ext(rsi, p["lo"], p["hi"], 10), rsi, f"RSI {n}"


@reg("stoch", "%K extremes; half weight if %K/%D disagree")
def i_stoch(X, p):
    n = I(p, "n")
    ll, hh = X.l.rolling(n).min(), X.h.rolling(n).max()
    k = 100 * (X.c - ll) / (hh - ll).replace(0, np.nan)
    d = k.rolling(I(p, "d")).mean()
    base = _ext(k, p["lo"], p["hi"], 10)
    agree = ((k > d) & (base > 0)) | ((k < d) & (base < 0))
    return base * np.where(agree, 1.0, 0.5), k, f"Stoch {n}"


def _rolling_mad(x, n):
    """Rolling mean absolute deviation (same result as rolling(n).apply(mean |a-mean(a)|), but vectorised)."""
    a = np.asarray(x, dtype=float)
    out = np.full(len(a), np.nan)
    if len(a) >= n:
        w = sliding_window_view(a, n)
        out[n - 1:] = np.abs(w - w.mean(axis=1, keepdims=True)).mean(axis=1)
    return pd.Series(out, index=getattr(x, "index", None))


@reg("cci", "extremes beyond +-100")
def i_cci(X, p):
    n = I(p, "n")
    mad = _rolling_mad(X.tp, n)
    cci = (X.tp - X.tp.rolling(n).mean()) / (0.015 * mad.replace(0, np.nan))
    return _ext(cci, p["lo"], p["hi"], 50), cci, f"CCI {n}"


@reg("willr", "Williams %R extremes")
def i_willr(X, p):
    n = I(p, "n")
    ll, hh = X.l.rolling(n).min(), X.h.rolling(n).max()
    wr = -100 * (hh - X.c) / (hh - ll).replace(0, np.nan)
    return _ext(wr, p["lo"], p["hi"], 10), wr, f"Williams %R {n}"


@reg("bb", "Bollinger %B extremes")
def i_bb(X, p):
    n, k = I(p, "n"), float(p["k"])
    ma, sd = X.c.rolling(n).mean(), X.c.rolling(n).std()
    pb = (X.c - (ma - k * sd)) / (2 * k * sd).replace(0, np.nan)
    return _ext(pb, p["lo"], p["hi"], 0.15), pb, f"Bollinger %B {n}"


@reg("obv", "OBV vs its EMA (volume flow)")
def i_obv(X, p):
    n = I(p, "n")
    obv = (np.sign(X.c.diff().fillna(0)) * X.v).cumsum()
    scale = obv.diff().abs().rolling(n).mean() * math.sqrt(n) + 1e-12
    d = obv - ema(obv, n)
    return np.tanh(d / scale), d, f"OBV {n}"


@reg("mfi", "volume-weighted RSI extremes")
def i_mfi(X, p):
    n = I(p, "n")
    rf = X.tp * X.v
    up = rf.where(X.tp > X.tp.shift(), 0).rolling(n).sum()
    dn = rf.where(X.tp < X.tp.shift(), 0).rolling(n).sum()
    ratio = up / dn.replace(0, np.nan)
    mfi = 100 - 100 / (1 + ratio)
    # A zero negative-flow window is not missing data: positive-only flow is 100.
    # A window with neither positive nor negative flow is neutral at 50.
    mfi = mfi.where(dn > 0, np.where(up > 0, 100.0, 50.0))
    return _ext(mfi, p["lo"], p["hi"], 10), mfi, f"MFI {n}"


@reg("cmf", "Chaikin money flow")
def i_cmf(X, p):
    n = I(p, "n")
    mfv = ((X.c - X.l) - (X.h - X.c)) / (X.h - X.l).replace(0, np.nan) * X.v
    cmf = mfv.rolling(n).sum() / X.v.rolling(n).sum().replace(0, np.nan)
    return (cmf / 0.25).clip(-1, 1), cmf, f"CMF {n}"


@reg("vwap", "price vs rolling VWAP")
def i_vwap(X, p):
    n = I(p, "n")
    vw = (X.tp * X.v).rolling(n).sum() / X.v.rolling(n).sum().replace(0, np.nan)
    d = X.c - vw
    return np.tanh(d / (2 * X.atr)), d, f"VWAP {n}"


@reg("donchian", "position inside / breakout of N-bar channel")
def i_don(X, p):
    n = I(p, "n")
    hi, lo = X.h.rolling(n).max().shift(1), X.l.rolling(n).min().shift(1)
    pos = (X.c - (hi + lo) / 2) / ((hi - lo) / 2).replace(0, np.nan)
    return pos.clip(-1, 1), pos, f"Donchian {n}"


@reg("keltner", "position vs Keltner channel")
def i_kel(X, p):
    n, m = I(p, "n"), float(p["mult"])
    pos = (X.c - ema(X.c, n)) / (m * X.atr)
    return pos.clip(-1, 1), pos, f"Keltner {n}x{m:g}"


def verdict_of(s, thr):
    return "Bullish" if s >= thr else "Bearish" if s <= -thr else "Neutral"


def compute(df, tf):
    """Full-history signal series for every indicator, group and the aggregate score (all causal)."""
    C = conf()
    P = params_for(tf)
    X = Ctx(df, P)
    members = {}
    for g, m in groups_for(tf, C).items():
        if isinstance(m, dict):
            members[g] = {k: float(w) for k, w in m.items()
                          if k in IND and isinstance(w, (int, float)) and float(w) > 0}
    sig, val, lab, err = {}, {}, {}, {}
    for m in members.values():
        for k in m:
            if k in sig:
                continue
            try:
                s, v, l = IND[k][0](X, P[k])
                sig[k] = pd.Series(s, index=df.index).replace([np.inf, -np.inf], np.nan).fillna(0).clip(-1, 1)
                val[k], lab[k] = pd.Series(v, index=df.index).replace([np.inf, -np.inf], np.nan), l
            except Exception as e:
                sig[k] = pd.Series(0.0, index=df.index)
                val[k], lab[k] = pd.Series(np.nan, index=df.index), k
                err[k] = f"{type(e).__name__}: {str(e)[:60]}"
    gs = {g: sum(sig[k] * w for k, w in m.items()) / sum(m.values()) for g, m in members.items() if m}
    lo, hi = C["regime_adx"]
    # Legacy regime remains the sole input to existing regime-weighted signals.
    # V6.9.2 regime and V6.9.3/V6.9.4 exit research are research metadata; neither alters S.
    regime = pd.Series(np.where(X.adx > hi, "trending", np.where(X.adx < lo, "ranging", "transition")), index=df.index)
    from market_regime_intelligence import analyze_market_regimes
    market_regime_intelligence = analyze_market_regimes(df)
    num, den = pd.Series(0.0, index=df.index), pd.Series(0.0, index=df.index)
    for r in ("trending", "ranging", "transition"):
        mask = (regime == r).astype(float)
        for g, s in gs.items():
            w = float(C["regime_weights"][r].get(g, 1.0))
            num += s * w * mask
            den += w * mask
    S = (num / den.replace(0, np.nan)).fillna(0)
    return {"x": X, "sig": sig, "val": val, "lab": lab, "err": err, "gs": gs, "S": S,
            "regime": regime, "market_regime_intelligence": market_regime_intelligence,
            "members": members}


def summarize(res, df, C):
    from indicator_integrity import audit_indicators, METRIC_META
    thr = float(C["verdict_threshold"])
    X = res["x"]
    S = float(res["S"].iloc[-1])
    inds = []
    for g, m in res["members"].items():
        for k in m:
            v = res["val"][k].iloc[-1]
            kind, meaning, unit = METRIC_META.get(k, ("unknown", "indicator-specific metric", "unknown"))
            row = {"key": k, "name": res["lab"][k], "group": g, "note": IND[k][1],
                   "value": None if pd.isna(v) else round(float(v), 4),
                   "metric_kind": kind, "meaning": meaning, "unit": unit,
                   "signal": round(float(res["sig"][k].iloc[-1]), 2)}
            if k in res["err"]:
                row["error"] = res["err"][k]
            inds.append(row)
    groups = {g: {"score": round(float(s.iloc[-1]), 3), "verdict": verdict_of(float(s.iloc[-1]), thr),
                  "n": len(res["members"][g])} for g, s in res["gs"].items()}
    active = [v["score"] for v in groups.values() if abs(v["score"]) >= 0.1]
    agree_n = sum(1 for v in active if S != 0 and np.sign(v) == np.sign(S))
    agree = agree_n / len(active) if active else 0.0
    conf_v = round(min(1.0, abs(S) / 0.5) * agree, 2)
    ma, sd = X.c.rolling(20).mean(), X.c.rolling(20).std()
    bw = (4 * sd / ma).dropna()
    bw_pct = float((bw.iloc[-1] >= bw).mean()) if len(bw) else 0.5
    atr = float(X.atr.iloc[-1])
    indicator_integrity = audit_indicators(res, df.close)
    from market_regime_intelligence import summarize_regime_state
    market_regime_state = summarize_regime_state(res.get("market_regime_intelligence"))
    return {"indicators": inds, "groups": groups, "indicator_integrity": indicator_integrity, "score": round(S, 3), "verdict": verdict_of(S, thr),
            "agree_n": agree_n, "active_n": len(active), "confidence": conf_v,
            "conf_label": "high" if conf_v >= 0.66 else "medium" if conf_v >= 0.33 else "low",
            "regime": str(res["regime"].iloc[-1]), "market_regime_intelligence": market_regime_state,
            "adx": round(float(X.adx.iloc[-1]), 1),
            "bw_pct": round(bw_pct, 2), "atr": atr, "atr_pct": round(100 * atr / float(X.c.iloc[-1]), 3),
            "squeeze": bool(bw_pct < 0.2)}


def levels(df, atr):
    h, l = df.high.values, df.low.values
    k, piv = 5, []
    for i in range(k, len(df) - k):
        if h[i] == h[i - k:i + k + 1].max(): piv.append(float(h[i]))
        if l[i] == l[i - k:i + k + 1].min(): piv.append(float(l[i]))
    piv.sort()
    cl, tol = [], 0.6 * atr
    for p in piv:
        if cl and p - cl[-1][2] <= tol:
            c = cl[-1]; c[0] = (c[0] * c[1] + p) / (c[1] + 1); c[1] += 1; c[2] = p
        else:
            cl.append([p, 1, p])
    last = float(df.close.iloc[-1])
    strong = [c for c in cl if c[1] >= 2] or cl
    sup = sorted([c for c in strong if c[0] < last], key=lambda c: last - c[0])[:3]
    res = sorted([c for c in strong if c[0] > last], key=lambda c: c[0] - last)[:3]
    return ([{"p": round(c[0], 6), "n": c[1], "kind": "S"} for c in sup] +
            [{"p": round(c[0], 6), "n": c[1], "kind": "R"} for c in res])


def apply_integrity_gate(plan, data_audit, indicator_audit):
    """Fail closed when critical source or latest-indicator integrity checks fail."""
    blockers = []
    if not (data_audit or {}).get("valid", False):
        blockers.append("OHLCV data integrity: " + "; ".join((data_audit or {}).get("errors", [])))
    if not (indicator_audit or {}).get("valid", False):
        blockers.append("Indicator integrity: " + "; ".join((indicator_audit or {}).get("errors", [])))
    if blockers:
        return {"action": "NO TRADE - DATA INTEGRITY BLOCK", "note": " | ".join(blockers)}
    return plan


def make_plan(res, lv, last):
    atr = res["atr"]
    if res["verdict"] != "Bullish":
        return {"action": "No long setup" if res["verdict"] == "Neutral" else "Bearish - stay out of longs",
                "note": "Spot-only plan: nothing to buy while the aggregate score is not Bullish."}
    sup = [x["p"] for x in lv if x["kind"] == "S"]
    resi = [x["p"] for x in lv if x["kind"] == "R"]
    stop = last - 1.5 * atr
    if sup and last - 2.5 * atr < sup[0] < last - 0.5 * atr:
        stop = sup[0] - 0.25 * atr
    t1 = next((r for r in resi if r > last + atr), last + 2 * atr)
    t2 = next((r for r in resi if r > t1 + 0.5 * atr), last + 3.5 * atr)
    rr = (t1 - last) / max(last - stop, 1e-12)
    ok = rr >= 1.5
    return {"action": "Long setup" if ok else "Skip - risk/reward too low",
            "entry": [round(last - 0.5 * atr, 6), round(last, 6)], "stop": round(stop, 6),
            "t1": round(t1, 6), "t2": round(t2, 6), "rr": round(rr, 2),
            "note": f"Stop = ATR/support based. Regime: {res['regime']}. Not a guarantee - size the position from the stop distance."}


# ---------------------------------------------------------------- backtest (causal, fee-aware)
def _execution_cost_kwargs(backtest_config, gate_config=None, multiplier=1.0):
    """Return explicit spread/slippage kwargs, retaining the legacy combined field."""
    bt=backtest_config or {}; gate=gate_config or {}; m=float(multiplier)
    if "spread_roundtrip" in bt or "slippage_roundtrip" in bt:
        return {"spread_rt":float(bt.get("spread_roundtrip",0.0))*m,
                "slippage_rt":float(bt.get("slippage_roundtrip",0.0))*m}
    legacy=float(bt.get("spread_slippage_roundtrip",gate.get("spread_slippage_roundtrip",0.0)))
    return {"spread_slippage_rt":legacy*m}


def _roundtrip_spread_slippage(backtest_config, gate_config=None):
    bt=backtest_config or {}; gate=gate_config or {}
    if "spread_roundtrip" in bt or "slippage_roundtrip" in bt:
        return float(bt.get("spread_roundtrip",0.0))+float(bt.get("slippage_roundtrip",0.0))
    return float(bt.get("spread_slippage_roundtrip",gate.get("spread_slippage_roundtrip",0.0)))


def backtest(df, tf, fee=None, start=None, symbol=""):
    """Market-aware backtest; Spot is long-only and Futures can be long/short."""
    from backtest_engine import run_backtest
    C = conf()
    bt = C.get("backtest") or {}
    gate = C.get("gate") or {}
    market_type = str(bt.get("market_type", "spot")).lower()
    if market_type not in ("spot", "futures"):
        raise ValueError("backtest.market_type must be 'spot' or 'futures'")
    thr = float(C["verdict_threshold"])
    fee = get_fee(C, symbol, market_type) / 2.0 if fee is None else float(fee)
    fee = max(0.0, min(fee, 0.05))
    required_warmup = required_indicator_warmup(tf, C)
    configured_start = 120 if start is None else int(start)
    # run_backtest's `start` is the SIGNAL candle; its fill occurs at open[start+1].
    # WF's first OOS fill is at open[warmup_bars]. Therefore the full-window
    # start must be warmup_bars - 1 to evaluate the exact same first executable
    # candle. Explicit `start` remains a low-level override for tests/research.
    if start is None:
        required_signal_start = max(configured_start, required_warmup - 1)
        feasible_start = max(configured_start, len(df) - 64)
        effective_start = min(required_signal_start, feasible_start)
    else:
        effective_start = configured_start
    spread_rt = float(bt.get("spread_slippage_roundtrip", gate.get("spread_slippage_roundtrip", 0.0)))
    position_size = float(bt.get("position_size", 1.0))
    leverage = float(bt.get("futures_leverage", 1.0)) if market_type == "futures" else 1.0
    allow_short = bool(bt.get("allow_short_futures", True)) if market_type == "futures" else False
    funding_rate = float(bt.get("funding_fee_per_bar", 0.0)) if market_type == "futures" else 0.0
    from strategy_autopsy import analyze_trades
    indicators = compute(df, tf)
    S = indicators["S"].to_numpy(dtype=float)
    result = run_backtest(
        df, S, thr, fee, **_execution_cost_kwargs(bt,gate), start=effective_start,
        allow_short=allow_short, position_size=position_size,
        market_type=market_type, leverage=leverage,
        funding_fee_per_bar=funding_rate,
        atr=_backtest_atr(df, indicators),
        stop_atr_mult=float(bt.get("stop_atr_mult", 1.5)),
        target_atr_mult=float(bt.get("target_atr_mult", 2.0)),
        execution_delay_bars=int(bt.get("execution_delay_bars", 0)),
        market_impact_rt=float(bt.get("market_impact_roundtrip", 0.0)),
        entry_allowed=filter_restricted_bars(df, C, tf),
    )
    # Attach only information known at the signal candle close immediately
    # before the next-open fill. This preserves the engine's causal timing.
    for trade in result.get("trades_log", []):
        signal_idx = max(0, min(len(df) - 1, int(trade.get("entry_bar", 1)) - 1))
        score = float(indicators["S"].iloc[signal_idx])
        trade["entry_signal_score"] = round(score, 6)
        trade["entry_signal_band"] = ("bullish" if score >= thr else "bearish" if score <= -thr else "neutral")
        regime_series = indicators.get("regime") if isinstance(indicators, dict) else None
        trade["entry_regime"] = str(regime_series.iloc[signal_idx]) if regime_series is not None else "unknown"
        mri = indicators.get("market_regime_intelligence") if isinstance(indicators, dict) else None
        if mri is not None and len(mri) > signal_idx:
            trade["entry_market_regime"] = str(mri["regime"].iloc[signal_idx])
            trade["entry_regime_transition"] = bool(mri["transition"].iloc[signal_idx])
        group_series = indicators.get("gs", {}) if isinstance(indicators, dict) else {}
        for group, series in group_series.items():
            trade[f"entry_{group}_score"] = round(float(series.iloc[signal_idx]), 6)
    result["autopsy"] = analyze_trades(
        result.get("trades_log", []), strategy_pct=result.get("strategy_pct"),
        buyhold_pct=result.get("buyhold_pct"), max_dd_pct=result.get("max_dd_pct"),
    )
    # Research-only edge and execution-economics audits on completed trades.
    from trade_edge_discovery import analyze_trade_edge
    edge_rows = []
    for trade in result.get("trades_log", []):
        row = dict(trade)
        row["market_regime"] = row.get("entry_regime", "unknown")
        row["exit_reason"] = row.get("exit_reason", "unknown")
        edge_rows.append(row)
    result["trade_edge_discovery"] = analyze_trade_edge(edge_rows)
    # Separate regime-intelligence attribution; legacy regime groups remain intact.
    regime_edge_rows = []
    for trade in result.get("trades_log", []):
        row = dict(trade)
        row["market_regime"] = row.get("entry_market_regime", "unknown")
        regime_edge_rows.append(row)
    result["market_regime_edge_research"] = analyze_trade_edge(
        regime_edge_rows, group_fields=("market_regime",), min_samples=10
    )
    result["market_regime_transition_edge_research"] = analyze_trade_edge(
        regime_edge_rows, group_fields=("entry_regime_transition",), min_samples=10
    )
    result["market_regime_intelligence"] = {
        "engine": "MarketRegimeIntelligence V1.0", "status": "RESEARCH_ONLY",
        "note": "Parallel classification only; existing signal and regime weights are unchanged."
    }
    from execution_viability import analyze_execution_viability
    result["execution_viability"] = analyze_execution_viability(result.get("trades_log", []))
    from trade_intelligence import analyze_trade_intelligence
    result["trade_intelligence"] = analyze_trade_intelligence(result.get("trades_log", []))
    from trade_forensics import build_trade_forensics
    result["trade_forensics"] = build_trade_forensics(result.get("trades_log", []))
    # Re-run the exact same frozen signal under deterministic execution-cost
    # multipliers. No parameters or signals are fitted to these scenarios.
    stress = []
    for label, multiplier in (("normal", 1.0), ("moderate", 1.5), ("high", 2.0), ("extreme", 3.0)):
        stressed = run_backtest(
            df, S, thr, min(0.499999, fee * multiplier),
            **_execution_cost_kwargs(bt,gate,multiplier), start=effective_start,
            allow_short=allow_short, position_size=position_size,
            market_type=market_type, leverage=leverage,
            funding_fee_per_bar=funding_rate * multiplier,
            atr=_backtest_atr(df, indicators),
            stop_atr_mult=float(bt.get("stop_atr_mult", 1.5)),
            target_atr_mult=float(bt.get("target_atr_mult", 2.0)),
            execution_delay_bars=int(bt.get("execution_delay_bars", 0)),
            market_impact_rt=float(bt.get("market_impact_roundtrip", 0.0))*multiplier,
            entry_allowed=filter_restricted_bars(df, C, tf),
        )
        stress.append({"scenario": label, "cost_multiplier": multiplier,
            "strategy_pct": stressed.get("strategy_pct"), "trades": stressed.get("trades"),
            "net_expectancy_pct": stressed.get("net_expectancy_pct"),
            "net_profit_factor": stressed.get("net_profit_factor"),
            "net_win_rate_pct": stressed.get("net_win_rate_pct"),
            "max_dd_pct": stressed.get("max_dd_pct")})
    result["execution_cost_stress"] = {"scenarios": stress,
        "method": "Same frozen signal and data; configured fee, spread/slippage and funding multiplied; no refitting.",
        "warning": "Sensitivity analysis only. Multipliers are hypothetical and do not estimate actual order-book execution costs."}
    from trade_research import analyze_trade_quality, analyze_rejected_bullish_candidates
    result["trade_research"] = analyze_trade_quality(result.get("trades_log", []))
    result["rejected_signal_research"] = analyze_rejected_bullish_candidates(df, S, thr)
    from research_diagnostics import build_research_diagnostics
    result["research_diagnostics"] = build_research_diagnostics(
        result.get("trades_log", []), result,
        market_type=market_type, position_size=position_size, leverage=leverage,
    )
    result["restricted_hours_filter_applied"] = bool((C.get("restricted_hours") or {}).get("enabled", False))
    result["market_regime_intelligence"] = {
        "engine": "MarketRegimeIntelligence V1.0", "status": "RESEARCH_ONLY",
        "note": "Causal labels sampled at signal close; no signal filtering or parameter fitting."
    }
    result["version"] = "7.2.0"
    result["evaluation_scope"] = "full_window_descriptive_not_out_of_sample"
    result["configured_start"] = configured_start
    result["required_indicator_warmup_bars"] = required_warmup
    result["effective_start"] = effective_start
    result["warmup_adjustment_applied"] = effective_start > configured_start
    result["warmup_bars_used"] = max(0, effective_start + 1)
    result["warmup_shortfall_bars"] = max(0, required_warmup - result["warmup_bars_used"])
    result["warmup_satisfied"] = result["warmup_shortfall_bars"] == 0
    result["first_entry_fill_bar"] = min(len(df) - 1, effective_start + 1)
    result["first_entry_fill_timestamp"] = (
        float(df["time"].iloc[result["first_entry_fill_bar"]])
        if "time" in df.columns and len(df) > result["first_entry_fill_bar"] else None
    )
    if result["warmup_shortfall_bars"]:
        result["warning"] = str(result.get("warning", "")) + (
            f" Indicator warm-up is short by {result['warmup_shortfall_bars']} bars; "
            "treat this small-window run as exploratory, not comparable to fully warmed-up results."
        )
    return result


def _backtest_atr(df, indicators):
    """Use the indicator engine ATR, with a deterministic TR fallback for test/custom signal providers."""
    if isinstance(indicators, dict):
        ctx = indicators.get("x")
        atr = getattr(ctx, "atr", None) if ctx is not None else None
        if atr is not None:
            return np.asarray(atr, dtype=float)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift()).abs(),
        (df["low"] - df["close"].shift()).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / 14, adjust=False, min_periods=1).mean().to_numpy(dtype=float)


def required_indicator_warmup(tf, C=None):
    """Conservative history requirement derived from active indicator lookbacks."""
    C = C or conf()
    P = _merge(DEFAULTS["params"], DEFAULTS["tf_params"].get(tf, {}))
    P = _merge(P, C.get("params") or {})
    P = _merge(P, (C.get("tf_params") or {}).get(tf) or {})
    periods = []
    for values in P.values():
        if isinstance(values, dict):
            for key, value in values.items():
                if key in {"n", "fast", "slow", "sig", "t", "k", "b", "d"}:
                    try:
                        periods.append(int(value))
                    except (TypeError, ValueError):
                        pass
    return max(periods or [2]) + 2


def walk_forward_backtest(df, tf, symbol="", n_splits=None, warmup_bars=None, gap_bars=None,
                          cost_multiplier=1.0, include_diagnostics=True):
    """Evaluate the current frozen signal on sequential out-of-sample windows."""
    from walk_forward import run_walk_forward
    C = conf()
    bt = C.get("backtest") or {}
    wf = C.get("walk_forward") or {}
    gate = C.get("gate") or {}
    market_type = str(bt.get("market_type", "spot")).lower()
    if market_type not in ("spot", "futures"):
        raise ValueError("backtest.market_type must be 'spot' or 'futures'")
    fee = get_fee(C, symbol, market_type) / 2.0
    spread_rt = float(bt.get("spread_slippage_roundtrip", gate.get("spread_slippage_roundtrip", 0.0)))
    multiplier=float(cost_multiplier)
    if not math.isfinite(multiplier) or multiplier < 1.0:
        raise ValueError("cost_multiplier must be finite and >= 1")
    engine_kwargs = {
        "threshold": float(C["verdict_threshold"]),
        "fee_per_side": max(0.0, min(float(fee)*multiplier, 0.499)),
        **_execution_cost_kwargs(bt,gate,multiplier),
        "market_impact_rt": float(bt.get("market_impact_roundtrip", 0.0))*multiplier,
        "execution_delay_bars": int(bt.get("execution_delay_bars", 0)),
        "allow_short": bool(bt.get("allow_short_futures", True)) if market_type == "futures" else False,
        "position_size": float(bt.get("position_size", 1.0)),
        "market_type": market_type,
        "leverage": float(bt.get("futures_leverage", 1.0)) if market_type == "futures" else 1.0,
        "funding_fee_per_bar": (float(bt.get("funding_fee_per_bar", 0.0))*multiplier) if market_type == "futures" else 0.0,
    }
    indicators = compute(df, tf)
    signal = indicators["S"].to_numpy(dtype=float)
    regime_series = indicators.get("regime") if isinstance(indicators, dict) else None
    entry_regime = regime_series.to_numpy(dtype=object) if regime_series is not None else None
    mri_frame = indicators.get("market_regime_intelligence")
    entry_market_regime = mri_frame["regime"].to_numpy(dtype=object) if mri_frame is not None else None
    entry_regime_transition = mri_frame["transition"].to_numpy(dtype=bool) if mri_frame is not None else None
    allowed = filter_restricted_bars(df, C, tf)
    configured_warmup = int(warmup_bars if warmup_bars is not None else wf.get("warmup_bars", 120))
    effective_warmup = max(configured_warmup, required_indicator_warmup(tf, C))
    engine_kwargs.update({
        "atr": _backtest_atr(df, indicators),
        "stop_atr_mult": float(bt.get("stop_atr_mult", 1.5)),
        "target_atr_mult": float(bt.get("target_atr_mult", 2.0)),
    })
    components = {f"group_{k}": v.to_numpy(dtype=float) for k, v in indicators.get("gs", {}).items()}
    components.update({f"indicator_{k}": v.to_numpy(dtype=float) for k, v in indicators.get("sig", {}).items()})
    result = run_walk_forward(
        df, signal, entry_regime=entry_regime, entry_market_regime=entry_market_regime,
        entry_regime_transition=entry_regime_transition,
        entry_allowed=allowed, entry_components=components,
        n_splits=int(n_splits if n_splits is not None else wf.get("n_splits", 5)),
        warmup_bars=effective_warmup,
        gap_bars=int(gap_bars if gap_bars is not None else wf.get("gap_bars", 0)),
        engine_kwargs=engine_kwargs,
        include_diagnostics=bool(include_diagnostics),
    )
    result["stop_atr_mult"] = float(bt.get("stop_atr_mult", 1.5))
    result["target_atr_mult"] = float(bt.get("target_atr_mult", 2.0))
    result["market_type"] = market_type
    result["allow_short"] = bool(engine_kwargs["allow_short"])
    result["position_size"] = float(engine_kwargs["position_size"])
    result["leverage"] = float(engine_kwargs["leverage"])
    result["fee_per_side"] = float(engine_kwargs["fee_per_side"])
    result["spread_slippage_rt"] = _roundtrip_spread_slippage(bt,gate)*multiplier
    result["spread_rt"] = float(engine_kwargs.get("spread_rt", _roundtrip_spread_slippage(bt,gate)*multiplier))
    result["slippage_rt"] = float(engine_kwargs.get("slippage_rt", 0.0))
    result["market_impact_rt"] = float(engine_kwargs["market_impact_rt"])
    result["execution_delay_bars"] = int(engine_kwargs["execution_delay_bars"])
    result["cost_multiplier"] = multiplier
    result["funding_fee_per_bar"] = float(engine_kwargs["funding_fee_per_bar"])
    result["execution_model"] = f"signal close; extra delay {int(engine_kwargs['execution_delay_bars'])} bars; next-open fill; OHLC intrabar ATR stop/target; stop-first if both levels touch"
    result["configured_warmup_bars"] = configured_warmup
    result["required_indicator_warmup_bars"] = required_indicator_warmup(tf, C)
    result["warmup_adjustment_applied"] = effective_warmup > configured_warmup
    result["restricted_hours_config"] = copy.deepcopy(C.get("restricted_hours") or {})
    from signal_forensics import build_signal_forensics
    result["signal_forensics"] = build_signal_forensics(
        result.get("trades_log", []), df,
        threshold=float(C["verdict_threshold"]),
        restricted_hours_enabled=bool((C.get("restricted_hours") or {}).get("enabled", False)),
    )
    result["market_regime_intelligence"] = {
        "engine": "MarketRegimeIntelligence V1.0", "status": "RESEARCH_ONLY",
        "note": "Causal labels sampled at signal close; no signal filtering or parameter fitting."
    }
    result["version"] = "7.2.0"
    result["evaluation_scope"] = "sequential_out_of_sample"
    return result


# ---------------------------------------------------------------- forecast cone
def vol_state(c, lam):
    r = np.diff(np.log(c))
    prev = max(float(np.var(r[:20])) if len(r) >= 20 else float(np.var(r)), 1e-18)
    vs, z = np.empty(len(r)), np.zeros(len(r))
    for j in range(len(r)):
        z[j] = r[j] / math.sqrt(prev)
        prev = max(lam * prev + (1 - lam) * r[j] ** 2, 1e-18)
        vs[j] = prev
    return r, vs, z


def sim_paths(pool, sigma0, sigma_lr, H, mu_bar, paths, block, rng, c0, phi=0.97):
    block = max(1, min(block, H, len(pool)))
    nb = -(-H // block)
    st = rng.integers(0, len(pool) - block + 1, size=(paths, nb))
    zz = pool[(st[:, :, None] + np.arange(block)).reshape(paths, nb * block)[:, :H]]
    sg = sigma_lr + (sigma0 - sigma_lr) * phi ** np.arange(H)
    return c0 * np.exp(np.cumsum(zz * sg + mu_bar, axis=1))


def _std_pool(z):
    return (z - z.mean()) / max(float(z.std()), 1e-9)


def edge_stats(S, groups, c, sigma, H, warm, mask=None):
    n = len(c)
    idx = np.arange(warm, n - H)
    lc = np.log(c)
    fwd = np.full(len(idx), np.nan)
    if len(idx):
        fwd = (lc[idx + H] - lc[idx]) / (sigma[idx] * math.sqrt(H))
    ok = np.isfinite(fwd)
    if mask is not None:
        ok &= np.asarray(mask, dtype=bool)[idx]      # restricted-hours entries are not tradable -> not counted
    idx, fwd = idx[ok], fwd[ok]
    if len(idx) < 30:
        raise ValueError("not enough history to measure the score's edge")

    def stat(sv):
        x = sv[idx]
        if float(np.std(x)) < 1e-9:
            return {"ic": 0.0, "t_eff": 0.0, "beta": 0.0, "hit_pct": None, "n": int(len(x))}
        ic = float(np.corrcoef(x, fwd)[0, 1])
        neff = max(len(x) / H, 3.0)  # overlapping windows -> effective sample ~ n/H
        t = ic * math.sqrt(max(neff - 2, 1)) / math.sqrt(max(1 - ic * ic, 1e-9))
        beta = float(np.cov(x, fwd, ddof=0)[0, 1] / np.var(x))
        m = np.abs(x) >= 0.1
        hit = float(np.mean(np.sign(x[m]) == np.sign(fwd[m])) * 100) if m.sum() >= 10 else None
        return {"ic": round(ic, 3), "t_eff": round(t, 2), "beta": round(beta, 3),
                "hit_pct": None if hit is None else round(hit, 1), "n": int(len(x)), "n_eff": round(neff, 1)}

    return {"aggregate": stat(S), "groups": {g: stat(s) for g, s in groups.items()}}


def cone_calibration(c, r, vs, z, H, warm, L, block):
    rng = np.random.default_rng(1)
    step = max(1, H // 2)
    m80 = m50 = tot = 0
    widths = []
    for t in range(warm, len(c) - H, step):
        zl = z[max(0, t - L):t]
        if len(zl) < 50:
            continue
        P = sim_paths(_std_pool(zl), math.sqrt(vs[t - 1]), float(np.std(r[max(0, t - L):t])),
                      H, 0.0, 150, block, rng, c[t])
        fin = P[:, -1]
        q10, q25, q75, q90 = np.percentile(fin, [10, 25, 75, 90])
        a = c[t + H]
        m80 += q10 <= a <= q90
        m50 += q25 <= a <= q75
        widths.append((q90 - q10) / c[t])
        tot += 1
    if tot < 10:
        return None
    return {"n": tot, "cover80_pct": round(100 * float(m80) / tot, 1), "cover50_pct": round(100 * float(m50) / tot, 1),
            "avg_width80_pct": round(100 * float(np.mean(widths)), 2), "note": "measured with zero drift"}


def _first_hit(P, t1, stop):
    H = P.shape[1]
    up, dn = P >= t1, P <= stop
    iu = np.where(up.any(1), up.argmax(1), H + 1)
    idn = np.where(dn.any(1), dn.argmax(1), H + 1)
    return float(np.mean(iu < idn)), float(np.mean(idn < iu))


def _r(a, d=6):
    return [round(float(x), d) for x in a]


def scenario_ranking(paths_matrix, last_price, round_trip_fee):
    """Rank terminal Monte Carlo outcomes into mutually exclusive scenarios.

    The reported percentages are simulation path frequencies, NOT calibrated
    real-world probabilities. The neutral band is the configured round-trip
    fee (a minimum economically meaningful move); spread/slippage are not
    included because this app does not have reliable per-fill estimates.
    """
    P = np.asarray(paths_matrix, dtype=float)
    if P.ndim != 2 or P.shape[0] < 1 or P.shape[1] < 1:
        raise ValueError("paths_matrix must be a non-empty 2D array")
    if not np.isfinite(P).all() or not np.isfinite(last_price) or last_price <= 0:
        raise ValueError("paths and last_price must be finite; last_price must be positive")
    fee = float(round_trip_fee)
    if not np.isfinite(fee) or fee < 0:
        raise ValueError("round_trip_fee must be a finite non-negative fraction")

    terminal = P[:, -1]
    ret = terminal / float(last_price) - 1.0
    band = fee
    eps = 1e-12  # keep exact fee-boundary paths neutral despite float representation
    masks = {
        "Bullish": ret > band + eps,
        "Sideways": (ret >= -band - eps) & (ret <= band + eps),
        "Bearish": ret < -band - eps,
    }
    n = int(len(ret))
    rows = []
    counts = {name: int(mask.sum()) for name, mask in masks.items()}
    raw_pct = {name: 100.0 * counts[name] / n for name in masks}
    rounded = {name: round(value, 1) for name, value in raw_pct.items()}
    # Make displayed shares add to exactly 100.0 despite one-decimal rounding.
    largest = max(masks, key=lambda name: counts[name])
    rounded[largest] = round(rounded[largest] + (100.0 - sum(rounded.values())), 1)

    for name, mask in masks.items():
        vals = terminal[mask]
        count = int(mask.sum())
        band_prices = None
        avg_return_pct = None
        if count:
            q10, q50, q90 = np.percentile(vals, [10, 50, 90])
            band_prices = {"p10": round(float(q10), 6), "p50": round(float(q50), 6),
                           "p90": round(float(q90), 6)}
            avg_return_pct = round(float(np.mean(ret[mask]) * 100.0), 3)
        p = counts[name] / n
        rows.append({
            "scenario": name,
            "count": count,
            "probability_pct": rounded[name],
            "simulation_se_pct": round(100.0 * math.sqrt(p * (1.0 - p) / n), 2),
            "conditional_terminal_price": band_prices,
            "conditional_mean_return_pct": avg_return_pct,
        })
    rows.sort(key=lambda row: (-counts[row["scenario"]],
                               {"Sideways": 0, "Bullish": 1, "Bearish": 2}[row["scenario"]]))
    for rank, row in enumerate(rows, 1):
        row["rank"] = rank
        row["is_most_likely"] = rank == 1

    return {
        "ranking": rows,
        "top_scenario": rows[0]["scenario"],
        "simulation_paths": n,
        "neutral_band_pct": round(band * 100.0, 3),
        "probability_type": "uncalibrated_monte_carlo_path_frequency",
        "calibration_status": "not_calibrated_against_resolved_out_of_sample_predictions",
        "note": ("Percentages are shares of this Monte Carlo simulation, not validated real-world odds. "
                 "The neutral band equals the configured round-trip fee; spread and slippage are not included."),
    }


def forecast(df, tf, hours, paths, C, symbol="", source="ramzinex"):
    from data_integrity import audit_ohlcv
    data_audit = audit_ohlcv(
        df, TF_MIN[tf] * 60,
        check_stale=source in ("ramzinex", "udf", "ccxt"),
    )
    if not data_audit.get("valid", False):
        errors = "; ".join(data_audit.get("errors", [])) or "unknown OHLCV integrity failure"
        raise ValueError("Forecast blocked by OHLCV integrity gate: " + errors)
    pc = C["projection"]
    c = df.close.values
    n = len(c)
    H = int(min(pc["max_bars"], max(1, round(hours * 60 / TF_MIN[tf]))))
    warm, L = int(proj_param(pc, "warm", tf, 120)), int(proj_param(pc, "lookback", tf, 300))
    block = int(proj_param(pc, "block", tf, 5))
    if n < warm + H + 80:
        raise ValueError(f"need at least {warm + H + 80} candles for this horizon (got {n})")
    res = compute(df, tf)
    summ = summarize(res, df, C)
    r, vs, z = vol_state(c, float(proj_param(pc, "lambda", tf, 0.94)))
    sigma = np.concatenate([[np.nan], np.sqrt(vs)])
    tradable = filter_restricted_bars(df, C, tf)
    edge = edge_stats(res["S"].values, {g: s.values for g, s in res["gs"].items()}, c, sigma, H, warm,
                      mask=None if tradable.all() else tradable)
    ag = edge["aggregate"]
    use = ag["t_eff"] >= float(pc["min_t"]) and ag["beta"] > 0
    beta_used = ag["beta"] * float(pc["beta_shrink"]) if use else 0.0
    if use:
        drift_note = "score shows a statistically significant positive relation to forward returns; drift applied (shrunk)"
    elif ag["t_eff"] <= -float(pc["min_t"]):
        drift_note = "score is significantly NEGATIVELY related to forward returns here - not used (likely data-mining); drift = 0"
    else:
        drift_note = "no demonstrable edge in this history: drift = 0, the cone is pure volatility"
    S_last = float(res["S"].iloc[-1])
    last = float(c[-1])
    sigma0 = math.sqrt(vs[-1])
    Ls = min(L, len(z) - 1)
    pool = _std_pool(z[-Ls:])
    sigma_lr = float(np.std(r[-Ls:]))
    mu_bar = beta_used * S_last * sigma0 * math.sqrt(H) / H
    rng = np.random.default_rng(int(pc["seed"]))
    P = sim_paths(pool, sigma0, sigma_lr, H, mu_bar, int(paths), block, rng, last)
    q = np.percentile(P, [10, 25, 50, 75, 90], axis=0)
    scenario_probs = scenario_ranking(P, last, get_fee(C, symbol))
    order = np.argsort(P[:, -1])
    picks = [order[int(f * (len(order) - 1))] for f in (0.15, 0.5, 0.85)]
    tsec = TF_MIN[tf] * 60
    t0 = int(df.time.iloc[-1])
    lv = levels(df, summ["atr"])
    plan = make_plan(summ, lv, last)
    plan_hit = None
    if plan.get("stop"):
        pt, ps = _first_hit(P, plan["t1"], plan["stop"])
        plan_hit = {"t1_first_pct": round(100 * pt, 1), "stop_first_pct": round(100 * ps, 1),
                    "neither_pct": round(100 * (1 - pt - ps), 1),
                    "note": "closes only (no intrabar highs/lows), entry assumed at last price"}
    cal = cone_calibration(c, r, vs, z, H, warm, L, block)
    fin = P[:, -1]
    pct = lambda v: round(100 * (float(v) / last - 1), 2)
    summary = {
        "tf": tf, "horizon_bars": H, "horizon_hours": round(H * TF_MIN[tf] / 60, 2), "last": last,
        "score_now": summ["score"], "paths": int(paths),
        "p_up_pct": round(100 * float(np.mean(fin > last)), 1),
        "q10": round(float(q[0, -1]), 6), "q50": round(float(q[2, -1]), 6), "q90": round(float(q[4, -1]), 6),
        "q10_pct": pct(q[0, -1]), "q50_pct": pct(q[2, -1]), "q90_pct": pct(q[4, -1]),
        "drift_used": bool(use), "beta_used": round(beta_used, 3), "drift_note": drift_note,
        "edge": edge, "calibration": cal, "plan_hit": plan_hit,
        "data_integrity": data_audit,
        "scenario_probabilities": scenario_probs,
        "warning": "Statistical cone from past volatility. It is NOT a forecast of the actual path; "
                   "news, policy moves and jumps (common in IRR markets) are not modelled.",
    }
    summary["plan"] = plan
    summary["verdict"] = summ["verdict"]
    summary["bar_ts"] = t0
    summary["source"] = source
    summary["gate"] = make_gate(summary, plan, last, C, symbol, source=source)
    cone = {"h": H, "t": [t0 + tsec * k for k in range(1, H + 1)],
            "q10": _r(q[0]), "q25": _r(q[1]), "q50": _r(q[2]), "q75": _r(q[3]), "q90": _r(q[4]),
            "sc": [_r(P[i]) for i in picks]}
    return {"cone": cone, "summary": summary}


# ---------------------------------------------------------------- api
def make_gate(summary, plan, last, C, symbol="", source="ramzinex"):
    d = _make_gate(summary, plan, last, C, symbol)
    tf = summary.get("tf")
    if (C.get("gate") or {}).get("require_oos_validation", True):
        from research_provenance import config_fingerprint
        from strategy_validation import deployment_status
        config_hash = config_fingerprint(_research_execution_config(tf, symbol, None, C))
        validation = deployment_status(
            VALIDATION_EVIDENCE_PATH, source=source, symbol=symbol, timeframe=tf,
            config_hash=config_hash, policy=C.get("gate") or {},
        )
        d["deployment_validation"] = validation
        if not validation.get("eligible", False):
            reason = "OOS strategy validation gate: " + " ".join(validation.get("reasons") or ["evidence unavailable"])
            d["state"] = "NO_TRADE"
            d["confidence_cap"] = "low"
            d["reasons"] = list(d.get("reasons") or []) + [reason]
    if tf in TF_MIN and is_restricted(time.time(), C, tf):
        r = C["restricted_hours"]
        d = dict(d, state="NO_TRADE", confidence_cap="low",
                 reasons=list(d.get("reasons") or []) + [f"inside restricted trading hours ({r['start']}-{r['end']}, UTC{int(r.get('tz_offset_min', 210)) / 60:+g})"])
    return d


def _make_gate(summary, plan, last, C, symbol=""):
    g = C.get("gate") or {}
    bt = C.get("backtest") or {}
    market_type = str(bt.get("market_type", "spot")).lower()
    kw = dict(fee_rt=get_fee(C, symbol, market_type),
              min_t=float(g.get("min_t", 2.0)), min_rr=float(g.get("min_rr", 1.5)),
              spread_slippage_rt=_roundtrip_spread_slippage(bt,g),
              min_probability_samples=int(g.get("min_edge_samples", 30)),
              edge_n=summary.get("edge", {}).get("aggregate", {}).get("n"))
    t_eff = float(summary["edge"]["aggregate"]["t_eff"])
    ph = summary.get("plan_hit")
    if plan.get("stop") and ph:
        return AC.decide(t_eff, last, plan["stop"], plan["t1"],
                         ph["t1_first_pct"] / 100.0, ph["stop_first_pct"] / 100.0, **kw)
    return AC.decide(t_eff, None, None, None, 0.0, 0.0, **kw)


def _price_at(key, tf, ts):
    """Close of the candle that opened at ts, only once that candle is complete."""
    source, _, symbol = key.partition(":")
    tsec = TF_MIN[tf] * 60
    now = time.time()
    if now < ts + tsec:
        return None
    ck = (key, tf)
    hit = _PA_CACHE.get(ck)
    if hit and now - hit[0] < 60 and float(hit[1].time.min()) <= ts:
        df = hit[1]
    else:
        need = int((now - ts) / tsec) + 10
        if need > 5000:
            return None
        df = load(source, symbol, tf, need)
        _PA_CACHE[ck] = (now, df)
    row = df[df.time == ts]
    return float(row.close.iloc[0]) if len(row) else None


_PA_CACHE = {}


def journal_once(w, seen):
    source, symbol, tf = w["source"], w["symbol"], w["tf"]
    C = conf()
    df = load_cached(source, symbol, tf, int(C["projection"]["calib_bars"]), "coinex", "", "")
    bar = int(df.time.iloc[-1])
    key = f"{source}:{symbol}"
    hk = (key, tf, float(w.get("hours", 12)))
    if seen.get(hk) == bar:
        return
    out = forecast(df, tf, float(w.get("hours", 12)), int(C["projection"]["paths"]), C,
                   symbol=symbol, source=source)
    s = out["summary"]
    # Self-learning is deliberately journal-only: it learns from already-resolved,
    # out-of-sample outcomes and never trains on pending forecasts.
    p_raw = s["p_up_pct"] / 100.0
    learn = AC.calibrate_probability(key, tf, s["horizon_hours"], p_raw)
    p_logged = learn["probability"]
    AC.log_prediction(key, tf, bar, s["horizon_hours"], s["last"], p_logged,
                      s["q10"], s["q50"], s["q90"], s["edge"]["aggregate"]["t_eff"], s["verdict"],
                      score=s["score_now"], gate=s["gate"]["state"], plan=s.get("plan"),
                      extra={"drift_used": s["drift_used"], "ev": (s["gate"].get("ev") or {}).get("ev"),
                             "p_up_raw": p_raw, "adaptive_calibration": learn})
    seen[hk] = bar


def journal_items(C):
    out = []
    grid = [g for g in (C.get("journal_grid") or []) if isinstance(g, (list, tuple)) and len(g) == 2]
    for sym in (C.get("journal_symbols") or []):
        for tf, hours in grid:
            out.append({"source": "ramzinex", "symbol": sym, "tf": tf, "hours": hours})
    out += [w for w in (C.get("journal_watch") or []) if isinstance(w, dict)]
    return out


def journal_loop():
    seen, fails = {}, {}
    while True:
        C = conf()
        for w in journal_items(C):
            sym = str(w.get("symbol", ""))
            if time.time() < fails.get(sym, (0, 0))[1]:
                continue                      # symbol in backoff after repeated errors
            try:
                if (w.get("source") == "ramzinex" and w.get("tf") in TF_MIN and SYM_RE.match(sym)):
                    journal_once(w, seen)
                    fails.pop(sym, None)
            except Exception as e:
                n = fails.get(sym, (0, 0))[0] + 1
                fails[sym] = (n, time.time() + min(3600, 120 * 2 ** n))
                print("[journal]", sym, w.get("tf"), redact(f"{type(e).__name__}: {e}")[:160],
                      f"- retry in {min(3600, 120 * 2 ** n) // 60} min")
        try:
            AC.resolve_due(_price_at)
        except Exception as e:
            print("[journal-resolve]", redact(f"{type(e).__name__}: {e}")[:160])
        time.sleep(max(30, int(C.get("journal_poll_sec", 120))))


@app.get("/api/journal")
def api_journal(symbol: str = "ramzinex:USDTIRR", tf: str = "1h", horizon: float = 0.0):
    try:
        return AC.evaluate(symbol, tf, horizon or None)
    except Exception as e:
        raise HTTPException(400, redact(f"{type(e).__name__}: {e}"))


@app.get("/api/iran-crypto/status")
def api_iran_crypto_status():
    return iran_crypto_market().status()


def _iran_mcp_call(method, **kwargs):
    settings = conf().get("iran_crypto_mcp") or {}
    if not settings.get("enabled", False):
        raise HTTPException(503, "Iran Crypto MCP is disabled. Set iran_crypto_mcp.enabled=true in config.json.")
    try:
        return getattr(iran_crypto_market(), method)(**kwargs)
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, redact(f"{type(exc).__name__}: {exc}"))
    except Exception as exc:
        # Child-process stderr and upstream exceptions may contain local paths or secrets.
        print("[iran-mcp]", redact(f"{type(exc).__name__}: {exc}")[:300])
        raise HTTPException(502, "Iran Crypto MCP unavailable; inspect the local application console.")


@app.get("/api/iran-crypto/compare")
def api_iran_crypto_compare(coin: str = "BTC", quote: str = "IRT"):
    coin = str(coin).upper().strip()
    quote = str(quote).upper().strip()
    if not re.fullmatch(r"[A-Z0-9]{2,15}", coin) or quote not in ("IRT", "USDT"):
        raise HTTPException(400, "coin or quote is invalid")
    return {"source": "iran-crypto-mcp", "read_only": True, "quote": quote,
            "retrieved_at": time.time(), "data": _iran_mcp_call("compare", coin=coin, quote=quote)}


@app.get("/api/iran-crypto/orderbook")
def api_iran_crypto_orderbook(coin: str = "BTC", exchange: str = "wallex", quote: str = "IRT", levels: int = 10):
    coin, exchange, quote = str(coin).upper().strip(), str(exchange).lower().strip(), str(quote).upper().strip()
    if not re.fullmatch(r"[A-Z0-9]{2,15}", coin) or not re.fullmatch(r"[a-z0-9_]{2,24}", exchange) or quote not in ("IRT", "USDT"):
        raise HTTPException(400, "coin, exchange or quote is invalid")
    return {"source": "iran-crypto-mcp", "read_only": True, "retrieved_at": time.time(),
            "data": _iran_mcp_call("orderbook", coin=coin, exchange=exchange, quote=quote, levels=levels)}


@app.get("/api/iran-crypto/candles")
def api_iran_crypto_candles(coin: str = "BTC", exchange: str = "wallex", quote: str = "IRT", timeframe: str = "1h", limit: int = 100):
    coin, exchange, quote = str(coin).upper().strip(), str(exchange).lower().strip(), str(quote).upper().strip()
    if not re.fullmatch(r"[A-Z0-9]{2,15}", coin) or not re.fullmatch(r"[a-z0-9_]{2,24}", exchange) or quote not in ("IRT", "USDT"):
        raise HTTPException(400, "coin, exchange or quote is invalid")
    return {"source": "iran-crypto-mcp", "read_only": True, "retrieved_at": time.time(),
            "data": _iran_mcp_call("candles", coin=coin, exchange=exchange, quote=quote, timeframe=timeframe, limit=limit)}


@app.get("/api/analyze")
def api_analyze(source: str = "ramzinex", symbol: str = "USDTIRR", tf: str = "1h", n: int = 500,
                exchange: str = "coinex", udf_url: str = "", csv_path: str = ""):
    try:
        C = conf()
        df = load_cached(source, symbol, tf, n, exchange, udf_url, csv_path)
        from data_integrity import audit_ohlcv
        data_audit = audit_ohlcv(
            df, TF_MIN[tf] * 60,
            check_stale=source in ("ramzinex", "udf", "ccxt"),
        )
        if not data_audit.get("valid", False):
            errors = "; ".join(data_audit.get("errors", [])) or "unknown OHLCV integrity failure"
            raise ValueError("Analysis blocked by OHLCV integrity gate: " + errors)
        res = summarize(compute(df, tf), df, C)
    except Exception as e:
        raise HTTPException(400, redact(f"{type(e).__name__}: {e}"))
    res["data_integrity"] = data_audit
    res["candles"] = df[["time", "open", "high", "low", "close"]].values.tolist()
    res["last"] = float(df.close.iloc[-1])
    res["levels"] = levels(df, res["atr"])
    res["plan"] = make_plan(res, res["levels"], res["last"])
    res["plan"] = apply_integrity_gate(res["plan"], data_audit, res.get("indicator_integrity", {}))
    return res


@app.get("/api/mtf")
def api_mtf(source: str = "ramzinex", symbol: str = "USDTIRR", exchange: str = "coinex",
            udf_url: str = "", csv_path: str = ""):
    C = conf()
    if source == "csv":
        return {"rows": [], "alignment": "n/a - a csv file has a single timeframe", "combined": None}
    tfl = [t for t in C["tf_list"] if t in TF_MIN]

    def one(tf):
        try:
            df = load_cached(source, symbol, tf, 400, exchange, udf_url, csv_path)
            s = summarize(compute(df, tf), df, C)
            return {"tf": tf, "verdict": s["verdict"], "score": s["score"], "regime": s["regime"],
                    "groups": {g: v["score"] for g, v in s["groups"].items()}}
        except Exception as e:
            return {"tf": tf, "error": redact(str(e))[:80]}

    with ThreadPoolExecutor(max_workers=4) as ex:
        rows = list(ex.map(one, tfl))
    ok = [r for r in rows if "score" in r]
    W = C["tf_weights"]
    tw = sum(float(W.get(r["tf"], 0)) for r in ok)
    comb = sum(float(W.get(r["tf"], 0)) * r["score"] for r in ok) / tw if tw > 0 else None
    v = [r["verdict"] for r in ok]
    al = ("aligned bullish" if v and all(x == "Bullish" for x in v) else
          "aligned bearish" if v and all(x == "Bearish" for x in v) else "mixed - low confluence")
    return {"rows": rows, "alignment": al, "combined": None if comb is None else round(comb, 3),
            "combined_verdict": None if comb is None else verdict_of(comb, float(C["verdict_threshold"])),
            "method": "indicator_group_composite",
            "interpretation": "Indicator consensus only; it is not the structural trend score from /api/trend or /api/trend_mtf."}


def _trend_pack(source, symbol, tf, exchange, udf_url, csv_path, C, overlay=True):
    tc = C.get("trend") or {}
    df = load_cached(source, symbol, tf, int(tc.get("bars", 1000)), exchange, udf_url, csv_path)
    out = TE.analyse(df, tf, time.time(), tc)
    S = out.pop("_series")
    if not overlay:
        out.pop("overlay", None)
    return out, S


def trend_edge(S, tf, C):
    """Does the trend score relate to forward returns in THIS history? (same test as the main score, plus a naive control)"""
    pc = C["projection"]
    H = int((C.get("trend") or {}).get("edge_bars", 5))
    c, lam = S["c"], float(proj_param(pc, "lambda", tf, 0.94))
    r, vs, z = vol_state(c, lam)
    sigma = np.concatenate([[np.nan], np.sqrt(vs)])
    warm = int(proj_param(pc, "warm", tf, 120))
    try:
        a = edge_stats(S["score"], {}, c, sigma, H, warm)["aggregate"]
    except ValueError as e:
        return {"available": False, "reason": str(e)}
    naive = np.tanh(S["zs"][len(S["zs"]) // 2] / 1.5)
    try:
        b = edge_stats(naive, {}, c, sigma, H, warm)["aggregate"]
    except ValueError:
        b = None
    ok = a["t_eff"] >= float(pc["min_t"])
    note = ("in-sample association with the next %d bars is statistically visible (t_eff >= %.1f) - still unproven out of sample; "
            "the live journal is the judge" % (H, float(pc["min_t"]))) if ok else \
           ("NO demonstrated predictive edge on this history (t_eff %.2f < %.1f): read the trend block as a description, not a signal"
            % (a["t_eff"], float(pc["min_t"])))
    return {"available": True, "horizon_bars": H, "ic": a["ic"], "t_eff": a["t_eff"], "hit_pct": a["hit_pct"],
            "n_eff": a.get("n_eff"), "naive_momentum_ic": None if b is None else b["ic"],
            "naive_momentum_t": None if b is None else b["t_eff"], "significant": bool(ok), "note": note}


@app.get("/api/trend")
def api_trend(source: str = "ramzinex", symbol: str = "USDTIRR", tf: str = "1d", exchange: str = "coinex",
              udf_url: str = "", csv_path: str = ""):
    try:
        C = conf()
        if tf not in TE.TF_SEC:
            raise ValueError("unknown timeframe")
        out, S = _trend_pack(source, symbol, tf, exchange, udf_url, csv_path, C)
        out["edge"] = trend_edge(S, tf, C)
        return out
    except Exception as e:
        raise HTTPException(400, redact(f"{type(e).__name__}: {e}"))


@app.get("/api/trend_mtf")
def api_trend_mtf(source: str = "ramzinex", symbol: str = "USDTIRR", exchange: str = "coinex",
                  udf_url: str = "", csv_path: str = ""):
    C = conf()
    tc = C.get("trend") or {}
    if source == "csv":
        return {"rows": [], "relation": "n/a - a csv file has a single timeframe", "combined": None}
    tfl = [t for t in tc.get("tf_list", ["4h", "12h", "1d"]) if t in TE.TF_SEC]

    def one(tf):
        try:
            o, _ = _trend_pack(source, symbol, tf, exchange, udf_url, csv_path, C, overlay=False)
            return {k: o[k] for k in ("tf", "score", "direction", "label", "trendiness", "conf_label", "structure",
                                      "invalidation", "last_closed_t")}
        except Exception as e:
            return {"tf": tf, "error": redact(str(e))[:80]}

    with ThreadPoolExecutor(max_workers=3) as ex:
        rows = list(ex.map(one, tfl))
    out = TE.combine(rows, tc.get("tf_weights") or {}, float(tc.get("thr", 0.20)))
    out["rows"] = rows
    return out


def _research_execution_config(tf, symbol, result=None, C=None):
    """Canonical config derived only from the active config used by the engine.

    Runtime result fields are deliberately ignored: provenance and the live gate
    must fingerprint the exact same configuration, never a report-side override.
    """
    C = _merge(DEFAULTS, C if C is not None else conf())
    bt = C.get("backtest") or {}
    market_type = str(bt.get("market_type", "spot")).lower()
    allow_short = bool(bt.get("allow_short_futures", True)) if market_type == "futures" else False
    leverage = float(bt.get("futures_leverage", 1.0)) if market_type == "futures" else 1.0
    fee = get_fee(C, symbol, market_type) / 2.0
    return {
        "threshold": float(C.get("verdict_threshold", 0.20)),
        "indicators": params_for(tf),
        "indicator_groups": groups_for(tf, C),
        "regime_weights": C.get("regime_weights", {}),
        "market_type": market_type,
        "allow_short": allow_short,
        "position_size": float(bt.get("position_size", 1.0)),
        "leverage": leverage,
        "fee_per_side": float(fee),
        "spread_slippage_roundtrip": _roundtrip_spread_slippage(bt,C.get("gate") or {}),
        "spread_roundtrip": bt.get("spread_roundtrip"),
        "slippage_roundtrip": bt.get("slippage_roundtrip"),
        "market_impact_roundtrip": float(bt.get("market_impact_roundtrip", 0.0)),
        "execution_delay_bars": int(bt.get("execution_delay_bars", 0)),
        "funding_fee_per_bar": float(bt.get("funding_fee_per_bar", 0.0) if market_type == "futures" else 0.0),
        "stop_atr_mult": float(bt.get("stop_atr_mult", 1.5)),
        "target_atr_mult": float(bt.get("target_atr_mult", 2.0)),
        "restricted_hours": C.get("restricted_hours", {}),
    }


def _audit_research_frame(df, source, tf):
    from data_integrity import audit_ohlcv
    audit = audit_ohlcv(df, TF_MIN[tf] * 60, check_stale=source in ("ramzinex", "udf", "ccxt"))
    if not audit.get("valid", False):
        issues = "; ".join(audit.get("errors", [])) or "unknown OHLCV integrity failure"
        raise ValueError("Research run blocked by OHLCV integrity gate: " + issues)
    return audit


def _attach_research_provenance(result, df, *, source, symbol, tf, requested_n, audit, scope):
    from research_provenance import build_provenance
    C = conf()
    result.setdefault("market_type", str((C.get("backtest") or {}).get("market_type", "spot")).lower())
    result.update({"source": source, "symbol": symbol, "timeframe": tf, "candles": len(df)})
    result["data_integrity"] = audit
    result["provenance"] = build_provenance(
        df, source=source, symbol=symbol, timeframe=tf,
        requested_candles=requested_n, market_type=result.get("market_type", "spot"),
        strategy_config=_research_execution_config(tf, symbol, result, C),
        data_integrity=audit, evaluation_scope=scope,
    )
    return result


def _guard_holdout_research_access(df, source, symbol, tf):
    """Prevent ordinary research endpoints from consuming the sealed final holdout."""
    from holdout_vault import HoldoutVault
    if len(df) and HoldoutVault(HOLDOUT_VAULT_PATH).overlaps(
        source=source, symbol=symbol, timeframe=tf,
        start_timestamp=float(df["time"].iloc[0]), end_timestamp=float(df["time"].iloc[-1])):
        raise HTTPException(403, "This sample overlaps the sealed final holdout. Use the single-use holdout evaluation endpoint.")


@app.get("/api/backtest")
def api_backtest(source: str = "ramzinex", symbol: str = "USDTIRR", tf: str = "1h", n: int = 1200,
                 fee: float = -1.0, exchange: str = "coinex", udf_url: str = "", csv_path: str = ""):
    """Full-window descriptive run; its OHLCV sample and config are fingerprinted."""
    try:
        count = max(100, min(int(n), 5000))
        df = load_cached(source, symbol, tf, count, exchange, udf_url, csv_path)
        audit = _audit_research_frame(df, source, tf)
        result = backtest(df, tf, None if fee < 0 else fee, symbol=symbol)
        return _attach_research_provenance(
            result, df, source=source, symbol=symbol, tf=tf,
            requested_n=count, audit=audit,
            scope="full_window_descriptive_not_out_of_sample",
        )
    except Exception as e:
        raise HTTPException(400, redact(f"{type(e).__name__}: {e}"))


@app.get("/api/walk-forward")
def api_walk_forward(source: str = "ramzinex", symbol: str = "USDTIRR", tf: str = "1h", n: int = 1200,
                     n_splits: int = 0, warmup_bars: int = 0, gap_bars: int = -1,
                     exchange: str = "coinex", udf_url: str = "", csv_path: str = ""):
    """Frozen-signal sequential OOS validation on the requested, fingerprinted window."""
    try:
        C = conf()
        wf = C.get("walk_forward") or {}
        count = max(100, min(int(n), 5000))
        df = load_cached(source, symbol, tf, count, exchange, udf_url, csv_path)
        audit = _audit_research_frame(df, source, tf)
        result = walk_forward_backtest(
            df, tf, symbol=symbol,
            n_splits=None if int(n_splits) <= 0 else max(2, min(int(n_splits), 20)),
            warmup_bars=None if int(warmup_bars) <= 0 else max(2, min(int(warmup_bars), count - 64)),
            gap_bars=None if int(gap_bars) < 0 else max(0, min(int(gap_bars), count - 64)),
        )
        stress_result = walk_forward_backtest(
            df, tf, symbol=symbol,
            n_splits=None if int(n_splits) <= 0 else max(2, min(int(n_splits), 20)),
            warmup_bars=None if int(warmup_bars) <= 0 else max(2, min(int(warmup_bars), count - 64)),
            gap_bars=None if int(gap_bars) < 0 else max(0, min(int(gap_bars), count - 64)),
            cost_multiplier=1.5, include_diagnostics=False,
        )
        result["cost_stress_1_5x"] = {
            "net_expectancy_pct": stress_result.get("net_expectancy_pct"),
            "net_profit_factor": stress_result.get("net_profit_factor"),
            "strategy_pct": stress_result.get("strategy_pct"),
            "trades": stress_result.get("trades"),
            "positive_expectancy": bool(stress_result.get("net_expectancy_pct") is not None and stress_result["net_expectancy_pct"] > 0),
            "method": "exact chronological Walk-Forward rerun with fee, spread/slippage, market impact and funding multiplied by 1.5",
        }
        result["cost_stress_1_5x_positive"] = result["cost_stress_1_5x"]["positive_expectancy"]
        result.update({
            "configured_n_splits": int(n_splits) if int(n_splits) > 0 else int(wf.get("n_splits", 5)),
            "configured_warmup_bars": int(warmup_bars) if int(warmup_bars) > 0 else int(wf.get("warmup_bars", 120)),
        })
        result = _attach_research_provenance(
            result, df, source=source, symbol=symbol, tf=tf,
            requested_n=count, audit=audit,
            scope="sequential_out_of_sample",
        )
        from strategy_validation import build_evidence_record, evaluate_evidence, save_evidence
        record = build_evidence_record(
            result, source=source, symbol=symbol, timeframe=tf,
            config_hash=result["provenance"]["config_fingerprint"],
        )
        # Every completed frozen-signal OOS run is registered, including failures.
        # Identical run IDs are idempotent and do not inflate the experiment count.
        from research_registry import ResearchRegistry
        from research_statistics import moving_block_bootstrap, block_bootstrap_mean_test
        registry = ResearchRegistry(RESEARCH_REGISTRY_PATH)
        strategy_spec = result.get("provenance", {}).get("strategy_config", {})
        config_hash = result["provenance"]["config_fingerprint"]
        import hashlib
        market_identity = f"{source}|{symbol.upper()}|{tf}|{config_hash}"
        hypothesis_id = "HYP-" + hashlib.sha256(market_identity.encode("utf-8")).hexdigest()[:20]
        registry.register(hypothesis_id, "Frozen ChartAnalyzer signal", {
            "kind": "frozen_existing_signal", "strategy_config": strategy_spec,
            "source_scope": source, "symbol_scope": symbol, "timeframe": tf,
            "registered_by": "walk_forward_endpoint"
        }, family_id="chartanalyzer_global_research")
        trade_returns = [float(t.get("net_ret_pct")) for t in (result.get("trades_log") or [])
                         if isinstance(t, dict) and t.get("net_ret_pct") is not None]
        inference = {"bootstrap_ci_95_pct": None, "p_value": None, "method": "unavailable"}
        if len(trade_returns) >= 2:
            boot = moving_block_bootstrap(trade_returns, block_length=min(20, max(2, int(round(len(trade_returns) ** 0.5)))), n_boot=3000)
            test = block_bootstrap_mean_test(trade_returns, block_length=min(20, max(2, int(round(len(trade_returns) ** 0.5)))), n_boot=3000)
            inference = {"bootstrap_ci_95_pct": boot["ci"], "bootstrap_mean_pct": boot["mean"],
                         "p_value": test["p_value"], "method": test["method"], "sample_trades": len(trade_returns)}
        result["research_inference"] = inference
        exp_id = str(result.get("provenance", {}).get("run_id") or (hypothesis_id + "-" + str(int(time.time()))))
        registry.record(
            experiment_id=exp_id, hypothesis_id=hypothesis_id,
            dataset_id=str(result.get("provenance", {}).get("dataset_id") or "unknown"),
            config_hash=config_hash, source=source, symbol=symbol, timeframe=tf,
            run_id=result.get("provenance", {}).get("run_id"),
            sample_start=result.get("provenance", {}).get("first_timestamp"),
            sample_end=result.get("provenance", {}).get("last_timestamp"),
            metrics={"trades": int(result.get("trades", 0)),
                     "net_expectancy_pct": result.get("net_expectancy_pct"),
                     "net_profit_factor": result.get("net_profit_factor"),
                     "strategy_pct": result.get("strategy_pct"),
                     "positive_folds": sum(1 for f in result.get("folds", []) if float(f.get("strategy_pct") or 0) > 0),
                     "fold_count": len(result.get("folds", [])),
                     "bootstrap_ci_95_pct": inference.get("bootstrap_ci_95_pct"),
                     "cost_stress_1_5x_positive": result.get("cost_stress_1_5x_positive"),
                     "cost_stress_1_5x": result.get("cost_stress_1_5x")},
            p_value=inference.get("p_value"), status="COMPLETED")
        result["multiple_testing"] = registry.apply_fdr("chartanalyzer_global_research")
        registry_summary = registry.summary()
        current_exp = next((x for x in registry_summary.get("recent_experiments", []) if x.get("experiment_id") == exp_id), None)
        adjusted_p = current_exp.get("adjusted_p_value") if current_exp else None
        record["raw_p_value"] = inference.get("p_value")
        record["adjusted_p_value"] = adjusted_p
        record["max_oos_drawdown_pct"] = result.get("max_dd_pct")
        save_evidence(VALIDATION_EVIDENCE_PATH, record)
        result["deployment_eligibility"] = evaluate_evidence(record, C.get("gate") or {})
        result["research_registry"] = registry_summary
        return result
    except Exception as e:
        raise HTTPException(400, redact(f"{type(e).__name__}: {e}"))


@app.get("/api/research-lab/permutation")
def api_research_permutation(source: str = "ramzinex", symbol: str = "USDTIRR", tf: str = "1h",
                             n: int = 1200, permutations: int = 500, block_bars: int = 24,
                             exchange: str = "coinex", udf_url: str = "", csv_path: str = ""):
    """Entry-timing block permutation control; intentionally excludes ATR exit simulation."""
    try:
        from research_statistics import block_signal_permutation
        C = conf(); count = max(200, min(int(n), 5000))
        df = load_cached(source, symbol, tf, count, exchange, udf_url, csv_path)
        _audit_research_frame(df, source, tf)
        indicators = compute(df, tf)
        score = indicators["S"].to_numpy(dtype=float)
        threshold = float(C.get("verdict_threshold", 0.20))
        signal = (score[:-2] >= threshold).astype(float)
        opens = df["open"].to_numpy(dtype=float)
        forward = opens[2:] / opens[1:-1] - 1.0
        bt = C.get("backtest") or {}
        delay = int(bt.get("execution_delay_bars", 0))
        if delay:
            signal = np.r_[np.zeros(delay, dtype=float), signal[:-delay]]
        # Use the same causal warm-up and fill-time restriction as the backtester.
        warm = max(required_indicator_warmup(tf, C), int((C.get("walk_forward") or {}).get("warmup_bars", 120)))
        signal, forward = signal[warm:], forward[warm:]
        allowed = filter_restricted_bars(df, C, tf)
        if allowed is not None:
            fill_allowed = np.asarray(allowed, dtype=bool)[warm + 1:len(df)-1]
            signal = signal[:len(fill_allowed)] * fill_allowed.astype(float)
            forward = forward[:len(signal)]
        market = str(bt.get("market_type", "spot")).lower()
        fee_side = get_fee(C, symbol, market) / 2.0
        cost_side = fee_side + _roundtrip_spread_slippage(bt,C.get("gate") or {})/2 + float(bt.get("market_impact_roundtrip", 0.0))/2
        result = block_signal_permutation(signal, forward, cost_per_turnover=cost_side,
                    block_length=max(2,min(int(block_bars),len(signal))),
                    n_permutations=max(100,min(int(permutations),5000)))
        result.update({"source":source,"symbol":symbol,"timeframe":tf,"dataset_bars":len(df),
                       "warmup_bars":warm,"execution_delay_bars":int(bt.get("execution_delay_bars",0)),
                       "note":"Diagnostic entry timing control only. Does not replace the complete cost-aware Walk-Forward with stops, targets, and trade-level bootstrap."})
        return result
    except Exception as e:
        raise HTTPException(400, redact(f"{type(e).__name__}: {e}"))


@app.post("/api/research-lab/run-hypothesis")
def api_run_registered_hypothesis(payload: dict):
    """Run one pre-registered declarative rule through the existing cost-aware WF engine."""
    try:
        from research_rule_engine import build_feature_frame, compile_long_flat_rule
        from walk_forward import run_walk_forward
        from research_provenance import build_provenance
        from research_registry import ResearchRegistry
        from research_statistics import moving_block_bootstrap, block_bootstrap_mean_test
        hypothesis_id=str(payload.get("hypothesis_id") or "").strip()
        name=str(payload.get("name") or hypothesis_id).strip()
        rule_spec=payload.get("rule_spec")
        if not hypothesis_id or not name or not isinstance(rule_spec,dict):
            raise ValueError("hypothesis_id, name and rule_spec are required")
        source=str(payload.get("source") or "ramzinex"); symbol=str(payload.get("symbol") or "USDTIRR")
        tf=str(payload.get("tf") or "1h"); count=max(300,min(int(payload.get("n",1200)),5000))
        C=conf(); df=load_cached(source,symbol,tf,count,str(payload.get("exchange") or "coinex"),
                                 str(payload.get("udf_url") or ""),str(payload.get("csv_path") or ""))
        audit=_audit_research_frame(df,source,tf)
        indicators=compute(df,tf); features=build_feature_frame(df,indicators)
        signal=compile_long_flat_rule(features,rule_spec)
        market_type=str((C.get("backtest") or {}).get("market_type","spot")).lower()
        bt=C.get("backtest") or {}; wf=C.get("walk_forward") or {}
        fee_side=get_fee(C,symbol,market_type)/2.0
        atr=_backtest_atr(df,indicators)
        engine_kwargs={"start":1,"threshold":float(C.get("verdict_threshold",.20)),
            "fee_per_side":max(0.0,min(float(fee_side),.499)),
            **_execution_cost_kwargs(bt,C.get("gate") or {}),
            "market_impact_rt":float(bt.get("market_impact_roundtrip",0.0)),
            "execution_delay_bars":int(bt.get("execution_delay_bars",0)),
            "allow_short":bool(bt.get("allow_short_futures",True)) if market_type=="futures" else False,
            "position_size":float(bt.get("position_size",1.0)),"market_type":market_type,
            "leverage":float(bt.get("futures_leverage",1.0)) if market_type=="futures" else 1.0,
            "funding_fee_per_bar":float(bt.get("funding_fee_per_bar",0.0)) if market_type=="futures" else 0.0,
            "atr":atr,"stop_atr_mult":float(bt.get("stop_atr_mult",1.5)),
            "target_atr_mult":float(bt.get("target_atr_mult",2.0))}
        warm=max(int(payload.get("warmup_bars") or wf.get("warmup_bars",120)),required_indicator_warmup(tf,C))
        allowed=filter_restricted_bars(df,C,tf)
        regime=indicators.get("regime")
        mri=indicators.get("market_regime_intelligence")
        components={f"group_{k}":v.to_numpy(dtype=float) for k,v in (indicators.get("gs") or {}).items()}
        components.update({f"indicator_{k}":v.to_numpy(dtype=float) for k,v in (indicators.get("sig") or {}).items()})
        strategy_config={**_research_execution_config(tf,symbol,None,C),"custom_rule_spec":rule_spec,
                         "hypothesis_id":hypothesis_id}
        registry=ResearchRegistry(RESEARCH_REGISTRY_PATH)
        registered_spec={"kind":"declarative_long_flat_rule","rule_spec":rule_spec,
                         "source":source,"symbol":symbol,"timeframe":tf,"strategy_config":strategy_config}
        registry.register(hypothesis_id,name,registered_spec,family_id="chartanalyzer_global_research")
        result=run_walk_forward(df,signal,n_splits=max(2,min(int(payload.get("n_splits") or wf.get("n_splits",5)),20)),
            warmup_bars=warm,gap_bars=max(0,int(payload.get("gap_bars",wf.get("gap_bars",0)))),
            engine_kwargs=engine_kwargs,
            entry_regime=regime.to_numpy(dtype=object) if regime is not None else None,
            entry_market_regime=mri["regime"].to_numpy(dtype=object) if mri is not None else None,
            entry_regime_transition=mri["transition"].to_numpy(dtype=bool) if mri is not None else None,
            entry_allowed=allowed,entry_components=components)
        stress_kwargs=dict(engine_kwargs)
        for cost_key in ("fee_per_side","spread_slippage_rt","spread_rt","slippage_rt","market_impact_rt","funding_fee_per_bar"):
            if cost_key in stress_kwargs: stress_kwargs[cost_key]=float(stress_kwargs[cost_key])*1.5
        stress_result=run_walk_forward(df,signal,n_splits=max(2,min(int(payload.get("n_splits") or wf.get("n_splits",5)),20)),
            warmup_bars=warm,gap_bars=max(0,int(payload.get("gap_bars",wf.get("gap_bars",0)))),
            engine_kwargs=stress_kwargs,entry_regime=regime.to_numpy(dtype=object) if regime is not None else None,
            entry_market_regime=mri["regime"].to_numpy(dtype=object) if mri is not None else None,
            entry_regime_transition=mri["transition"].to_numpy(dtype=bool) if mri is not None else None,
            entry_allowed=allowed,entry_components=components,include_diagnostics=False)
        result["cost_stress_1_5x"]={"net_expectancy_pct":stress_result.get("net_expectancy_pct"),
            "net_profit_factor":stress_result.get("net_profit_factor"),"strategy_pct":stress_result.get("strategy_pct"),
            "trades":stress_result.get("trades"),"positive_expectancy":bool(stress_result.get("net_expectancy_pct") is not None and stress_result["net_expectancy_pct"]>0),
            "method":"exact chronological Walk-Forward rerun with all modeled costs multiplied by 1.5"}
        result["provenance"]=build_provenance(df,source=source,symbol=symbol,timeframe=tf,
            requested_candles=count,market_type=market_type,strategy_config=strategy_config,
            data_integrity=audit,evaluation_scope="custom_hypothesis_sequential_oos")
        result.update({"source":source,"symbol":symbol,"timeframe":tf,"market_type":market_type,
                       "strategy_frozen":True,"parameter_optimization":False,
                       "hypothesis_id":hypothesis_id,"hypothesis_name":name,
                       "rule_spec":rule_spec,"data_integrity":audit})
        returns=[float(t["net_ret_pct"]) for t in (result.get("trades_log") or []) if t.get("net_ret_pct") is not None]
        inference={"bootstrap_ci_95_pct":None,"p_value":None,"method":"unavailable","sample_trades":len(returns)}
        if len(returns)>=2:
            block=min(20,max(2,int(round(len(returns)**.5))))
            ci=moving_block_bootstrap(returns,block_length=block,n_boot=3000)
            test=block_bootstrap_mean_test(returns,block_length=block,n_boot=3000)
            inference={"bootstrap_ci_95_pct":ci["ci"],"bootstrap_mean_pct":ci["mean"],
                       "p_value":test["p_value"],"method":test["method"],"sample_trades":len(returns),"block_length":block}
        result["research_inference"]=inference
        run_id=result["provenance"]["run_id"]
        registry.record(experiment_id=run_id,hypothesis_id=hypothesis_id,
            dataset_id=result["provenance"]["dataset_id"],config_hash=result["provenance"]["config_fingerprint"],
            source=source,symbol=symbol,timeframe=tf,run_id=run_id,
            sample_start=result["provenance"]["first_timestamp"],sample_end=result["provenance"]["last_timestamp"],
            metrics={"trades":result.get("trades"),"net_expectancy_pct":result.get("net_expectancy_pct"),
                     "net_profit_factor":result.get("net_profit_factor"),"strategy_pct":result.get("strategy_pct"),
                     "max_dd_pct":result.get("max_dd_pct"),"bootstrap_ci_95_pct":inference["bootstrap_ci_95_pct"],
                     "cost_stress_1_5x_positive":result["cost_stress_1_5x"]["positive_expectancy"]},
            p_value=inference["p_value"],status="COMPLETED")
        result["multiple_testing"]=registry.apply_fdr("chartanalyzer_global_research")
        summary=registry.summary(); row=next((x for x in summary["recent_experiments"] if x.get("experiment_id")==run_id),None)
        result["adjusted_p_value"]=row.get("adjusted_p_value") if row else None
        result["research_registry"]=summary
        result["deployment_status"]="RESEARCH_ONLY_NOT_CONNECTED_TO_LIVE_GATE"
        return result
    except Exception as e: raise HTTPException(400,redact(f"{type(e).__name__}: {e}"))


@app.get("/api/research-lab/random-entry")
def api_research_random_entry(source: str = "ramzinex", symbol: str = "USDTIRR", tf: str = "1h",
                             n: int = 1200, simulations: int = 500,
                             exchange: str = "coinex", udf_url: str = "", csv_path: str = ""):
    """Matched random-entry control preserving observed holding-run lengths."""
    try:
        from research_statistics import matched_random_entry_control
        C=conf(); count=max(200,min(int(n),5000)); df=load_cached(source,symbol,tf,count,exchange,udf_url,csv_path)
        _audit_research_frame(df,source,tf); indicators=compute(df,tf)
        score=indicators["S"].to_numpy(dtype=float); threshold=float(C.get("verdict_threshold",.20))
        signal=(score[:-2]>=threshold).astype(int)
        opens=df["open"].to_numpy(dtype=float); forward=opens[2:]/opens[1:-1]-1.0
        bt=C.get("backtest") or {}; delay=int(bt.get("execution_delay_bars",0))
        if delay: signal=np.r_[np.zeros(delay,dtype=int),signal[:-delay]]
        warm=max(required_indicator_warmup(tf,C),int((C.get("walk_forward") or {}).get("warmup_bars",120)))
        signal,forward=signal[warm:],forward[warm:]
        allowed=filter_restricted_bars(df,C,tf)
        if allowed is not None:
            fill_allowed=np.asarray(allowed,dtype=bool)[warm+1:len(df)-1]
            signal=signal[:len(fill_allowed)]*fill_allowed.astype(int); forward=forward[:len(signal)]
        market=str(bt.get("market_type","spot")).lower(); fee_side=get_fee(C,symbol,market)/2.0
        cost_side=fee_side+_roundtrip_spread_slippage(bt,C.get("gate") or {})/2+float(bt.get("market_impact_roundtrip",0.0))/2
        out=matched_random_entry_control(signal,forward,cost_per_turnover=cost_side,
             n_simulations=max(100,min(int(simulations),5000)))
        out.update({"source":source,"symbol":symbol,"timeframe":tf,"dataset_bars":len(df),"warmup_bars":warm,
                    "execution_delay_bars":delay,"note":"Control baseline only. Compare with full OOS strategy and Buy & Hold; this is not a deployment test."})
        return out
    except Exception as e: raise HTTPException(400,redact(f"{type(e).__name__}: {e}"))


@app.get("/api/research-lab")
def api_research_lab():
    from research_registry import ResearchRegistry
    from holdout_vault import HoldoutVault
    return {"engine": "ChartAnalyzer Quant Research Engine 7.2.0",
            "registry": ResearchRegistry(RESEARCH_REGISTRY_PATH).summary(),
            "hypotheses": ResearchRegistry(RESEARCH_REGISTRY_PATH).list_hypotheses(100),
            "holdout": HoldoutVault(HOLDOUT_VAULT_PATH).status(),
            "controls": {"walk_forward": True, "bootstrap": True, "fdr": True,
                         "signal_permutation_module": True, "matched_random_entry_control": True,
                         "declarative_hypothesis_runner": True, "chronological_train_validation_holdout": True,
                         "purged_embargo_cv_primitives": True, "parameter_regime_symbol_robustness_primitives": True,
                         "holdout_final_locked": True},
            "limitations": ["The permutation module is an entry-timing diagnostic, not a full stop/target execution test.",
                            "The holdout vault stores an immutable seal record; it does not encrypt or physically isolate raw candle files."]}


@app.get("/api/research-lab/split-preview")
def api_research_split_preview(n: int = 1000, train_ratio: float = 0.60,
                              validation_ratio: float = 0.20, holdout_ratio: float = 0.20):
    from research_protocol import chronological_split
    try:
        return {"observations":int(n),"split":chronological_split(
            int(n),train_ratio=train_ratio,validation_ratio=validation_ratio,
            holdout_ratio=holdout_ratio),
            "note":"Boundary preview only. Do not treat an already inspected historical segment as an untouched final holdout."}
    except Exception as e: raise HTTPException(400,redact(f"{type(e).__name__}: {e}"))


@app.post("/api/research-lab/robustness")
def api_research_robustness(payload: dict):
    from research_robustness import parameter_neighborhood_stability, cross_symbol_stability, regime_stability
    try:
        return {"parameter_stability":parameter_neighborhood_stability(payload.get("parameter_runs") or []),
                "cross_symbol_stability":cross_symbol_stability(payload.get("symbol_runs") or []),
                "regime_stability":regime_stability(payload.get("regime_runs") or []),
                "warning":"Only pre-registered, genuinely OOS experiment rows should be supplied. This endpoint summarizes evidence; it does not create it."}
    except Exception as e: raise HTTPException(400,redact(f"{type(e).__name__}: {e}"))


@app.post("/api/research-lab/hypotheses")
def api_register_hypothesis(payload: dict):
    from research_registry import ResearchRegistry
    try:
        hypothesis_id=str(payload.get("hypothesis_id") or "").strip()
        name=str(payload.get("name") or hypothesis_id).strip()
        rule_spec=payload.get("rule_spec")
        source=str(payload.get("source") or "ramzinex")
        symbol=str(payload.get("symbol") or "USDTIRR")
        tf=str(payload.get("tf") or "1h")
        if not hypothesis_id or not name or not isinstance(rule_spec,dict):
            raise ValueError("hypothesis_id, name and declarative rule_spec are required")
        C=conf()
        specification={"kind":"declarative_long_flat_rule","rule_spec":rule_spec,
                       "source":source,"symbol":symbol,"timeframe":tf,
                       "strategy_config":{**_research_execution_config(tf,symbol,None,C),
                                          "custom_rule_spec":rule_spec,"hypothesis_id":hypothesis_id}}
        return ResearchRegistry(RESEARCH_REGISTRY_PATH).register(
            hypothesis_id,name,specification,family_id="chartanalyzer_global_research")
    except Exception as e:
        raise HTTPException(400, redact(f"{type(e).__name__}: {e}"))

@app.get("/api/research-lab/fingerprint")
def api_research_fingerprint(source: str = "ramzinex", symbol: str = "USDTIRR", tf: str = "1h",
                            n: int = 1200, exchange: str = "coinex", udf_url: str = "", csv_path: str = ""):
    """Preview exact dataset and frozen strategy identities before sealing a holdout."""
    try:
        from research_provenance import dataset_fingerprint, config_fingerprint
        count=max(100,min(int(n),5000)); df=load_cached(source,symbol,tf,count,exchange,udf_url,csv_path)
        audit=_audit_research_frame(df,source,tf); C=conf()
        return {"dataset_id":dataset_fingerprint(df,source=source,symbol=symbol,timeframe=tf),
                "config_hash":config_fingerprint(_research_execution_config(tf,symbol,None,C)),
                "source":source,"symbol":symbol,"timeframe":tf,"bars":len(df),
                "start_timestamp":float(df["time"].iloc[0]),"end_timestamp":float(df["time"].iloc[-1]),
                "data_integrity":audit,"warning":"Fingerprint preview only. Seal only a genuinely untouched future holdout sample."}
    except Exception as e: raise HTTPException(400,redact(f"{type(e).__name__}: {e}"))


@app.post("/api/research-lab/holdout/seal")
def api_seal_holdout(payload: dict):
    from holdout_vault import HoldoutVault
    try:
        source=str(payload.get("source") or ""); symbol=str(payload.get("symbol") or "")
        timeframe=str(payload.get("timeframe") or "")
        start_ts=float(payload.get("start_timestamp")); end_ts=float(payload.get("end_timestamp"))
        from research_registry import ResearchRegistry
        prior=ResearchRegistry(RESEARCH_REGISTRY_PATH).latest_sample_end(
            source=source, symbol=symbol, timeframe=timeframe)
        if prior is not None and start_ts <= float(prior):
            raise ValueError("holdout must begin strictly after the latest registered research sample")
        return HoldoutVault(HOLDOUT_VAULT_PATH).seal(
            dataset_id=str(payload.get("dataset_id") or ""),
            config_hash=str(payload.get("config_hash") or ""),
            source=source, symbol=symbol, timeframe=timeframe,
            start_timestamp=start_ts, end_timestamp=end_ts)
    except Exception as e:
        raise HTTPException(400, redact(f"{type(e).__name__}: {e}"))


@app.post("/api/research-lab/holdout/evaluate")
def api_evaluate_holdout(payload: dict):
    """Consume the sealed holdout exactly once with the frozen config; no tuning interface."""
    try:
        from holdout_vault import HoldoutVault
        from research_provenance import dataset_fingerprint, config_fingerprint
        source=str(payload.get("source") or "ramzinex"); symbol=str(payload.get("symbol") or "USDTIRR")
        tf=str(payload.get("tf") or "1h"); count=max(100,min(int(payload.get("n",1200)),5000))
        df=load(source,symbol,tf,count,str(payload.get("exchange") or "coinex"),
                str(payload.get("udf_url") or ""),str(payload.get("csv_path") or ""))
        audit=_audit_research_frame(df,source,tf); C=conf()
        did=dataset_fingerprint(df,source=source,symbol=symbol,timeframe=tf)
        ch=config_fingerprint(_research_execution_config(tf,symbol,None,C))
        claim=HoldoutVault(HOLDOUT_VAULT_PATH).claim(
            dataset_id=did,config_hash=ch,source=source,symbol=symbol,timeframe=tf,
            start_timestamp=float(df["time"].iloc[0]),end_timestamp=float(df["time"].iloc[-1]))
        result=backtest(df,tf,symbol=symbol)
        result=_attach_research_provenance(result,df,source=source,symbol=symbol,tf=tf,
            requested_n=count,audit=audit,scope="single_use_final_holdout")
        return {"status":"HOLDOUT_CONSUMED_ONCE","claim":claim,"provenance":result.get("provenance"),
                "metrics":{k:result.get(k) for k in ("trades","strategy_pct","net_expectancy_pct","net_profit_factor",
                    "net_win_rate_pct","max_dd_pct","buyhold_net_pct","sharpe_approx")},
                "trades_log":result.get("trades_log"),
                "warning":"This result is final and must not be used to tune the strategy. The holdout is now permanently marked as opened."}
    except Exception as e: raise HTTPException(400,redact(f"{type(e).__name__}: {e}"))


@app.get("/api/margin")
def api_margin(market_type: str = "futures", side: str = "long",
               entry_price: float = 0.0, quantity: float = 0.0,
               leverage: float = 0.0, maintenance_margin_rate: float = -1.0,
               additional_margin: float = 0.0, liquidation_fee_rate: float = -1.0,
               mark_price: float = 0.0):
    """Estimate isolated linear-futures margin and liquidation threshold."""
    try:
        from margin_engine import calculate_isolated_margin
        C = conf()
        mc = C.get("futures_margin") or {}
        bt = C.get("backtest") or {}
        margin_mode = str(mc.get("margin_mode", "isolated")).strip().lower()
        if margin_mode != "isolated":
            raise ValueError("V6.3 supports isolated margin only; cross margin is not modelled")
        # Zero/-1 are API sentinels for config defaults. Other invalid values
        # are passed to the engine and rejected instead of silently overridden.
        lev_raw = float(leverage)
        mmr_raw = float(maintenance_margin_rate)
        lfr_raw = float(liquidation_fee_rate)
        lev = float(bt.get("futures_leverage", 1.0)) if lev_raw == 0 else lev_raw
        mmr = float(mc.get("maintenance_margin_rate", 0.005)) if mmr_raw == -1 else mmr_raw
        lfr = float(mc.get("liquidation_fee_rate", 0.005)) if lfr_raw == -1 else lfr_raw
        mark = None if float(mark_price) == 0 else mark_price
        result = calculate_isolated_margin(
            market_type=market_type, side=side, entry_price=entry_price, quantity=quantity,
            leverage=lev, maintenance_margin_rate=mmr,
            additional_margin=additional_margin, liquidation_fee_rate=lfr,
            mark_price=mark,
            max_leverage=float(mc.get("max_leverage", 125)),
            warning_distance_pct=float(mc.get("warning_distance_pct", 5.0)),
            critical_distance_pct=float(mc.get("critical_distance_pct", 2.0)),
        )
        result["version"] = "7.2.0"
        return result
    except Exception as e:
        raise HTTPException(400, redact(f"{type(e).__name__}: {e}"))


@app.get("/api/forecast")
def api_forecast(source: str = "ramzinex", symbol: str = "USDTIRR", tf: str = "1h", hours: float = 12.0,
                 paths: int = 2000, exchange: str = "coinex", udf_url: str = "", csv_path: str = ""):
    try:
        C = conf()
        df = load_cached(source, symbol, tf, int(C["projection"]["calib_bars"]), exchange, udf_url, csv_path)
        return forecast(df, tf, max(0.25, min(float(hours), 72.0)), max(500, min(int(paths), 10000)),
                        C, symbol=symbol, source=source)
    except Exception as e:
        raise HTTPException(400, redact(f"{type(e).__name__}: {e}"))


@app.get("/api/pmi")
def api_pmi(source: str = "ramzinex", symbol: str = "USDTIRR", tf: str = "1h", n: int = 1000,
            exchange: str = "coinex", udf_url: str = "", csv_path: str = "",
            horizons: str = "1,4,12,24", paths: int = 3000):
    """Predictive Market Intelligence: causal regime + scenario distributions."""
    try:
        C = conf()
        count = max(120, min(int(n), 5000))
        df = load_cached(source, symbol, tf, count, exchange, udf_url, csv_path)
        hs = tuple(int(x.strip()) for x in str(horizons).split(",") if x.strip())
        hs = tuple(x for x in hs if 1 <= x <= 300)[:8]
        if not hs:
            raise ValueError("horizons must contain 1..300 bars")
        return PMI.analyze(df, timeframe=tf, horizons=hs, paths=max(500, min(int(paths), 10000)))
    except Exception as e:
        raise HTTPException(400, redact(f"{type(e).__name__}: {e}"))


@app.get("/api/data-integrity")
def api_data_integrity(source: str = "ramzinex", symbol: str = "USDTIRR", tf: str = "1h", n: int = 800,
                      exchange: str = "coinex", udf_url: str = "", csv_path: str = ""):
    try:
        from data_integrity import audit_ohlcv
        df = load_cached(source, symbol, tf, n, exchange, udf_url, csv_path)
        report = audit_ohlcv(df, TF_MIN[tf] * 60, check_stale=source in ("ramzinex", "udf", "ccxt"))
        report.update({"source": source, "symbol": symbol, "timeframe": tf})
        return report
    except Exception as e:
        raise HTTPException(400, redact(f"{type(e).__name__}: {e}"))


# ---------------------------------------------------------------- LLM layer (local + cloud)
def provider(name):
    c = cfg()
    if name == "local":
        return {"url": c.get("local_url") or os.getenv("LOCAL_URL") or os.getenv("LLM_URL") or "http://127.1.0.1:11434/v1",
                "key": "", "model": c.get("local_model") or os.getenv("LOCAL_MODEL", ""), "headers": {}}
    if name == "openrouter":
        return {"url": "https://openrouter.ai/api/v1",
                "key": c.get("openrouter_key") or os.getenv("OPENROUTER_API_KEY", ""),
                "model": c.get("openrouter_model") or os.getenv("OPENROUTER_MODEL", ""),
                "headers": {"HTTP-Referer": "http://127.1.0.1:8765", "X-Title": "Chart Analyzer"}}
    if name == "xai":
        return {"url": c.get("xai_url") or "https://api.x.ai/v1",
                "key": c.get("xai_key") or os.getenv("XAI_API_KEY", ""),
                "model": c.get("xai_model") or os.getenv("XAI_MODEL", ""), "headers": {}}
    if name == "custom":
        return {"url": c.get("custom_url", ""), "key": c.get("custom_key", ""),
                "model": c.get("custom_model", ""), "headers": {}}
    raise ValueError(f"unknown provider {name}")


def _base(url):
    u = url.rstrip("/")
    return u[:-len("/chat/completions")] if u.endswith("/chat/completions") else u


def _session(name):
    ss = requests.Session()
    if name == "local":
        ss.trust_env = False  # never route localhost through a system/VPN proxy
    return ss


def _headers(p):
    h = {"Content-Type": "application/json", **p["headers"]}
    if p["key"]:
        h["Authorization"] = "Bearer " + p["key"]
    return h


def list_models(name, free_only=False):
    p = provider(name)
    if not p["url"]:
        raise RuntimeError(f"{name}: url not configured")
    r = _session(name).get(_base(p["url"]) + "/models", headers=_headers(p), timeout=30, allow_redirects=False)
    try:
        j = r.json()
    except Exception:
        raise RuntimeError(f"{name}: HTTP {r.status_code}, non-JSON reply: {r.text[:200]!r}")
    if r.status_code >= 400:
        raise RuntimeError(f"{name}: HTTP {r.status_code}: {str(j)[:200]}")
    out = []
    for m in j.get("data", []):
        if free_only and name == "openrouter":
            pr = m.get("pricing") or {}
            if not (str(pr.get("prompt")) == "0" and str(pr.get("completion")) == "0"):
                continue
        out.append(m.get("id"))
    return sorted(x for x in out if x)


def chat(name, messages, model="", max_tokens=1600, temperature=0.3):
    p = provider(name)
    if not p["url"]:
        raise RuntimeError(f"{name}: url not configured in config.json")
    if name != "local" and not p["key"]:
        raise RuntimeError(f"{name}: API key missing (config.json or environment variable)")
    model = model or p["model"]
    if not model and name == "local":
        ms = list_models("local")
        model = ms[0] if ms else ""
    if not model:
        raise RuntimeError(f"{name}: no model selected (pick one in the page or set it in config.json)")
    r = _session(name).post(_base(p["url"]) + "/chat/completions", headers=_headers(p), timeout=300,
                            allow_redirects=False,
                            json={"model": model, "messages": messages,
                                  "temperature": temperature, "max_tokens": min(int(max_tokens), 3000)})
    try:
        j = r.json()
    except Exception:
        raise RuntimeError(f"{name}: HTTP {r.status_code}, non-JSON reply: {r.text[:300]!r}")
    if r.status_code >= 400 or "error" in j:
        raise RuntimeError(f"{name}: HTTP {r.status_code}: {str(j.get('error', j))[:300]}")
    try:
        text = j["choices"][0]["message"]["content"] or ""
    except Exception:
        raise RuntimeError(f"{name}: unexpected reply shape: {str(j)[:300]}")
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    return text, model


SYSTEM = ("You are a cautious quantitative crypto analyst assisting a MANUAL spot trader. "
          "Use ONLY the numbers in the DATA block; never invent prices, indicator values or news. "
          "The 'forecast' block is a statistical volatility cone, NOT a prediction: report its edge statistics "
          "(t_eff, hit rate) and calibration honestly; if drift_used is false say there is no demonstrated directional edge. "
          "If the evidence is weak or conflicting, say so plainly. "
          "Output sections: 1) Bias 2) Evidence for / against (by indicator group) 3) Trade plan (entry, stop, targets, invalidation) "
          "4) Key risks 5) Confidence (low/medium/high) and why. No hype, no promises of profit. "
          "The 'trend' block (market structure, trend lines, zones) is a DESCRIPTION of closed candles; quote trend.edge honestly - "
          "if trend.edge.significant is false, say the trend read has no demonstrated predictive edge, and use trend.invalidation "
          "as the level that cancels the idea. "
          "HARD RULE: if forecast.gate.state is NO_TRADE, the first line must be 'NO TRADE' followed by the gate reasons, "
          "the trade-plan section must say it is not actionable, and confidence must not exceed gate.confidence_cap. "
          "Never override the gate and never invent numbers.")
REVIEWER = ("You are a strict reviewer. A weaker local model wrote a DRAFT analysis from the DATA block. "
            "Check every claim in the draft against DATA. Output: A) errors or unsupported claims (quote them) "
            "B) important things the draft missed C) a corrected FINAL analysis in the same 5-section format. "
            "If DATA is insufficient for a claim, say so instead of guessing. Do not present the cone as a forecast.")


def build_context(p):
    keys = ("symbol", "tf", "last", "verdict", "score", "confidence", "conf_label", "agree_n", "active_n",
            "regime", "adx", "bw_pct", "atr", "atr_pct", "squeeze", "groups", "plan", "levels", "mtf",
            "backtest", "forecast", "trend", "trend_mtf", "news")
    d = {k: p.get(k) for k in keys if p.get(k) is not None}
    d["indicators"] = [{"name": i.get("name"), "group": i.get("group"), "value": i.get("value"),
                        "signal": i.get("signal")} for i in (p.get("indicators") or []) if isinstance(i, dict)]
    s = json.dumps(d, ensure_ascii=False)
    if len(s) > 14000:  # drop the bulkiest part rather than cutting JSON in half
        d["indicators"] = d["indicators"][:20]
        s = json.dumps(d, ensure_ascii=False)[:14000]
    return s


@app.get("/api/providers")
def api_providers():
    return {"local": True, "openrouter": bool(provider("openrouter")["key"]),
            "xai": bool(provider("xai")["key"]), "custom": bool(provider("custom")["url"])}


@app.get("/api/models")
def api_models(provider: str = "local", free: int = 0):
    try:
        return {"models": list_models(provider, bool(free))}
    except Exception as e:
        raise HTTPException(502, redact(e))


@app.post("/api/explain")
def api_explain(payload: dict):
    if not explain_rate_ok():
        raise HTTPException(429, "explain rate limit reached (explain_per_hour in config.json)")
    mode, cloud = payload.get("mode", "local"), payload.get("cloud", "openrouter")
    if cloud not in ("openrouter", "xai", "custom"):
        raise HTTPException(400, "unknown cloud provider")
    models = payload.get("models") or {}
    lang = ("Answer in Persian (Farsi); keep numbers, tickers and indicator names in Latin script."
            if payload.get("lang", "fa") == "fa" else "Answer in English.")
    q = str(payload.get("question") or "Give the analysis.")[:1000]
    data = build_context(payload)
    msgs = [{"role": "system", "content": SYSTEM + " " + lang},
            {"role": "user", "content": f"DATA:\n{data}\n\nUSER REQUEST: {q}"}]
    parts = []
    try:
        if mode == "local":
            t, m = chat("local", msgs, models.get("local", ""))
            parts.append({"title": f"Local - {m}", "text": t})
        elif mode == "cloud":
            t, m = chat(cloud, msgs, models.get("cloud", ""))
            parts.append({"title": f"{cloud} - {m}", "text": t})
        elif mode == "review":
            t, m = chat("local", msgs, models.get("local", ""))
            parts.append({"title": f"Local draft - {m}", "text": t})
            try:
                rv = [{"role": "system", "content": REVIEWER + " " + lang},
                      {"role": "user", "content": f"DATA:\n{data}\n\nDRAFT:\n{t}\n\nUSER REQUEST: {q}"}]
                t2, m2 = chat(cloud, rv, models.get("cloud", ""))
                parts.append({"title": f"Cloud review - {cloud} / {m2}", "text": t2})
            except Exception as e:
                parts.append({"title": "Cloud review failed (local draft above is unreviewed)", "text": redact(e)})
        else:
            raise ValueError("unknown mode")
    except Exception as e:
        raise HTTPException(502, redact(f"{type(e).__name__}: {e}"))
    return {"parts": parts}


# ---------------------------------------------------------------- page
PAGE = r"""<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><title>Chart Analyzer</title>
<style>
body{font:14px system-ui;background:#10141a;color:#d8dee9;margin:0;padding:16px}
input,select,button,textarea{background:#1b222c;color:#d8dee9;border:1px solid #2c3745;padding:6px;border-radius:4px}
button{cursor:pointer;background:#2b6cb0}
table{border-collapse:collapse;width:100%;margin-top:6px}td,th{padding:4px 8px;border-bottom:1px solid #222c38;text-align:left}
.on{background:#d69e2e!important}.box table{display:block;overflow-x:auto}
@media(max-width:700px){body{padding:8px}input,select,button,textarea{font-size:16px}#cv{height:320px!important}}
#tb{margin-top:10px}#info{margin-left:12px;color:#94a3b8}.box{margin:8px 0;padding:8px;background:#141a22;border-radius:4px}
.b{color:#4ade80}.s{color:#f87171}.n{color:#94a3b8}.w{color:#facc15}
#v{font-size:18px;margin:10px 0}pre{white-space:pre-wrap;background:#1b222c;padding:10px;border-radius:4px}
.bar{display:inline-block;position:relative;width:110px;height:8px;background:#222c38;vertical-align:middle;margin:0 8px}
.bar i{position:absolute;top:0;bottom:0}.bar i.b{background:#4ade80}.bar i.s{background:#f87171}.bar i.n{background:#94a3b8}
details summary{cursor:pointer;margin-top:4px}
.scenario-table{width:100%;max-width:780px;margin:8px 0;border-collapse:collapse}
.scenario-table th,.scenario-table td{padding:6px 8px;border-bottom:1px solid #2a3441;text-align:left}
.scenario-table tr.top{background:#1b2832}
.scenario-rank{display:inline-block;min-width:22px;text-align:center;border-radius:12px;background:#263445;padding:2px 5px}
.scenario-note{font-size:12px;color:#aab6c5;line-height:1.45}
</style>
<div>
<select id=source><option>ramzinex</option><option>ccxt</option><option>udf</option><option>csv</option></select>
<input id=symbol value=USDTIRR size=12 placeholder="symbol">
<select id=tf><option>15m</option><option selected>1h</option><option>4h</option><option>12h</option><option>1d</option></select>
<input id=exchange value=coinex size=8 title="ccxt exchange">
<input id=udf_url placeholder="udf base url / csv file name" size=30>
<input id=hours type=number value=12 min=1 max=72 style="width:60px" title="forecast horizon, hours"> h
<button onclick=run()>Analyze</button></div>
<div class=box><label>Backtest + Walk-Forward candle window <input id=bt_bars type=number value=1200 min=300 max=5000 step=100 style="width:90px"></label>
<span class=scenario-note>Both research panels use this exact requested OHLCV window and share a reproducible run ID. Full-window results remain descriptive; Walk-Forward is the OOS test.</span></div>
<div class=box id=iranbox><h3>Iran Exchange Intelligence · Read-only MCP</h3>
<div><label>Coin <input id=ic_coin value=BTC size=8></label>
<label>Quote <select id=ic_quote><option value=IRT selected>IRT (Toman)</option><option value=USDT>USDT</option></select></label>
<label>Exchange <select id=ic_exchange><option value=wallex>Wallex</option><option value=ramzinex>Ramzinex</option><option value=nobitex>Nobitex</option><option value=bitpin>Bitpin</option><option value=tabdeal>Tabdeal</option><option value=ompfinex>OMPFinex</option><option value=exir>Exir</option><option value=bit24>Bit24</option><option value=balinex>Balinex</option><option value=abantether>Aban Tether</option><option value=kifpool>Kifpool</option><option value=okex>OK-EX</option><option value=tetherland>Tetherland</option></select></label>
<label>TF <select id=ic_tf><option>15m</option><option selected>1h</option><option>4h</option><option>1d</option></select></label>
<button onclick="iranStatus()">Connection</button><button onclick="iranCompare()">Compare exchanges</button><button onclick="iranOrderbook()">Order book</button><button onclick="iranCandles()">Candles</button></div>
<div id=iranout class=scenario-note>Disabled by default. Enable iran_crypto_mcp in config.json, then install uv/uvx. This source is separate from the backtest feed.</div></div>
<div id=tb><button id=bt onclick="setMode('trend')">Trend line</button> <button id=bh onclick="setMode('hline')">Horizontal line</button> <button onclick="undo()">Undo</button> <button onclick="clearDr()">Clear</button> <button onclick="resetView()">Reset view</button> <button onclick="toggleFC()">Cone on/off</button> <button onclick="toggleTR()">Trend overlay</button><span id=info></span></div>
<canvas id=cv style="width:100%;height:380px;display:block;margin-top:6px;background:#0b0f14;cursor:grab;touch-action:none"></canvas>
<div id=gate></div><div id=v></div><div id=trend class=box></div><div id=tmtf class=box></div><div id=ctx class=box></div><div id=fc class=box></div><div id=mtf class=box></div><div id=grp></div>
<button onclick=backtest()>Backtest</button> <button onclick=walkForward()>Walk-Forward OOS</button> <button onclick=dataIntegrity()>Data integrity</button> <button onclick=journalRep()>Journal report</button>
<div id=jr class=box></div><div class=box id=researchlab><h3>Quant Research Engine · QRE</h3>
<div id=qrestatus class=scenario-note>Registry and holdout status not loaded.</div>
<button onclick="loadResearchLab()">Refresh Research Lab</button> <button onclick="runPermutation()">Run entry permutation control</button> <button onclick="runRandomEntry()">Run matched random-entry</button> <span id=qreperm class=scenario-note></span>
<div class=scenario-note>Every Walk-Forward run is registered. Failed experiments remain in the registry. FDR is applied to completed registered tests. Holdout sealing is one-way.</div></div>
<div id=btout class=box></div><div id=wfo class=box><b>Walk-Forward:</b> not run</div><div id=di class=box></div>
<div class=box id=marginbox><h3>Futures Isolated-Margin Calculator</h3>
<label>Side <select id=m_side><option value=long>Long</option><option value=short>Short</option></select></label>
<label>Entry price <input id=m_entry type=number min=0 step=any placeholder="e.g. 68000"></label>
<label>Quantity (base units) <input id=m_qty type=number min=0 step=any placeholder="e.g. 0.01"></label>
<label>Leverage <input id=m_lev type=number min=1 max=125 step=0.1 value=5></label>
<label>Maintenance margin % <input id=m_mmr type=number min=0 max=49 step=0.01 value=0.5></label>
<label>Additional margin <input id=m_extra type=number min=0 step=any value=0></label>
<label>Liquidation fee % <input id=m_liqfee type=number min=0 max=49 step=0.01 value=0.5></label>
<label>Mark price (optional) <input id=m_mark type=number min=0 step=any placeholder="for live risk distance"></label>
<button onclick=marginCalc()>Calculate margin / liquidation</button><div id=margout class=box>Calculator not run.</div>
<div class=scenario-note>Linear isolated estimate only. It does not replicate an exchange's tiered maintenance margin, cross margin, mark-price rules or liquidation engine.</div></div>
<h3>LLM analysis</h3>
<div>
mode <select id=mode><option value=local>local only</option><option value=cloud>cloud only</option><option value=review selected>local draft + cloud review</option></select>
cloud <select id=cloud><option>openrouter</option><option>xai</option><option>custom</option></select>
lang <select id=lang><option value=fa>Persian</option><option value=en>English</option></select>
<label><input type=checkbox id=free checked> free only</label>
<span id=prov class=n></span></div>
<div style="margin-top:6px">
local model <input id=lm list=lml placeholder="auto" size=22><datalist id=lml></datalist>
<button onclick="loadModels('local')">load</button>
&nbsp; cloud model <input id=cm list=cml placeholder="config default" size=40><datalist id=cml></datalist>
<button onclick="loadModels('cloud')">load</button></div>
<textarea id=q rows=2 style="width:100%;margin-top:6px" placeholder="e.g. short-term plan for a manual trade"></textarea>
<button onclick=explain()>Send</button><div id=out></div>
<script>
const $=id=>document.getElementById(id);
function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
let last=null,FC=null,SF=true,TRD=null,ST=true,TIM=new Map(),LV=[],D=[],V={start:0,count:100},DR=[],mode=null,pend=null,M=null,drag=null;
const CV=$('cv'),X2=CV.getContext('2d'),AX=64;
function toggleTR(){ST=!ST;render();}
function dirc(d){return d=='Up'?'b':d=='Down'?'s':'n'}
async function fetchTrend(){
 const el=$('trend');el.textContent='trend structure...';TRD=null;
 try{const {ok,j}=await api('/api/trend',qs());if(!ok){el.textContent='trend: '+j.detail;render();return}
  TRD=j.overlay||null;if(last){const t={...j};delete t.overlay;last.trend=t;}showTrend(j);render();
 }catch(e){el.textContent='trend failed: '+e;}}
function showTrend(j){
 const s=j.structure,q=j.quality,e=j.edge||{},c=j.components,z=j.momentum_z;
 let h='<b>Trend ('+esc(j.tf)+', closed candles)</b> &nbsp; <span class='+dirc(j.direction)+' style="font-size:16px">'+esc(j.label)+'</span> &nbsp; score '+j.score+bar(j.score)+' confidence <b>'+esc(j.conf_label)+'</b> &nbsp; trendiness '+j.trendiness+'<br>';
 h+='Structure: <b>'+esc(s.state)+'</b>, bias by last break: <b>'+esc(s.bias_by_last_break)+'</b>';
 if(s.last_event)h+=' ('+esc(s.last_event.type)+(s.last_event.dir>0?' up':' down')+' @ '+fmt(s.last_event.lvl)+', '+s.bars_since_last_break+' bars ago)';
 h+='<br>Swing high '+(s.swing_high==null?'n/a':fmt(s.swing_high))+' / swing low '+(s.swing_low==null?'n/a':fmt(s.swing_low))+'<br>';
 h+='Components: '+Object.entries(c).map(([k,v])=>esc(k)+' <span class='+sc(v)+'>'+v+'</span>').join(' | ')+'<br>';
 h+='Momentum z (vs random walk): '+Object.entries(z).map(([k,v])=>esc(k)+' '+v).join(' | ')+' &nbsp; ER '+q.efficiency_ratio+' &nbsp; ADX '+q.adx+' &nbsp; VR '+q.variance_ratio+' (z '+q.variance_ratio_z+': '+esc(q.reading)+')<br>';
 const L=j.overlay&&j.overlay.lines;
 if(L)h+='Trend lines: support '+fmt(L.support_now)+' ('+L.support.slope_pct_per_bar+'%/bar, '+L.support.touches+' touches) | resistance '+fmt(L.res_now)+' ('+L.resistance.slope_pct_per_bar+'%/bar, '+L.resistance.touches+' touches) | channel position '+L.position+(L.break?' <span class=w>'+(L.break>0?'BROKE ABOVE':'BROKE BELOW')+' the line</span>':'')+'<br>';
 const F=j.overlay&&j.overlay.fib;
 if(F)h+='Last leg '+esc(F.leg)+': retracements '+F.retrace.map(x=>x.r+' &rarr; '+fmt(x.p)).join(' | ')+'<br>';
 const Z=(j.overlay&&j.overlay.fvg)||[];
 if(Z.length)h+='Open fair-value gaps: '+Z.map(z=>(z.dir>0?'bull ':'bear ')+fmt(z.lo)+'-'+fmt(z.hi)).join(' | ')+'<br>';
 if(j.invalidation)h+='<b>Invalidation ('+esc(j.invalidation.side)+'):</b> '+fmt(j.invalidation.level)+' - '+esc(j.invalidation.rule)+'<br>';
 if(e.available)h+='<span class='+(e.significant?'b':'w')+'>Edge check:</span> corr '+e.ic+', t_eff '+e.t_eff+', hit '+(e.hit_pct==null?'n/a':e.hit_pct+'%')+' over '+e.horizon_bars+' bars | naive momentum corr '+e.naive_momentum_ic+' (t '+e.naive_momentum_t+'). '+esc(e.note);
 else h+='<span class=w>Edge check unavailable:</span> '+esc(e.reason||'');
 $('trend').innerHTML=h;}
async function fetchTrendMtf(){
 const el=$('tmtf');el.textContent='trend multi-timeframe...';
 try{const {ok,j}=await api('/api/trend_mtf',qs());if(!ok){el.textContent=j.detail;return}
  if(last)last.trend_mtf=j;
  el.innerHTML='<b>Trend 4h / 12h / 1d:</b> '+esc(j.relation)+(j.combined!=null?' &nbsp; weighted <b class='+dirc(j.combined_direction)+'>'+j.combined+' ('+esc(j.combined_direction)+')</b>':'')+'<br>'+
   j.rows.map(x=>x.error?esc(x.tf)+': '+esc(x.error):esc(x.tf)+': <span class='+dirc(x.direction)+'>'+esc(x.label)+'</span> ('+x.score+', '+esc(x.structure.state)+(x.invalidation?', invalid '+(x.invalidation.side=='long'?'< ':'> ')+fmt(x.invalidation.level):'')+')').join(' &nbsp; | &nbsp; ');
 }catch(e){el.textContent='trend mtf failed: '+e;}}
function drawTrend(g,x){
 const T=TRD;if(!T||!D.length)return;const ix=t=>{const v=TIM.get(t);return v==null?-1:v};
 const R=D.length-1+((FC&&SF)?FC.h:3);
 x.lineWidth=1.2;x.setLineDash([]);x.strokeStyle='rgba(167,139,250,0.85)';x.beginPath();let st=false;
 (T.pivots||[]).forEach(p=>{const i=ix(p.t);if(i<0)return;const X=g.X(i),Y=g.Y(p.p);if(!st){x.moveTo(X,Y);st=true}else x.lineTo(X,Y)});x.stroke();
 (T.pivots||[]).forEach(p=>{const i=ix(p.t);if(i<0)return;x.fillStyle=p.kind>0?'#f87171':'#4ade80';x.globalAlpha=p.tentative?0.4:1;
  x.beginPath();x.arc(g.X(i),g.Y(p.p),3,0,6.3);x.fill();x.globalAlpha=1;});
 const L=T.lines;
 if(L)[[L.support,'#4ade80'],[L.resistance,'#f87171']].forEach(([ln,col])=>{const i2=ix(ln.t2);if(i2<0)return;
  const sl=Math.log(1+ln.slope_pct_per_bar/100),i0=Math.max(0,i2-60),P=i=>ln.p2*Math.exp(sl*(i-i2));
  x.strokeStyle=col;x.lineWidth=1.6;x.setLineDash([7,4]);x.beginPath();x.moveTo(g.X(i0),g.Y(P(i0)));x.lineTo(g.X(R),g.Y(P(R)));x.stroke();x.setLineDash([]);});
 const F=T.fib;
 if(F){const i0=Math.max(0,ix(F.to_t));x.font='10px system-ui';F.retrace.forEach(f=>{x.strokeStyle='rgba(250,204,21,0.45)';x.lineWidth=1;x.setLineDash([2,3]);
  x.beginPath();x.moveTo(g.X(i0),g.Y(f.p));x.lineTo(g.pw,g.Y(f.p));x.stroke();x.setLineDash([]);x.fillStyle='rgba(250,204,21,0.8)';x.fillText(f.r,g.X(i0)+2,g.Y(f.p)-6);});}
 (T.fvg||[]).forEach(z=>{const i=Math.max(0,ix(z.t));x.fillStyle=z.dir>0?'rgba(74,222,128,0.10)':'rgba(248,113,113,0.10)';
  const y1=g.Y(z.hi),y2=g.Y(z.lo);x.fillRect(g.X(i),y1,g.pw-g.X(i),Math.max(1,y2-y1));});
 x.font='10px system-ui';(T.events||[]).forEach(e=>{const i=ix(e.t);if(i<0)return;x.fillStyle=e.dir>0?'#4ade80':'#f87171';
  x.fillText(e.type+(e.dir>0?'\u2191':'\u2193'),g.X(i)-10,g.Y(e.lvl)+(e.dir>0?12:-8));});}
function fmt(p){return p>=1000?Math.round(p).toLocaleString():p>=1?p.toFixed(3):p.toPrecision(4)}
function sc(c){return c>0.05?'b':c<-0.05?'s':'n'}
function bar(s){const w=Math.abs(s)*50;return '<span class=bar><i class='+sc(s)+' style="left:'+(s>=0?50:50-w)+'%;width:'+w+'%"></i></span>';}
function qs(){const g=id=>$(id).value,src=g('source');
 return new URLSearchParams({source:src,symbol:g('symbol'),tf:g('tf'),exchange:g('exchange'),
  udf_url:src=='udf'?g('udf_url'):'',csv_path:src=='csv'?g('udf_url'):''});}
function researchQs(){const p=qs();const raw=Number($('bt_bars')?.value||1200);p.set('n',String(Math.max(400,Math.min(5000,Math.trunc(raw)||1200))));return p;}
function provenanceHtml(j){
 const p=j?.provenance;if(!p)return '<div class=scenario-note>Run provenance unavailable.</div>';
 return '<div class=box><b>Reproducibility:</b> run <code>'+esc(p.run_id)+'</code> | dataset <code>'+esc(p.dataset_id)+'</code><br>'+
  esc(p.source)+' / '+esc(p.symbol)+' / '+esc(p.timeframe)+' | '+p.actual_candles+' actual / '+p.requested_candles+' requested candles | '+
  esc(p.first_time_utc||'n/a')+' → '+esc(p.last_time_utc||'n/a')+' | OHLCV '+esc(p.data_integrity?.state||'UNKNOWN')+
  (p.data_integrity?.warnings?.length?'<br><span class=w>'+esc(p.data_integrity.warnings.join('; '))+'</span>':'')+'</div>';
}
function researchComparisonHtml(){
 const b=last?.backtest?.provenance,w=last?.walk_forward?.provenance;if(!b||!w)return '';
 if(b.run_id===w.run_id)return '<div class=box><b class=b>Sample match PASS:</b> Backtest and Walk-Forward use the same candles and strategy/execution configuration. Full-window statistics are still descriptive, not OOS.</div>';
 return '<div class=box><b class=s>Sample mismatch:</b> Backtest and Walk-Forward run IDs differ. Do not compare their headline metrics as if they came from the same experiment. Rerun both without changing data or configuration.</div>';
}
async function api(path,p){const controller=new AbortController();const timer=setTimeout(()=>controller.abort(),60000);try{const r=await fetch(path+(p?'?'+p:''),{signal:controller.signal});let j={};try{j=await r.json()}catch(e){j={detail:'HTTP '+r.status+' (invalid JSON response)'}}return {ok:r.ok,j}}catch(e){return {ok:false,j:{detail:e.name==='AbortError'?'درخواست پس از ۶۰ ثانیه متوقف شد. سرور یا منبع داده پاسخ نداده است.':('Network error: '+(e.message||e))}}}finally{clearTimeout(timer)}}
function NT(){return D.length+((FC&&SF)?FC.h:0)}
async function run(){
 $('v').textContent='analyzing...';
 const {ok,j}=await api('/api/analyze',qs());
 if(!ok){$('v').textContent=j.detail;return}
 last={...j,symbol:$('symbol').value,tf:$('tf').value};FC=null;LV=j.levels||[];setData(j.candles);showAll(j);fetchMtf();fetchTrend();fetchTrendMtf();fetchForecast();
}
function showAll(j){
 $('v').innerHTML='<span class='+sc(j.score)+'>'+esc(j.verdict)+'</span> &nbsp; score '+j.score+' &nbsp; confidence <b>'+esc(j.conf_label)+'</b> ('+j.agree_n+'/'+j.active_n+' groups agree) &nbsp; last '+fmt(j.last);
 showCtx(j);showGroups(j);}
function showCtx(j){
 const p=j.plan||{};
 let h='<div>Regime: <b>'+esc(j.regime)+'</b> (ADX '+j.adx+') &nbsp; ATR '+j.atr_pct+'% &nbsp; BB-width pct '+j.bw_pct+(j.squeeze?' <span class=w>squeeze: low volatility, breakout risk</span>':'')+'</div>';
 const mr=j.market_regime_intelligence||{};
 if(mr.regime)h+='<div class=n>Market Regime Intelligence: <b>'+esc(mr.regime)+'</b>'+(mr.transition?' &nbsp; <span class=w>transition '+esc(mr.transition_from||'?')+' → '+esc(mr.transition_to||'?')+'</span>':'')+' &nbsp; | ER '+(mr.efficiency_ratio??'n/a')+' | ATR rank '+(mr.atr_pct_rank??'n/a')+' | BB rank '+(mr.bb_width_rank??'n/a')+' <span class=w>(research only)</span></div>';
 h+='<div style="margin:6px 0"><b>Plan:</b> '+esc(p.action||'');
 if(p.entry){h+=' | entry '+fmt(p.entry[0])+' - '+fmt(p.entry[1])+' | stop '+fmt(p.stop)+' | T1 '+fmt(p.t1)+' | T2 '+fmt(p.t2)+' | R:R '+p.rr;}
 h+=(p.note?'<br><span class=n>'+esc(p.note)+'</span>':'')+'</div>';
 h+='<div class=n>Levels (blue support / orange resistance): '+(j.levels||[]).map(l=>esc(l.kind)+' '+fmt(l.p)+' x'+l.n).join(' &nbsp; ')+'</div>';
 $('ctx').innerHTML=h;}
function showGroups(j){
 const by={};j.indicators.forEach(i=>(by[i.group]=by[i.group]||[]).push(i));
 const di=j.data_integrity||{},ii=j.indicator_integrity||{};
 const blocked=di.valid===false||ii.valid===false;
 const warned=(di.warnings||[]).length>0||(ii.warnings||[]).length>0;
 const state=blocked?'BLOCKED':warned?'WARN':'PASS';
 const details=[...(di.errors||[]),...(ii.errors||[]),...(di.warnings||[]),...(ii.warnings||[])];
 const integrity='<div class=box><b>Data & Indicator Integrity: <span class='+ (blocked?'s':warned?'w':'b') +'>'+state+'</span></b>'+
  '<div class=n>OHLCV: '+(di.state||'not checked')+' | Indicators: '+(ii.state||'not checked')+' | Checked: '+(ii.checked_indicators??0)+'/'+(ii.enabled_indicators??0)+'</div>'+
  (details.length?'<ul>'+details.map(x=>'<li>'+esc(x)+'</li>').join('')+'</ul>':'<div class=n>No integrity warnings.</div>')+'</div>';
 $('grp').innerHTML=integrity+Object.entries(j.groups).map(([g,o])=>'<div class=box><b>'+esc(g)+'</b>'+bar(o.score)+'<span class='+sc(o.score)+'>'+esc(o.verdict)+' '+o.score+'</span>'+
  '<details><summary class=n>'+o.n+' indicators</summary><table><tr><th>Indicator<th>Value<th>Meaning / unit<th>Signal<th>Note</tr>'+
  (by[g]||[]).map(i=>'<tr><td>'+esc(i.name)+'<td>'+esc(i.value)+'<td>'+esc(i.meaning)+' ('+esc(i.unit)+')<td class='+sc(i.signal)+'>'+esc(i.signal)+'<td>'+esc(i.note)+(i.error?' <span class=s>'+esc(i.error)+'</span>':'')).join('')+
  '</table></details></div>').join('');}
async function fetchMtf(){
 const el=$('mtf');el.textContent='multi-timeframe...';
 try{const {ok,j}=await api('/api/mtf',qs());if(!ok){el.textContent=j.detail;return}
  if(last)last.mtf=j;
  el.innerHTML='<b>Indicator MTF (composite of indicator groups, not structural trend):</b> '+esc(j.alignment)+(j.combined!=null?' &nbsp; weighted score <b class='+sc(j.combined)+'>'+j.combined+' ('+esc(j.combined_verdict)+')</b>':'')+'<br>'+
   j.rows.map(x=>x.error?esc(x.tf)+': '+esc(x.error):esc(x.tf)+': indicator bias <span class='+sc(x.score)+'>'+esc(x.verdict)+'</span> ('+x.score+', '+esc(x.regime)+')').join(' &nbsp; | &nbsp; ');
 }catch(e){el.textContent='mtf failed: '+e;}}
async function fetchForecast(){
 const el=$('fc');el.textContent='forecast cone...';
 const p=qs();p.set('hours',$('hours').value||12);
 try{const {ok,j}=await api('/api/forecast',p);
  if(!ok){el.textContent='forecast: '+j.detail;FC=null;render();return}
  FC=j.cone;if(last)last.forecast=j.summary;showFC(j.summary);resetView();
 }catch(e){el.textContent='forecast failed: '+e;}}
function showFC(s){
 const e=s.edge.aggregate,c=s.calibration;
 let h='<b>Forecast cone</b> - next '+s.horizon_bars+' bars (~'+s.horizon_hours+' h), '+s.paths+' simulated paths<br>';
 h+='P(close above now) = <b>'+s.p_up_pct+'%</b> &nbsp; 80% range at horizon: <b>'+fmt(s.q10)+' ... '+fmt(s.q90)+'</b> ('+s.q10_pct+'% ... '+s.q90_pct+'%), median '+s.q50_pct+'%<br>';
 const sr=s.scenario_probabilities;
 if(sr&&Array.isArray(sr.ranking)){
  const names={Bullish:'Bullish',Sideways:'Sideways',Bearish:'Bearish'};
  h+='<div style="margin-top:10px"><b>Scenario Intelligence — ranked by simulated frequency</b> <span class=n>(most frequent first)</span></div>';
  h+='<table class="scenario-table"><thead><tr><th>Rank</th><th>Scenario</th><th>Simulation share</th><th>Conditional terminal range (P10–P90)</th><th>Paths</th></tr></thead><tbody>';
  for(const z of sr.ranking){
   const band=z.conditional_terminal_price;
   const range=band?fmt(band.p10)+' – '+fmt(band.p90):'n/a';
   h+='<tr class="'+(z.is_most_likely?'top':'')+'"><td><span class="scenario-rank">'+z.rank+'</span></td>'+
    '<td><b>'+names[z.scenario]+'</b>'+(z.is_most_likely?' <span class=b>← most frequent</span>':'')+'</td>'+
    '<td><b>'+z.probability_pct+'%</b> <span class=n>±'+z.simulation_se_pct+' pp MC error</span></td>'+
    '<td>'+range+'</td><td>'+z.count+'</td></tr>';
  }
  h+='</tbody></table><div class="scenario-note">Neutral band: ±'+sr.neutral_band_pct+'% terminal move (round-trip fee floor). These are Monte Carlo path frequencies, NOT calibrated real-world probabilities. MC error reflects finite simulation count only; it does not measure model error. Dashed chart paths are illustrative sample trajectories near the 15th/50th/85th terminal-return quantiles, not one path per scenario. '+esc(sr.note)+'</div>';
 }
 h+='<span class='+(s.drift_used?'b':'w')+'>Edge check:</span> corr '+e.ic+', t_eff '+e.t_eff+', hit '+(e.hit_pct==null?'n/a':e.hit_pct+'%')+' (n='+e.n+'). '+esc(s.drift_note)+'<br>';
 if(c)h+='Cone calibration (walk-forward, n='+c.n+'): the 80% band held <b>'+c.cover80_pct+'%</b>, the 50% band held <b>'+c.cover50_pct+'%</b>'+(s.drift_used?' <span class=n>('+esc(c.note)+'; with drift applied the real coverage differs)</span>':(c.cover80_pct<72?' <span class=s>- cone too narrow, trust it less</span>':''))+'<br>';
 if(s.plan_hit)h+='Plan odds (closes only): T1 first '+s.plan_hit.t1_first_pct+'% / stop first '+s.plan_hit.stop_first_pct+'% / neither '+s.plan_hit.neither_pct+'%<br>';
 h+='<span class=n>'+esc(s.warning)+'</span>';
 $('fc').innerHTML=h;showGate(s.gate);}
function showGate(g){
 const el=$('gate');if(!g){el.innerHTML='';return;}
 const no=g.state!='ALLOWED',e=g.ev||{};
 $('v').style.opacity=no?0.5:1;
 let h='<div class=box style="border-left:4px solid '+(no?'#f87171':'#4ade80')+'"><b class='+(no?'s':'b')+' style="font-size:18px">'+(no?'NO TRADE':'TRADE ALLOWED')+'</b> &nbsp; confidence cap: '+esc(g.confidence_cap);
 if(g.reasons&&g.reasons.length)h+='<ul style="margin:6px 0">'+g.reasons.map(r=>'<li>'+esc(r)+'</li>').join('')+'</ul>';
 if(e.ev!=null)h+='<div>EV after costs: <b class='+(e.ev>0?'b':'s')+'>'+(e.ev*100).toFixed(2)+'%</b> | gross R:R '+(e.rr_gross==null?'n/a':e.rr_gross.toFixed(2))+' | net R:R '+(e.rr_net==null?'n/a':e.rr_net.toFixed(2))+' | breakeven win rate '+(e.breakeven_winrate==null?'n/a':(e.breakeven_winrate*100).toFixed(1)+'%')+' | round-trip fee '+(g.fee_rt*100).toFixed(2)+'% | spread/slippage '+((g.spread_slippage_rt||0)*100).toFixed(2)+'%</div>';
 el.innerHTML=h+'</div>';}
async function journalRep(){
 const el=$('jr');el.textContent='...';
 const p=qs(),key=p.get('source')+':'+p.get('symbol'),f=v=>v==null?'n/a':(+v).toFixed(3);
 const {ok,j}=await api('/api/journal','symbol='+encodeURIComponent(key)+'&tf='+encodeURIComponent($('tf').value)+'&horizon='+encodeURIComponent($('hours').value||12));
 if(!ok){el.textContent=j.detail;return;}
 let h='resolved <b>'+j.n+'</b> / pending '+j.pending+' / expired '+j.expired+' &nbsp; <b>'+esc(j.verdict)+'</b>';
 if(j.n){h+='<br>Brier: model '+f(j.brier_model)+' | always-50% '+f(j.brier_half)+' | base-rate '+f(j.brier_base_rate)+' | skill vs 50% '+f(j.skill_vs_half)+
  '<br>score vs forward return: corr '+f(j.score_ic)+', t_eff '+f(j.score_t)+' (n_eff '+f(j.score_neff)+')'+
  '<br>Bullish calls: '+(j.bullish_n||0)+' | up-rate '+f(j.bullish_up_rate)+' (base '+f(j.base_rate_up)+') | avg ret '+f(j.bullish_avg_ret)+
  '<br>gate ALLOWED: '+j.allowed_n+' | avg ret '+f(j.allowed_avg_ret)+' | 80% band coverage '+f(j.band80_coverage);}
 el.innerHTML=h;}
function renderTradeIntelligence(ti){
 if(!ti)return '';
 const pct=v=>v==null?'n/a':Number(v).toFixed(3)+'%';
 const num=v=>v==null?'n/a':Number(v).toFixed(3);
 const o=ti.overall||{};
 const c=o.avg_cost_components_pct||{};
 const table=(title,groups)=>{
  const entries=Object.entries(groups||{});
  if(!entries.length)return '';
  let x='<h4>'+esc(title)+'</h4><table><thead><tr><th>Group</th><th>Trades</th><th>Net win rate</th><th>Net expectancy</th><th>PF</th><th>Avg MFE</th><th>Avg MAE</th></tr></thead><tbody>';
  entries.forEach(([k,v])=>x+='<tr><td>'+esc(k)+'</td><td>'+v.trades+'</td><td>'+pct(v.win_rate_pct)+'</td><td>'+pct(v.expectancy_pct)+'</td><td>'+num(v.profit_factor)+'</td><td>'+pct(v.avg_mfe_pct)+'</td><td>'+pct(v.avg_mae_pct)+'</td></tr>');
  return x+'</tbody></table>';
 };
 let h='<div class=box><h3>Trade Intelligence | '+esc(ti.status||'')+'</h3>';
 if(ti.trades===0)return h+'No completed trades.</div>';
 h+='<b>'+ti.trades+' trades</b> | average net expectancy '+pct(o.expectancy_pct)+' | PF '+num(o.profit_factor)+
  ' | average MFE '+pct(o.avg_mfe_pct)+' | average MAE '+pct(o.avg_mae_pct)+'<br>'+
  'Average combined cost drag/trade: <b>'+pct(o.avg_cost_drag_pct)+'</b>';
 if(c && Object.keys(c).length){
  h+='<div class=scenario-note><b>Average per-trade compounded cost components:</b> Fees '+pct(c.fee_drag_pct)+
   ' | Spread/slippage '+pct(c.spread_slippage_drag_pct)+' | Funding '+pct(c.funding_drag_pct)+'</div>';
 }
 h+=table('By side',ti.by_side)+table('By entry regime',ti.by_entry_regime)+
  table('By entry signal quality',ti.by_entry_signal_quality)+table('By exit reason',ti.by_exit_reason)+
  table('By holding duration',ti.by_holding_duration);
 if(ti.failure_diagnostics){const d=ti.failure_diagnostics;
  h+='<div class=scenario-note><b>Failure hypotheses (descriptive, not causal):</b> losing trades with MAE below -1%: '+
   d.losers_with_mae_below_minus_1pct+'; losing trades with favorable excursion above 0.2%: '+d.losers_with_positive_mfe_above_0_2pct+'</div>';
 }
 if(ti.sample_warning)h+='<div class=w>'+esc(ti.sample_warning)+'</div>';
 if(ti.cost_drag_note)h+='<div class=scenario-note>'+esc(ti.cost_drag_note)+'</div>';
 if(ti.excursion_note)h+='<div class=scenario-note>'+esc(ti.excursion_note)+'</div>';
 h+='<div class=scenario-note>'+esc(ti.guardrail||'Descriptive diagnostics only.')+'</div></div>';
 return h;
}

async function backtest(){
 const el=$('btout');el.textContent='running...';
 const num = v => v == null || !Number.isFinite(Number(v)) ? 'n/a' : Number(v).toFixed(3);
 const pct = v => v == null || !Number.isFinite(Number(v)) ? 'n/a' : Number(v).toFixed(2)+'%';
 try {
 const {ok,j}=await api('/api/backtest',researchQs());
 if(!ok){el.textContent=j.detail;return;} if(last)last.backtest=j;
 let h='<h3>Full-window backtest | descriptive / in-sample</h3>'+provenanceHtml(j)+researchComparisonHtml()+'<div>bars '+j.bars+' | trades '+j.trades+' | exposure '+j.exposure_pct+'% | gross win rate '+j.win_rate_pct+'% | net win rate '+(j.net_win_rate_pct??'n/a')+'% | net expectancy '+j.expectancy_pct+'% | net PF '+j.profit_factor+'<br>net strategy <b>'+
  j.strategy_pct+'%</b> vs cost-adjusted passive long <b>'+j.buyhold_net_pct+'%</b> (gross benchmark '+j.buyhold_gross_pct+'%; cost drag '+j.buyhold_cost_drag_pct+' pp) | max DD '+j.max_dd_pct+'% | fees/side '+j.fee_sides_charged+' | fee/side rate '+j.fee_per_side+'<br>Warm-up start '+(j.effective_start??'n/a')+' (required '+(j.required_indicator_warmup_bars??'n/a')+') | first executable fill bar '+(j.first_entry_fill_bar??'n/a')+' | warm-up shortfall '+(j.warmup_shortfall_bars??0)+' bars'+'<br><span class=n>'+esc(j.accounting)+'; '+esc(j.warning)+'</span></div>';
 const a=j.autopsy||{};
 if(a.n_trades){
  const pct=v=>v==null?'n/a':Number(v).toFixed(2)+'%';
  const group=(title,obj)=>{let x='<h4>'+esc(title)+'</h4><table><thead><tr><th>Group</th><th>Trades</th><th>Net win rate</th><th>Net expectancy</th><th>PF</th></tr></thead><tbody>';
   Object.entries(obj||{}).forEach(([k,v])=>x+='<tr><td>'+esc(k)+'</td><td>'+v.trades+'</td><td>'+pct(v.win_rate_pct)+'</td><td>'+pct(v.net_expectancy_pct)+'</td><td>'+ (v.net_profit_factor==null?'n/a':v.net_profit_factor)+'</td></tr>');
   return x+'</tbody></table>';};
  h+='<div class=box><h3>Strategy Autopsy | '+esc(a.verdict||'')+'</h3><b>'+a.n_trades+' trades</b> | gross win rate '+pct(a.overall?.gross_win_rate_pct)+' | net win rate '+pct(a.overall?.net_win_rate_pct)+' | net winners/losses/flat '+(a.overall?.net_winners??'n/a')+'/'+(a.overall?.net_losers??'n/a')+'/'+(a.overall?.net_breakeven??'n/a')+' | avg MFE '+pct(a.diagnostics?.avg_mfe_pct)+' | avg MAE '+pct(a.diagnostics?.avg_mae_pct);
  h+=group('By side',a.by_side)+group('By market regime at entry',a.by_regime)+group('By exit reason',a.by_exit_reason)+group('By signal strength',a.by_signal_band)+group('By holding duration',a.by_holding_duration);
  if(a.warnings?.length)h+='<ul class=w>'+a.warnings.map(x=>'<li>'+esc(x)+'</li>').join('')+'</ul>';
  h+='<div class=scenario-note>'+esc(a.guardrail||'')+'</div></div>';
 }
 h+=renderTradeIntelligence(j.trade_intelligence);
 const ev=j.execution_viability||{}, em=ev.metrics||{};
 if(ev.status){
  const ep=v=>v==null?'n/a':Number(v).toFixed(3)+'%';
  h+='<div class=box><h3>Execution Viability | '+esc(ev.verdict||'')+'</h3>'+   '<b>'+ev.samples+' executed trades</b> | mean gross '+ep(em.mean_gross_return_pct)+' | mean net '+ep(em.mean_net_return_pct)+   ' | modeled cost drag '+ep(em.mean_modeled_cost_drag_pct)+' | intrabar MFE '+ep(em.mean_intrabar_mfe_pct)+   '<br>MFE / cost ratio: '+(em.mfe_to_cost_ratio==null?'n/a':Number(em.mfe_to_cost_ratio).toFixed(3))+   ' | trades where MFE exceeded cost: '+ep(em.trades_mfe_exceeded_cost_pct)+   '<div class=scenario-note>Research only. MFE is not a realizable exit price and does not establish an executable exit edge.</div></div>';
 }
 const ed=j.trade_edge_discovery||{}, eo=ed.overall||{};
 if(ed.status){
  h+='<div class=box><h3>Trade Edge Discovery | '+esc(ed.verdict||'')+'</h3>'+   '<b>'+eo.samples+' trades</b> | net expectancy '+(eo.net_expectancy_pct==null?'n/a':Number(eo.net_expectancy_pct).toFixed(3)+'%')+   ' | net PF '+(eo.net_profit_factor??'n/a')+' | 95% bootstrap CI '+(eo.expectancy_bootstrap_ci_pct?eo.expectancy_bootstrap_ci_pct.map(x=>Number(x).toFixed(3)+'%').join(' to '):'n/a')+   '<div class=scenario-note>Research only; positive findings require chronological walk-forward and untouched out-of-sample validation.</div></div>';
 }
 const mr=j.market_regime_edge_research||{}, mt=j.market_regime_transition_edge_research||{};
 const mrg=(mr.conditional_groups||{}).market_regime||{}, mtg=(mt.conditional_groups||{}).entry_regime_transition||{};
 if(mr.status||mt.status){
  const rows=(obj)=>Object.entries(obj||{}).map(([k,v])=>'<tr><td>'+esc(k)+'</td><td>'+v.samples+'</td><td>'+pct(v.win_rate_pct)+'</td><td>'+pct(v.net_expectancy_pct)+'</td><td>'+(v.net_profit_factor??'n/a')+'</td></tr>').join('');
  h+='<div class=box><h3>Market Regime Intelligence | trade attribution</h3><b>By regime</b><table><thead><tr><th>Regime</th><th>N</th><th>Net win</th><th>Net expectancy</th><th>Net PF</th></tr></thead><tbody>'+rows(mrg)+'</tbody></table>';
  h+='<b>By transition event</b><table><thead><tr><th>Transition</th><th>N</th><th>Net win</th><th>Net expectancy</th><th>Net PF</th></tr></thead><tbody>'+rows(mtg)+'</tbody></table>';
  h+='<div class=scenario-note>Research attribution only. Full-history cohorts are descriptive; use the Walk-Forward OOS cohort report before considering any regime-based filter. No existing signal rules were changed.</div></div>';
 }
 const rd=j.research_diagnostics||{};
 if(rd.status){
  const sm=rd.summary||{};
  const fmt=v=>v==null?'n/a':Number(v).toFixed(3)+'%';
  h+='<div class=box><h3>Full-window Research Findings | in-sample descriptive only</h3>'+
   '<b>Trades:</b> '+rd.trades+' | sample threshold met: '+(rd.sample_sufficient_for_diagnostics?'yes':'no')+
   '<br>Net winners/losses: '+(sm.net_winners??'n/a')+'/'+(sm.net_losers??'n/a')+
   ' | expectancy: '+fmt(sm.mean_net_expectancy_pct)+' | PF: '+(sm.net_profit_factor??'n/a')+
   '<br>Mean cost drag: '+fmt(sm.mean_cost_drag_account_pct)+' | mean open-to-open MFE: '+fmt(sm.mean_mfe_open_to_open_pct)+
   ' | mean MAE: '+fmt(sm.mean_mae_open_to_open_pct)+
   '<br>Losers with MFE &gt; 0.20%: '+(sm.losers_with_mfe_above_0_20pct??0);
  if(rd.findings?.length){h+='<ul class=w>'+rd.findings.map(x=>'<li><b>'+esc(x.code)+'</b>: '+esc(x.finding)+'<br><span class=scenario-note>'+esc(x.interpretation)+'</span></li>').join('')+'</ul>';}
  h+='<p class=scenario-note>'+esc(rd.guardrail||'')+'</p></div>';
 }
 const pctResearch=v=>v==null?'n/a':Number(v).toFixed(3)+'%';
 const tr=j.trade_research||{};
 if(tr.status){
  h+='<div class=box><h3>Full-window Entry & Exit Diagnostics | descriptive only</h3><b>Entry score cohorts</b><table><thead><tr><th>Score cohort</th><th>Trades</th><th>Mean net return</th><th>Median net return</th><th>Positive trades</th></tr></thead><tbody>';
  Object.entries(tr.entry_score_cohorts||{}).forEach(([k,v])=>h+='<tr><td>'+esc(k)+'</td><td>'+v.samples+'</td><td>'+pctResearch(v.mean_return_pct)+'</td><td>'+pctResearch(v.median_return_pct)+'</td><td>'+pctResearch(v.positive_pct)+'</td></tr>');
  h+='</tbody></table>';
  const ec=tr.exit_capture||{};
  h+='<b>Exit capture</b><p>Trades with MFE: '+(ec.trades_with_mfe||0)+' | winner-only gross MFE capture: '+pctResearch(ec.mean_gross_mfe_capture_pct)+' | losing trades with positive MFE: '+(ec.losing_trades_with_positive_mfe??0)+' | mean unused favorable excursion: '+pctResearch(ec.mean_unused_favorable_excursion_pct)+'</p>';
  if(ec.records?.length){h+='<details><summary>Per-trade exit capture records</summary><table><thead><tr><th>Bars</th><th>Gross</th><th>Net</th><th>MFE</th><th>Gross/MFE capture</th><th>Unused MFE</th><th>Exit</th></tr></thead><tbody>';ec.records.forEach(x=>h+='<tr><td>'+esc(x.bars_held)+'</td><td>'+pctResearch(x.gross_return_pct)+'</td><td>'+pctResearch(x.net_return_pct)+'</td><td>'+pctResearch(x.mfe_pct)+'</td><td>'+pctResearch(x.gross_mfe_capture_pct)+'</td><td>'+pctResearch(x.unused_favorable_excursion_pct)+'</td><td>'+esc(x.exit_reason)+'</td></tr>');h+='</tbody></table></details>';}
  h+='<p class=scenario-note>'+esc(ec.method_note||'')+' '+esc(tr.guardrail||'')+'</p></div>';
 }
 const rs=j.rejected_signal_research||{};
 if(rs.status){h+='<div class=box><h3>Full-window Rejected Bullish Candidate Labels | retrospective only</h3><p>'+esc(rs.definition||rs.reason||'')+'</p><b>Candidate observations: '+(rs.candidates??0)+'</b>';
  if(rs.horizons){h+='<table><thead><tr><th>Horizon (bars)</th><th>Samples</th><th>Mean raw return</th><th>Median raw return</th><th>Positive observations</th></tr></thead><tbody>';Object.entries(rs.horizons).forEach(([k,v])=>h+='<tr><td>'+k+'</td><td>'+v.samples+'</td><td>'+pctResearch(v.mean_return_pct)+'</td><td>'+pctResearch(v.median_return_pct)+'</td><td>'+pctResearch(v.positive_pct)+'</td></tr>');h+='</tbody></table>';}
  h+='<p class=scenario-note>'+esc(rs.warning||'')+'</p></div>';}
 const pctStress=v=>v==null?'n/a':Number(v).toFixed(2)+'%';
 const forensic=j.trade_forensics||[];
 if(forensic.length){h+='<details class=box><summary><b>Trade Forensics | '+forensic.length+' auditable trade records</b></summary><div style=overflow-x:auto><table><thead><tr><th>#</th><th>Side</th><th>Entry → Exit</th><th>Gross asset</th><th>Gross account</th><th>Net account</th><th>Net result</th><th>Cost drag</th><th>MFE / MAE</th><th>Bars</th><th>Exit</th></tr></thead><tbody>';
 forensic.forEach(t=>h+='<tr><td>'+t.trade_index+'</td><td>'+esc(t.side)+'</td><td>'+esc(t.entry_px)+' → '+esc(t.exit_px)+'</td><td>'+pctStress(t.gross_asset_return_pct)+'</td><td>'+pctStress(t.gross_account_return_pct)+'</td><td>'+pctStress(t.net_account_return_pct)+'</td><td>'+esc(t.net_result)+'</td><td>'+pctStress(t.cost_drag_account_pct)+'</td><td>'+pctStress(t.mfe_pct)+' / '+pctStress(t.mae_pct)+'</td><td>'+t.bars_held+'</td><td>'+esc(t.exit_reason)+'</td></tr>');
 h+='</tbody></table></div><p class=scenario-note>Gross asset return is price-direction return; account returns include position sizing/leverage. MFE/MAE are open-to-open price excursions, not intrabar execution outcomes.</p></details>';}
 const st=j.execution_cost_stress;
 if(st?.scenarios?.length){h+='<div class=box><h3>Execution Cost Stress Test</h3><p class=scenario-note>'+esc(st.method)+' '+esc(st.warning)+'</p><table><thead><tr><th>Scenario</th><th>Cost x</th><th>Net return</th><th>Trades</th><th>Net expectancy</th><th>Net PF</th><th>Net win rate</th><th>Max DD</th></tr></thead><tbody>';
 st.scenarios.forEach(x=>h+='<tr><td>'+esc(x.scenario)+'</td><td>'+x.cost_multiplier+'x</td><td>'+pctStress(x.strategy_pct)+'</td><td>'+x.trades+'</td><td>'+pctStress(x.net_expectancy_pct)+'</td><td>'+num(x.net_profit_factor)+'</td><td>'+pctStress(x.net_win_rate_pct)+'</td><td>'+pctStress(x.max_dd_pct)+'</td></tr>');
 h+='</tbody></table></div>';}
 el.innerHTML=h;
 } catch(err) { el.innerHTML='<div class=w>Backtest display error: '+esc(err?.message||String(err))+'</div>'; console.error('ChartAnalyzer backtest UI error:',err); }
}
async function walkForward(){
 const el=$('wfo');el.textContent='running out-of-sample folds...';
 const p=researchQs();
 const {ok,j}=await api('/api/walk-forward',p);
 if(!ok){el.innerHTML='<b class=s>Walk-Forward failed:</b> '+esc(j.detail||'unknown error');return;}
 if(last)last.walk_forward=j;
 const pct=v=>v==null?'n/a':Number(v).toFixed(2)+'%';
 const num=v=>v==null?'n/a':Number(v).toFixed(3);
 let h='<h3>Walk-Forward | Out-of-Sample</h3>'+provenanceHtml(j)+researchComparisonHtml()+'<b>'+esc(j.market_type||'spot').toUpperCase()+'</b> | '+esc(j.symbol)+' | '+esc(j.timeframe)+
  ' | '+j.candles+' candles | '+j.n_folds+' folds | '+j.oos_bars+' OOS bars<br>'+
  'Trades <b>'+j.trades+'</b> | Net strategy <b class='+(Number(j.strategy_pct)>=0?'b':'s')+'>'+pct(j.strategy_pct)+'</b> | Net passive long '+pct(j.buyhold_net_pct)+
  ' (gross '+pct(j.buyhold_gross_pct)+', cost drag '+pct(j.buyhold_cost_drag_pct)+') | Excess vs net benchmark '+pct(j.excess_vs_buyhold_pct)+'<br>'+
  'Net expectancy '+pct(j.net_expectancy_pct)+' | Net PF '+num(j.net_profit_factor)+' | Win rate '+pct(j.net_win_rate_pct)+' | Max DD '+pct(j.max_dd_pct)+'<br>'+
  'Warm-up '+j.warmup_bars+' bars (configured '+(j.configured_warmup_bars??'n/a')+', required '+(j.required_indicator_warmup_bars??'n/a')+') | Embargo '+j.gap_bars+' bars | Restricted-hour blocked OOS bars: '+(j.entry_hours_blocked_bars??0)+' | ATR exits: stop '+(j.stop_atr_mult??1.5)+'x / target '+(j.target_atr_mult??2.0)+'x | Frozen signal: '+(j.strategy_frozen?'yes':'no')+' | Parameter optimization: '+(j.parameter_optimization?'yes':'no');
 h+='<div class=box><b>Fold breakdown</b><table><thead><tr><th>Fold</th><th>OOS bars</th><th>Trades</th><th>Net strategy</th><th>Net passive long</th><th>Gross passive long</th><th>Benchmark cost drag</th><th>Excess vs net B&amp;H</th><th>Expectancy</th><th>PF</th><th>Win rate</th><th>Max DD</th></tr></thead><tbody>';
 (j.folds||[]).forEach(f=>{const excess=f.excess_vs_buyhold_pct!=null?f.excess_vs_buyhold_pct:((f.strategy_pct!=null&&f.buyhold_pct!=null)?f.strategy_pct-f.buyhold_pct:null);h+='<tr><td>'+f.fold+'</td><td>'+f.oos_bars+'</td><td>'+f.trades+'</td><td>'+pct(f.strategy_pct)+'</td><td>'+pct(f.buyhold_net_pct??f.buyhold_pct)+'</td><td>'+pct(f.buyhold_gross_pct)+'</td><td>'+pct(f.buyhold_cost_drag_pct)+'</td><td>'+pct(excess)+'</td><td>'+pct(f.net_expectancy_pct)+'</td><td>'+num(f.net_profit_factor)+'</td><td>'+pct(f.net_win_rate_pct)+'</td><td>'+pct(f.max_dd_pct)+'</td></tr>';});
 h+='</tbody></table></div>';
 const dv=j.deployment_eligibility||{};
 if(dv.status){
  h+='<div class=box><h3>Strategy Deployment Eligibility | <span class='+(dv.eligible?'b':'s')+'>'+esc(dv.status)+'</span></h3>'+   'OOS trades '+(dv.oos_trades??0)+' | net expectancy '+pct(dv.net_expectancy_pct)+' | PF '+num(dv.net_profit_factor)+   ' | OOS DD '+pct(dv.max_oos_drawdown_pct)+' | FDR q '+num(dv.adjusted_p_value)+' | 1.5x cost stress '+(dv.cost_stress_1_5x_positive?'PASS':'FAIL')+   ' | positive folds '+(dv.positive_folds??0)+'/'+(dv.fold_count??0)+' | positive-fold ratio '+pct((dv.positive_fold_ratio??0)*100)+   ' | block-bootstrap 95% CI '+(dv.bootstrap_ci_95_pct?pct(dv.bootstrap_ci_95_pct[0])+' to '+pct(dv.bootstrap_ci_95_pct[1]):'n/a')+   (dv.reasons?.length?'<ul class=w>'+dv.reasons.map(x=>'<li>'+esc(x)+'</li>').join('')+'</ul>':'<p class=b>All configured OOS evidence requirements passed. The evidence is time-limited and tied to this exact market/configuration.</p>')+   '<p class=scenario-note>Passing this gate is necessary, not a promise of profitability. Live plan EV, fees, risk/reward, and restricted-hour checks still apply.</p></div>';
 }
 const er=j.entry_edge_research||{};
 if(er.status){
  const eo=er.overall||{};
  h+='<div class=box><h3>Entry Edge Attribution | OOS executed trades only</h3><b>Trades:</b> '+(er.samples??0)+
   ' | net expectancy '+pct(eo.net_expectancy_pct)+' | PF '+num(eo.net_profit_factor)+' | win rate '+pct(eo.win_rate_pct)+
   ' | moving-block bootstrap 95% CI '+(eo.bootstrap_ci_95_pct?pct(eo.bootstrap_ci_95_pct[0])+' to '+pct(eo.bootstrap_ci_95_pct[1]):'n/a')+
   '<table><thead><tr><th>Directional entry-score cohort</th><th>Trades</th><th>Net expectancy</th><th>Net PF</th><th>Win rate</th><th>95% block-bootstrap CI</th><th>Evidence</th></tr></thead><tbody>';
  Object.entries(er.directional_score_cohorts||{}).forEach(([k,v])=>h+='<tr><td>'+esc(k)+'</td><td>'+v.samples+'</td><td>'+pct(v.net_expectancy_pct)+'</td><td>'+num(v.net_profit_factor)+'</td><td>'+pct(v.win_rate_pct)+'</td><td>'+(v.bootstrap_ci_95_pct?pct(v.bootstrap_ci_95_pct[0])+' to '+pct(v.bootstrap_ci_95_pct[1]):'n/a')+'</td><td>'+esc(v.status)+'</td></tr>');
  h+='</tbody></table><p class=scenario-note>'+esc(er.method||'')+' '+esc((er.guardrails||[]).join(' '))+'</p></div>';
 }
 const ri=j.research_cohort_integrity||{};
 if(ri.status){h+='<div class=box><h3>OOS Report Cohort Integrity | <span class='+(ri.status==='PASS'?'b':'s')+'>'+esc(ri.status)+'</span></h3>'+esc(JSON.stringify(ri.module_counts||{}))+
  '<br>Checks: '+esc(JSON.stringify(ri.checks||{}))+(ri.mismatches?.length?'<br><span class=s>Mismatches: '+esc(ri.mismatches.join(', '))+'</span>':'')+
  '<p class=scenario-note>'+esc(ri.policy||'')+'</p></div>';}
 h+=renderTradeIntelligence(j.trade_intelligence);
 const sf=j.signal_forensics||{};
 if(sf.status){
  h+='<div class=box><h3>Signal Forensics | descriptive only</h3><b>Analyzed trades:</b> '+(sf.trades_analyzed??0)+
   ' | Losing trades with positive intrabar MFE: '+(sf.losing_trades_with_positive_intrabar_mfe??0)+
   ' ('+(sf.losing_trades_with_positive_intrabar_mfe_pct??'n/a')+'% of losers)'+
   '<br><b>Exit reasons:</b> '+esc(JSON.stringify(sf.by_exit_reason||{}))+
   '<br><b>Entry regimes:</b> '+esc(JSON.stringify(sf.by_entry_regime||{}))+
   '<br><b>Signal conviction:</b> '+esc(JSON.stringify(sf.by_entry_conviction||{}));
  if(sf.trades?.length){h+='<details><summary>Per-trade signal and component breakdown</summary><div style=overflow-x:auto><table><thead><tr><th>#</th><th>Entry/Exit</th><th>Score</th><th>Conviction</th><th>Regime</th><th>Net</th><th>Intrabar MFE</th><th>Intrabar MAE</th><th>Positive/negative component votes</th><th>Exit</th></tr></thead><tbody>';
   sf.trades.forEach(t=>h+='<tr><td>'+t.trade_index+'</td><td>'+t.entry_bar+' → '+t.exit_bar+'</td><td>'+num(t.entry_signal_score)+'</td><td>'+esc(t.entry_conviction)+'</td><td>'+esc(t.entry_regime)+'</td><td>'+pct(t.net_return_pct)+'</td><td>'+pct(t.intrabar_mfe_pct)+'</td><td>'+pct(t.intrabar_mae_pct)+'</td><td>'+t.component_positive_votes+'/'+t.component_negative_votes+'</td><td>'+esc(t.exit_reason)+'</td></tr>');
   h+='</tbody></table></div></details>';}
  h+='<p class=scenario-note>'+esc(sf.method||'')+' '+esc(sf.warning||'')+'</p></div>';
 }
 const xp=j.exit_path_intelligence||{};
 if(xp.status){
  const xo=xp.overall||{};
  h+='<div class=box><h3>Exit Path Intelligence | descriptive only</h3><b>Audited trades:</b> '+(xp.trades_analyzed??0)+
   ' | losers with positive MFE: '+(xo.losers_with_positive_mfe??0)+
   ' | mean MFE: '+pct(xo.mean_mfe_pct)+' | mean giveback: '+pct(xo.mean_giveback_pct)+
   ' | winner-only mean gross MFE capture: '+(xo.mean_gross_mfe_capture_ratio==null?'n/a':Number(xo.mean_gross_mfe_capture_ratio).toFixed(3))+' (n='+(xo.gross_mfe_capture_ratio_n??0)+')';
  h+='<table><thead><tr><th>Exit reason</th><th>Trades</th><th>Positive MFE</th><th>Losers after MFE</th><th>Mean giveback</th><th>Median winner-only capture (n)</th><th>Bars peak → exit</th></tr></thead><tbody>';
  Object.entries(xp.by_exit_reason||{}).forEach(([k,v])=>h+='<tr><td>'+esc(k)+'</td><td>'+v.trades+'</td><td>'+v.positive_mfe_trades+'</td><td>'+v.losers_with_positive_mfe+'</td><td>'+pct(v.mean_giveback_pct)+'</td><td>'+(v.median_gross_mfe_capture_ratio==null?'n/a':Number(v.median_gross_mfe_capture_ratio).toFixed(3)+' (n='+v.gross_mfe_capture_ratio_n+')')+'</td><td>'+v.mean_bars_from_peak_to_exit+'</td></tr>');
  h+='</tbody></table>';
  if(xp.trades?.length){h+='<details><summary>Per-trade peak and giveback audit</summary><div style=overflow-x:auto><table><thead><tr><th>#</th><th>Peak timestamp</th><th>MFE</th><th>Peak → exit bars</th><th>Realized gross</th><th>Giveback</th><th>Capture ratio</th><th>Exit</th><th>Classification</th></tr></thead><tbody>';
   xp.trades.forEach(t=>h+='<tr><td>'+t.trade_index+'</td><td>'+esc(t.mfe_peak_timestamp||'n/a')+'</td><td>'+pct(t.mfe_pct)+'</td><td>'+t.bars_from_mfe_peak_to_exit+'</td><td>'+pct(t.realized_gross_price_return_pct)+'</td><td>'+pct(t.favorable_excursion_giveback_pct)+'</td><td>'+(t.gross_mfe_capture_ratio==null?'n/a':Number(t.gross_mfe_capture_ratio).toFixed(3))+'</td><td>'+esc(t.exit_reason)+'</td><td>'+esc(t.classification)+'</td></tr>');
   h+='</tbody></table></div></details>';}
  h+='<p class=scenario-note>Winner-only capture ratio uses only positive gross exits with positive MFE. Losing trades are reported separately; signed ratios remain in per-trade detail. '+esc((xp.guardrails||[]).join(' '))+'</p></div>';
 }
 const xpr=j.exit_policy_research||{};
 if(xpr.status){
  h+='<div class=box><h3>Exit Policy Research | paired counterfactuals, research only</h3>';
  h+='<p>Early time exits are compared with actual net returns on the same OOS trades. Only exits strictly before the recorded exit qualify; configured ATR barriers and modeled costs are retained.</p>';
  h+='<table><thead><tr><th>Horizon</th><th>Paired trades</th><th>CF net expectancy</th><th>Actual same-cohort expectancy</th><th>Paired delta</th><th>95% bootstrap CI of delta</th><th>CF PF</th><th>Improved trades</th><th>Status</th></tr></thead><tbody>';
  Object.entries(xpr.horizons||{}).forEach(([k,v])=>{const ci=v.paired_delta_bootstrap_95_ci_pct;h+='<tr><td>'+k+'</td><td>'+v.samples+'</td><td>'+pct(v.counterfactual_mean_net_pct)+'</td><td>'+pct(v.paired_actual_mean_net_pct)+'</td><td>'+pct(v.paired_mean_net_delta_pct)+'</td><td>'+(ci?pct(ci[0])+' to '+pct(ci[1]):'n/a')+'</td><td>'+(v.counterfactual_net_profit_factor==null?'n/a':num(v.counterfactual_net_profit_factor))+'</td><td>'+pct(v.trades_improved_pct)+'</td><td>'+esc(v.status)+'</td></tr>';});
  h+='</tbody></table><p class=scenario-note>'+esc(xpr.method||'')+' '+esc((xpr.guardrails||[]).join(' '))+'</p></div>';
 }
 const fi=j.trade_failure_intelligence||{};
 if(fi.status){
  h+='<div class=box><h3>OOS Exit Failure Reconstruction | detailed forensic audit</h3><b>Audited trades:</b> '+(fi.trades_analyzed??0)+' | invalid records: '+(fi.trades_invalid??0)+
   '<br><b>Failure tags:</b> '+esc(JSON.stringify(fi.failure_tag_counts||{}))+
   '<br><b>Exit reasons:</b> '+esc(JSON.stringify(fi.by_exit_reason||{}));
  const hs=fi.early_time_exit_scenarios||{};
  if(Object.keys(hs).length){h+='<h4>Counterfactual early time exits</h4><table><thead><tr><th>Horizon</th><th>Samples</th><th>Mean estimated net</th><th>Median estimated net</th><th>Positive</th></tr></thead><tbody>';
   Object.entries(hs).forEach(([k,v])=>h+='<tr><td>'+esc(k)+' bars</td><td>'+v.samples+'</td><td>'+pct(v.mean_estimated_net_return_pct)+'</td><td>'+pct(v.median_estimated_net_return_pct)+'</td><td>'+pct(v.positive_pct)+'</td></tr>');
   h+='</tbody></table>';}
  if(fi.trades?.length){h+='<details><summary>Per-trade failure reconstruction ('+fi.trades.length+')</summary><div style=overflow-x:auto><table><thead><tr><th>#</th><th>Entry → Exit</th><th>Entry score</th><th>Pre-exit score</th><th>Score Δ</th><th>MFE / MAE</th><th>First ATR event</th><th>Exit</th><th>Failure tags</th></tr></thead><tbody>';
   fi.trades.forEach(t=>{h+='<tr><td>'+t.trade_index+'</td><td>'+t.entry_bar+' → '+t.exit_bar+'</td><td>'+num(t.entry_signal_score)+'</td><td>'+num(t.pre_exit_signal_score)+'</td><td>'+num(t.signal_score_change)+'</td><td>'+pct(t.intrabar_mfe_pct)+' / '+pct(t.intrabar_mae_pct)+'</td><td>'+esc(t.first_atr_level_event||'n/a')+'</td><td>'+esc(t.exit_reason||'')+'</td><td>'+esc((t.failure_tags||[]).join(', '))+'</td></tr>';
    if(t.component_score_changes||t.counterfactual_early_time_exits?.length){h+='<tr><td colspan=9><small>Component changes: '+esc(JSON.stringify(t.component_score_changes||{}))+' | Early exits: '+esc(JSON.stringify(t.counterfactual_early_time_exits||[]))+'</small></td></tr>';}});
   h+='</tbody></table></div></details>';}
  h+='<p class=scenario-note>'+esc((fi.guardrails||[]).join(' '))+'</p></div>';
 }
 h+='<div class=scenario-note>'+esc(j.warning||'')+'</div>';
 if(j.trades<30)h+='<div class=w>هشدار: تعداد معاملات کمتر از ۳۰ است؛ نتیجه آماری هنوز کم‌نمونه است.</div>';
 $('wfo').innerHTML=h;
}
async function marginCalc(){
 const el=$('margout');el.textContent='calculating...';
 const p=new URLSearchParams({market_type:'futures',side:$('m_side').value,
  entry_price:$('m_entry').value,quantity:$('m_qty').value,leverage:$('m_lev').value,
  maintenance_margin_rate:(Number($('m_mmr').value)/100).toString(),
  additional_margin:$('m_extra').value,
  liquidation_fee_rate:(Number($('m_liqfee').value)/100).toString(),
  mark_price:$('m_mark').value||'0'});
 const {ok,j}=await api('/api/margin',p);
 if(!ok){el.innerHTML='<b class=s>Margin calculation failed:</b> '+esc(j.detail||'invalid input');return;}
 const n=v=>v==null?'n/a':Number(v).toLocaleString(undefined,{maximumFractionDigits:8});
 let h='<b>'+esc(j.side.toUpperCase())+'</b> | Isolated Futures | '+j.leverage+'x<br>'+
  'Notional: <b>'+n(j.notional)+'</b> | Initial margin: <b>'+n(j.initial_margin)+'</b> | Total collateral: <b>'+n(j.total_isolated_collateral)+'</b><br>'+
  'Maintenance margin (entry): '+n(j.maintenance_margin_at_entry)+' | Estimated liquidation price: <b class=w>'+n(j.estimated_liquidation_price)+'</b><br>'+
  'Distance from entry: '+n(j.liquidation_distance_from_entry_pct)+'%';
 if(j.mark_price!=null){let cls=j.margin_health==='healthy'?'b':j.margin_health==='warning'?'w':'s';
  if(j.price_scale_warning){h+='<div class=w><b>Price-unit warning:</b> '+esc(j.price_scale_warning)+'</div>';
   h+='<hr>Mark '+n(j.mark_price)+' | PnL: <b class=s>UNVERIFIED / N/A</b> | Equity: <b class=s>UNVERIFIED / N/A</b>'+
    '<br>Distance to liquidation: <b class=s>N/A</b> | Margin ratio: <b class=s>N/A</b> | Health <b class=s>unverified_inputs</b>'+
    '<div class=scenario-note>Risk metrics are suppressed until entry and mark prices are confirmed to use the same market and quote-currency units.</div>';
  }else h+='<hr>Mark '+n(j.mark_price)+' | Unrealized PnL '+n(j.unrealized_pnl)+' | Equity estimate '+n(j.current_equity_estimate)+
   '<br>Distance to liquidation: <b class='+cls+'>'+n(j.distance_to_liquidation_pct)+'%</b> | Margin ratio '+n(j.margin_ratio_pct)+'% | Health <b class='+cls+'>'+esc(j.margin_health)+'</b>';}
 h+='<div class=scenario-note>'+esc(j.warning)+'</div><details><summary>Assumptions</summary><ul>'+(j.assumptions||[]).map(x=>'<li>'+esc(x)+'</li>').join('')+'</ul></details>';
 el.innerHTML=h;
}
async function dataIntegrity(){
 const el=$('di');el.textContent='checking candles...';
 const {ok,j}=await api('/api/data-integrity',qs());
 if(!ok){el.textContent=j.detail;return;}
 el.innerHTML='<b>Data integrity: '+esc(j.state)+'</b> | candles '+j.candles+' | gaps '+j.gap_count+' | off-grid '+j.off_grid_interval_count+
  '<br>'+((j.errors||[]).map(esc).join('<br>'))+
  ((j.warnings||[]).length?'<br>'+j.warnings.map(esc).join('<br>'):'<br>No warnings');
}
async function loadProviders(){
 try{const {j}=await api('/api/providers');
  $('prov').textContent='keys: '+Object.entries(j).map(([k,v])=>k+(v?' ok':' missing')).join(' | ');}catch(e){}}
async function loadModels(which){
 const prov=which=='local'?'local':$('cloud').value,free=$('free').checked?1:0;
 const {ok,j}=await api('/api/models','provider='+encodeURIComponent(prov)+'&free='+free);
 if(!ok){alert(j.detail);return;}
 $(which=='local'?'lml':'cml').innerHTML=j.models.map(m=>'<option value="'+esc(m)+'">').join('');
 alert(j.models.length+' models loaded - click the field to pick one');}
async function explain(){
 if(!last){alert('Run Analyze first');return;}
 const g=id=>$(id).value,out=$('out');out.textContent='working...';
 const body={...last,candles:undefined,question:g('q'),mode:g('mode'),cloud:g('cloud'),lang:g('lang'),
  models:{local:g('lm'),cloud:g('cm')}};
 const r=await fetch('/api/explain',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
 let j={};try{j=await r.json()}catch(e){j={detail:'HTTP '+r.status}}
 if(!r.ok){out.textContent=j.detail;return;}
 out.innerHTML=j.parts.map(p=>'<h4>'+esc(p.title)+'</h4><pre dir=auto>'+esc(p.text)+'</pre>').join('');}

async function iranStatus(){const el=$('iranout');el.textContent='checking MCP connection...';
 const {ok,j}=await api('/api/iran-crypto/status');el.textContent=(j.state||'ERROR')+' · '+(j.message||'');}
function iranParams(){return 'coin='+encodeURIComponent($('ic_coin').value.trim().toUpperCase())+'&quote='+encodeURIComponent($('ic_quote').value)+'&exchange='+encodeURIComponent($('ic_exchange').value);}
async function iranFetch(path){const el=$('iranout');el.textContent='requesting public exchange data...';
 const {ok,j}=await api(path,iranParams()+(path.includes('candles')?'&timeframe='+encodeURIComponent($('ic_tf').value)+'&limit=100':path.includes('orderbook')?'&levels=10':''));
 if(!ok){el.textContent=j.detail||'MCP request failed';return;}
 el.innerHTML='<b>Source:</b> iran-crypto-mcp · <b>Read-only</b><pre dir=auto>'+esc(JSON.stringify(j.data,null,2))+'</pre>';
}
function iranCompare(){iranFetch('/api/iran-crypto/compare')}
function iranOrderbook(){iranFetch('/api/iran-crypto/orderbook')}
function iranCandles(){iranFetch('/api/iran-crypto/candles')}

/* ---------- chart ---------- */
function setData(c){D=c;TIM=new Map(c.map((k,i)=>[k[0],i]));DR=[];pend=null;resetView();}
function resetView(){const f=(FC&&SF)?FC.h:0;V.count=Math.min(100,D.length)+f;V.start=D.length+f-V.count+5;render();}
function toggleFC(){SF=!SF;resetView();}
function setMode(m){mode=(m&&mode===m)?null:m;pend=null;CV.style.cursor=mode?'crosshair':'grab';
 $('bt').classList.toggle('on',mode=='trend');$('bh').classList.toggle('on',mode=='hline');render();}
function undo(){DR.pop();render();}
function clearDr(){DR=[];pend=null;render();}
function clampV(){V.start=Math.max(-V.count+10,Math.min(NT()-10,V.start));}
function geom(){const dpr=window.devicePixelRatio||1,w=CV.clientWidth,h=CV.clientHeight;
 if(CV.width!=Math.round(w*dpr)||CV.height!=Math.round(h*dpr)){CV.width=Math.round(w*dpr);CV.height=Math.round(h*dpr);}
 X2.setTransform(dpr,0,0,dpr,0,0);
 const pw=w-AX,step=pw/V.count;
 const a=Math.max(0,Math.floor(V.start)),b=Math.min(D.length-1,Math.ceil(V.start+V.count));
 let hi=-1e300,lo=1e300;
 for(let i=a;i<=b;i++){hi=Math.max(hi,D[i][2]);lo=Math.min(lo,D[i][3]);}
 if(FC&&SF){const r0=Math.floor(V.start),r1=Math.ceil(V.start+V.count);
  for(let k=1;k<=FC.h;k++){const i=D.length-1+k;if(i>=r0&&i<=r1){hi=Math.max(hi,FC.q90[k-1]);lo=Math.min(lo,FC.q10[k-1]);}}}
 if(hi<lo){hi=1;lo=0}
 const pad=(hi-lo)*0.06||1;hi+=pad;lo-=pad;
 return {w,h,pw,step,a,b,hi,lo,
  X:i=>(i-V.start+0.5)*step, I:x=>x/step+V.start-0.5,
  Y:p=>(h-4)-(p-lo)/(hi-lo)*(h-8), P:y=>lo+((h-4)-y)/(h-8)*(hi-lo)};}
function seg(d,g,dash){const x=X2;x.setLineDash(dash?[5,4]:[]);x.beginPath();
 if(d.t=='h'){x.moveTo(0,g.Y(d.p));x.lineTo(g.pw,g.Y(d.p));}
 else{x.moveTo(g.X(d.a.i),g.Y(d.a.p));x.lineTo(g.X(d.b.i),g.Y(d.b.p));}
 x.stroke();x.setLineDash([]);}
function drawCone(g,x){
 const n0=D.length-1,L=D[n0][4],Xk=k=>g.X(n0+k);
 const band=(lo,hi,col)=>{x.fillStyle=col;x.beginPath();x.moveTo(Xk(0),g.Y(L));
  for(let k=1;k<=FC.h;k++)x.lineTo(Xk(k),g.Y(hi[k-1]));
  for(let k=FC.h;k>=1;k--)x.lineTo(Xk(k),g.Y(lo[k-1]));
  x.closePath();x.fill();};
 const pl=(arr,col,w,dash)=>{x.strokeStyle=col;x.lineWidth=w;x.setLineDash(dash);x.beginPath();x.moveTo(Xk(0),g.Y(L));
  for(let k=1;k<=FC.h;k++)x.lineTo(Xk(k),g.Y(arr[k-1]));x.stroke();x.setLineDash([]);};
 band(FC.q10,FC.q90,'rgba(56,189,248,0.10)');band(FC.q25,FC.q75,'rgba(56,189,248,0.18)');
 pl(FC.sc[0],'rgba(248,113,113,0.7)',1,[3,3]);pl(FC.sc[2],'rgba(74,222,128,0.7)',1,[3,3]);pl(FC.sc[1],'rgba(216,222,233,0.6)',1,[3,3]);
 pl(FC.q50,'#38bdf8',1.8,[]);
 const nx=g.X(n0)+g.step/2;x.strokeStyle='#4b5563';x.lineWidth=1;x.setLineDash([2,4]);x.beginPath();x.moveTo(nx,0);x.lineTo(nx,g.h);x.stroke();x.setLineDash([]);}
function render(){
 if(!D.length)return; const g=geom(),x=X2; x.clearRect(0,0,g.w,g.h);
 x.font='11px system-ui';x.textBaseline='middle';x.lineWidth=1;
 for(let k=0;k<=5;k++){const p=g.lo+(g.hi-g.lo)*k/5,y=g.Y(p);
  x.strokeStyle='#18202a';x.beginPath();x.moveTo(0,y);x.lineTo(g.pw,y);x.stroke();
  x.fillStyle='#7b8794';x.fillText(fmt(p),g.pw+4,y);}
 x.save();x.beginPath();x.rect(0,0,g.pw,g.h);x.clip();
 if(FC&&SF)drawCone(g,x);
 x.lineWidth=1;
 const bw=Math.max(1,g.step*0.7);
 for(let i=g.a;i<=g.b;i++){const k=D[i],cx=g.X(i),up=k[4]>=k[1];
  x.strokeStyle=x.fillStyle=up?'#4ade80':'#f87171';
  x.beginPath();x.moveTo(cx,g.Y(k[2]));x.lineTo(cx,g.Y(k[3]));x.stroke();
  x.fillRect(cx-bw/2,Math.min(g.Y(k[1]),g.Y(k[4])),bw,Math.max(1,Math.abs(g.Y(k[1])-g.Y(k[4]))));}
 if(TRD&&ST)drawTrend(g,x);
 x.strokeStyle='#facc15';x.lineWidth=1.5;
 DR.forEach(d=>seg(d,g,false));
 if(pend&&M)seg({t:'trend',a:pend,b:{i:g.I(M.x),p:g.P(M.y)}},g,true);
 LV.forEach(l=>{x.strokeStyle=l.kind=='S'?'#38bdf8':'#fb923c';x.lineWidth=1;x.setLineDash([2,3]);x.beginPath();x.moveTo(0,g.Y(l.p));x.lineTo(g.pw,g.Y(l.p));x.stroke();x.setLineDash([]);});
 x.lineWidth=1;
 if(M&&M.x<g.pw){x.strokeStyle='#4b5563';x.setLineDash([4,4]);x.beginPath();
  x.moveTo(M.x,0);x.lineTo(M.x,g.h);x.moveTo(0,M.y);x.lineTo(g.pw,M.y);x.stroke();x.setLineDash([]);}
 x.restore();
 if(M&&M.x<g.pw){x.fillStyle='#2b6cb0';x.fillRect(g.pw,M.y-9,AX,18);x.fillStyle='#fff';
  x.fillText(fmt(g.P(M.y)),g.pw+4,M.y);
  const idx=Math.round(g.I(M.x));let t='';
  if(idx>=0&&idx<D.length){const k=D[idx];t=new Date(k[0]*1000).toLocaleString()+'  O '+fmt(k[1])+'  H '+fmt(k[2])+'  L '+fmt(k[3])+'  C '+fmt(k[4]);}
  else if(FC&&SF){const k=idx-(D.length-1);if(k>=1&&k<=FC.h)t='forecast '+new Date(FC.t[k-1]*1000).toLocaleString()+'  10% '+fmt(FC.q10[k-1])+'  50% '+fmt(FC.q50[k-1])+'  90% '+fmt(FC.q90[k-1]);}
  $('info').textContent=t;}
}
CV.addEventListener('mousemove',e=>{const r=CV.getBoundingClientRect();M={x:e.clientX-r.left,y:e.clientY-r.top};
 if(drag&&D.length){V.start=drag.s-(M.x-drag.x)/geom().step;clampV();}
 render();});
CV.addEventListener('mouseleave',()=>{M=null;render();});
CV.addEventListener('mousedown',e=>{
 if(e.button!==0||!D.length||!M)return; const g=geom(); if(M.x>=g.pw)return;
 if(mode=='hline'){DR.push({t:'h',p:g.P(M.y)});setMode(null);}
 else if(mode=='trend'){const pt={i:g.I(M.x),p:g.P(M.y)};
  if(!pend)pend=pt;else{DR.push({t:'trend',a:pend,b:pt});setMode(null);}}
 else{drag={x:M.x,s:V.start};CV.style.cursor='grabbing';}
 render();});
window.addEventListener('mouseup',()=>{drag=null;CV.style.cursor=mode?'crosshair':'grab';});
CV.addEventListener('wheel',e=>{e.preventDefault(); if(!D.length)return;
 const g=geom(),r=CV.getBoundingClientRect(),mx=e.clientX-r.left;
 const i0=g.I(mx),f=e.deltaY>0?1.12:1/1.12;
 V.count=Math.max(15,Math.min(NT()+40,V.count*f));
 V.start=i0-mx/(g.pw/V.count)+0.5;clampV();render();},{passive:false});
window.addEventListener('keydown',e=>{
 if(e.key=='Escape')setMode(null);
 if(e.key=='Delete'||e.key=='Backspace'){if(document.activeElement.tagName=='BODY')undo();}});
window.addEventListener('resize',render);
function place(){
 const g=geom(); if(!M||M.x>=g.pw)return;
 if(mode=='hline'){DR.push({t:'h',p:g.P(M.y)});setMode(null);}
 else if(mode=='trend'){const pt={i:g.I(M.x),p:g.P(M.y)};
  if(!pend)pend=pt;else{DR.push({t:'trend',a:pend,b:pt});setMode(null);}}
 render();}
let tch=null,pinch=null;
function tdist(ts){return Math.hypot(ts[0].clientX-ts[1].clientX,ts[0].clientY-ts[1].clientY);}
function tpos(t){const r=CV.getBoundingClientRect();return {x:t.clientX-r.left,y:t.clientY-r.top};}
CV.addEventListener('touchstart',e=>{e.preventDefault();if(!D.length)return;
 if(e.touches.length==2){tch=null;pinch={d:tdist(e.touches),c:V.count};return;}
 M=tpos(e.touches[0]);tch={x:M.x,y:M.y,s:V.start,moved:false};render();},{passive:false});
CV.addEventListener('touchmove',e=>{e.preventDefault();if(!D.length)return;
 if(e.touches.length==2&&pinch){const g=geom(),a=tpos(e.touches[0]),b=tpos(e.touches[1]),mx=(a.x+b.x)/2;
  const i0=g.I(mx);V.count=Math.max(15,Math.min(NT()+40,pinch.c*pinch.d/Math.max(1,tdist(e.touches))));
  V.start=i0-mx/(g.pw/V.count)+0.5;clampV();render();return;}
 if(!tch)return;M=tpos(e.touches[0]);
 if(Math.abs(M.x-tch.x)>6)tch.moved=true;
 if(!mode&&tch.moved){V.start=tch.s-(M.x-tch.x)/geom().step;clampV();}
 render();},{passive:false});
CV.addEventListener('touchend',e=>{e.preventDefault();
 if(e.touches.length<2)pinch=null;
 if(tch&&!tch.moved&&mode){place();}
 if(e.touches.length==0)tch=null;},{passive:false});
async function runRandomEntry(){
 const el=$('qreperm'); if(!el)return; el.textContent='Running matched random-entry control...';
 try{const p=researchQs().toString()+'&simulations=500';const {ok,j}=await api('/api/research-lab/random-entry',p);
 if(!ok){el.textContent=j.detail||'Random-entry control failed';return;}
 el.textContent='Matched random-entry: p='+Number(j.p_value_one_sided).toFixed(4)+' | observed='+Number(j.observed_mean_net_bar_return*100).toFixed(4)+'%/bar | random mean='+Number(j.random_mean*100).toFixed(4)+'%/bar | trades='+j.trade_count+' | '+j.scope;
 }catch(e){el.textContent='Random-entry error: '+e.message;}
}

async function runPermutation(){
 const el=$('qreperm'); if(!el)return; el.textContent='Running block permutation control...';
 try{const p=researchQs().toString()+'&permutations=500&block_bars=24';const {ok,j}=await api('/api/research-lab/permutation',p);
 if(!ok){el.textContent=j.detail||'Permutation failed';return;}
 el.textContent='Entry timing diagnostic: p='+Number(j.p_value_one_sided).toFixed(4)+' | observed='+Number(j.observed_mean_net_bar_return*100).toFixed(4)+'%/bar | null mean='+Number(j.null_mean*100).toFixed(4)+'%/bar | '+j.permutations+' permutations. '+j.scope;
 }catch(e){el.textContent='Permutation error: '+e.message;}
}

async function loadResearchLab(){
 const el=$('qrestatus'); if(!el)return; el.textContent='Loading research registry...';
 try{const {ok,j}=await api('/api/research-lab'); if(!ok){el.textContent=j.detail||'Research Lab unavailable';return;}
 const r=j.registry||{}, h=j.holdout||{};
 let html='<b>Hypotheses:</b> '+r.hypotheses+' | <b>Experiments:</b> '+r.experiments+' | <b>Completed:</b> '+r.completed_experiments+' | <b>Failed:</b> '+r.failed_experiments+
 '<br><b>Multiple-testing family size:</b> '+r.tests_counted+' | <b>Holdout:</b> '+esc(h.status||'UNKNOWN')+' | <b>Access count:</b> '+(h.access_count??0);
 if(h.message)html+='<br>'+esc(h.message);
 if(h.dataset_id)html+='<br><b>Sealed dataset:</b> '+esc(h.dataset_id)+' | '+esc(h.source)+' / '+esc(h.symbol)+' / '+esc(h.timeframe);
 if((r.recent_experiments||[]).length){html+='<table><thead><tr><th>Hypothesis</th><th>Market</th><th>Trades</th><th>Net Exp.</th><th>Raw p</th><th>FDR q</th></tr></thead><tbody>';
 r.recent_experiments.forEach(x=>{const m=x.metrics||{};html+='<tr><td>'+esc(x.name||x.hypothesis_id)+'</td><td>'+esc(x.symbol)+' / '+esc(x.timeframe)+'</td><td>'+esc(m.trades??'n/a')+'</td><td>'+esc(m.net_expectancy_pct??'n/a')+'</td><td>'+esc(x.p_value??'n/a')+'</td><td>'+esc(x.adjusted_p_value??'n/a')+'</td></tr>';});html+='</tbody></table>';}
 (j.limitations||[]).forEach(x=>html+='<div class=scenario-note>'+esc(x)+'</div>');el.innerHTML=html;
 }catch(e){el.textContent='Research Lab error: '+e.message;}
}

loadProviders();
loadResearchLab();
</script>"""


@app.get("/")
def index():
    return PAGE


# ---------------------------------------------------------------- HTTP server with LAN token auth
def _clean(o):
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if hasattr(o, "item") and not isinstance(o, (str, bytes)):
        try:
            o = o.item()
        except Exception:
            return str(o)
    if isinstance(o, float) and (math.isnan(o) or math.isinf(o)):
        return None
    return o


def _loopback(ip):
    """Recognize the complete IPv4/IPv6 loopback ranges, including IPv4-mapped IPv6."""
    try:
        addr = ipaddress.ip_address(str(ip).split("%", 1)[0])
        mapped = getattr(addr, "ipv4_mapped", None)
        return addr.is_loopback or bool(mapped and mapped.is_loopback)
    except ValueError:
        return False


def _eq(a, b):
    return secrets.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def _valid_access_token(token):
    """Cookie-safe shared token: URL-safe ASCII only, bounded length."""
    return isinstance(token, str) and re.fullmatch(r"[A-Za-z0-9._~-]{16,512}", token) is not None


def authorized(ip, params, cookie_header, token):
    """Loopback is always allowed. Any other client needs the token (query ?t= or cookie at=)."""
    if _loopback(ip):
        return True, False
    if not token:
        return False, False
    q = (params.get("t") or [""])[0]
    if q and _eq(q, token):
        return True, True          # valid, and cookie should be set
    for part in (cookie_header or "").split(";"):
        k, _, v = part.strip().partition("=")
        if k == "at" and v and _eq(v, token):
            return True, False
    return False, False


_FAILS, _FL = {}, threading.Lock()


def _blocked(ip):
    now = time.time()
    with _FL:
        n, t0 = _FAILS.get(ip, (0, now))
        if now - t0 > 600:
            _FAILS.pop(ip, None)
            return False
        return n >= 15


def _fail(ip):
    now = time.time()
    with _FL:
        if ip not in _FAILS and len(_FAILS) >= 4096:
            # Bound memory under distributed failed-login attempts.
            stale = [addr for addr, (_, first_seen) in _FAILS.items() if now - first_seen > 600]
            for addr in stale:
                _FAILS.pop(addr, None)
            if len(_FAILS) >= 4096:
                oldest = min(_FAILS, key=lambda addr: _FAILS[addr][1])
                _FAILS.pop(oldest, None)
        n, t0 = _FAILS.get(ip, (0, now))
        _FAILS[ip] = (n + 1, t0 if now - t0 <= 600 else now)


def _convert(f, params, body):
    kwargs = {}
    for name, p in inspect.signature(f).parameters.items():
        if name == "payload":
            kwargs[name] = body if isinstance(body, dict) else {}
        elif name in params:
            v = params[name][0]
            if p.annotation in (int, float):
                v = p.annotation(v)
            kwargs[name] = v
    return kwargs


CSP = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
       "connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


def make_handler(token, hosts):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def setup(self):
            super().setup()
            # Prevent slow clients from holding a request thread indefinitely.
            self.connection.settimeout(15)

        def log_message(self, *a):
            pass

        def _hdrs(self):
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
            self.send_header("Content-Security-Policy", CSP)

        def _cookie(self):
            self.send_header("Set-Cookie", f"at={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age=2592000")

        def _send(self, status, body, ctype="application/json; charset=utf-8", cookie=False):
            b = body if isinstance(body, bytes) else body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(b)))
            self._hdrs()
            if cookie:
                self._cookie()
            self.end_headers()
            self.wfile.write(b)

        def _handle(self, method):
            u = urllib.parse.urlparse(self.path)
            params = urllib.parse.parse_qs(u.query)
            host = (self.headers.get("Host") or "").lower()
            if host not in hosts:  # blocks DNS-rebinding; add your name to allowed_hosts if needed
                return self._send(400, json.dumps({"detail": "host not allowed - add it to allowed_hosts in config.json"}))
            ip = self.client_address[0]
            if _blocked(ip):
                return self._send(429, json.dumps({"detail": "too many failed attempts"}))
            ok, setc = authorized(ip, params, self.headers.get("Cookie", ""), token)
            if not ok:
                _fail(ip)
                return self._send(401, "Unauthorized - open the exact URL printed in the console (with ?t=...)",
                                  "text/plain; charset=utf-8")
            if setc and method == "GET" and u.path == "/":
                # drop the token from the URL (history, screenshots, shared links)
                self.send_response(302)
                self.send_header("Location", "/")
                self.send_header("Content-Length", "0")
                self._hdrs()
                self._cookie()
                self.end_headers()
                return
            f = app.routes.get((method, u.path))
            if not f:
                return self._send(404, json.dumps({"detail": "not found"}))
            body = None
            if method == "POST":
                ct = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                if ct != "application/json":   # forces a CORS preflight -> blocks cross-site form/text posts
                    return self._send(415, json.dumps({"detail": "Content-Type must be application/json"}))
                org = self.headers.get("Origin")
                if org and urllib.parse.urlparse(org).netloc.lower() != host:
                    return self._send(403, json.dumps({"detail": "cross-origin request refused"}))
                if self.headers.get("Transfer-Encoding"):
                    return self._send(400, json.dumps({"detail": "Transfer-Encoding is not supported"}))
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    return self._send(400, json.dumps({"detail": "bad Content-Length"}))
                if n < 0:
                    return self._send(400, json.dumps({"detail": "Content-Length cannot be negative"}))
                if n > 5_000_000:
                    return self._send(413, json.dumps({"detail": "payload too large"}))
                try:
                    body = json.loads(self.rfile.read(n) or b"{}")
                except Exception:
                    return self._send(400, json.dumps({"detail": "invalid JSON"}))
            try:
                out = f(**_convert(f, params, body))
                if isinstance(out, str):
                    return self._send(200, out, "text/html; charset=utf-8", setc)
                return self._send(200, json.dumps(_clean(out), allow_nan=False), cookie=setc)
            except HTTPException as e:
                return self._send(e.status_code, json.dumps({"detail": redact(e.detail)}))
            except (ValueError, TypeError) as e:
                return self._send(400, json.dumps({"detail": redact(f"{type(e).__name__}: {e}")}))
            except Exception as e:
                return self._send(500, json.dumps({"detail": redact(f"{type(e).__name__}: {e}")}))

        def do_GET(self):
            self._handle("GET")

        def do_POST(self):
            self._handle("POST")

    return H


def lan_ip():
    try:
        s_ = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s_.connect(("10.255.255.255", 1))
        ip = s_.getsockname()[0]
        s_.close()
        return ip
    except Exception:
        return ""


def serve():
    c = cfg()
    lan = "--lan" in sys.argv
    host = "0.0.0.0" if lan else (c.get("host") or os.getenv("HOST", "127.1.0.1"))
    port = int(c.get("port") or os.getenv("PORT", 8765))
    exposed = host not in ("127.1.0.1", "localhost", "::1")
    token = (c.get("access_token") or os.getenv("ACCESS_TOKEN", "")) if exposed else ""
    if exposed and not token:
        token = secrets.token_urlsafe(16)
        print("(no access_token in config.json - generated a temporary one for this run)")
    if exposed and not _valid_access_token(token):
        raise SystemExit("ACCESS_TOKEN/access_token must be 16-512 URL-safe ASCII characters (letters, digits, . _ ~ -).")
    hosts = {f"{h}:{port}" for h in ("127.1.0.1", "localhost", "[::1]")}
    ip = lan_ip() if exposed else ""
    if exposed:
        hosts.add(f"{ip}:{port}")
        hosts.add(f"{socket.gethostname().lower()}:{port}")
    for h in (c.get("allowed_hosts") or []):
        h = str(h).lower()
        hosts.add(h if ":" in h else f"{h}:{port}")
    try:
        if os.name == "posix" and os.path.exists(CFG_PATH):
            mode = os.stat(CFG_PATH).st_mode
            shared_write = bool(mode & (stat.S_IWGRP | stat.S_IWOTH))
            shared_read = bool(mode & (stat.S_IRGRP | stat.S_IROTH))
            if shared_write:
                print(f"WARNING: {CFG_PATH} is writable by other users; they could alter MCP executable settings. Run: chmod 600 config.json")
            elif _secret_values() and shared_read:
                print(f"WARNING: {CFG_PATH} holds keys and is readable by other users. Run: chmod 600 config.json")
    except OSError:
        pass
    class BoundedThreadingHTTPServer(ThreadingHTTPServer):
        """Cap concurrent request threads so LAN clients cannot exhaust the process."""
        request_queue_size = 32
        daemon_threads = True

        def __init__(self, server_address, handler, max_workers=16):
            if not isinstance(max_workers, int) or not 1 <= max_workers <= 128:
                raise ValueError("max_workers must be between 1 and 128")
            self.max_workers = max_workers
            self._worker_slots = threading.BoundedSemaphore(max_workers)
            super().__init__(server_address, handler)

        def process_request(self, request, client_address):
            if not self._worker_slots.acquire(timeout=1):
                self.shutdown_request(request)
                return
            try:
                super().process_request(request, client_address)
            except BaseException:
                self._worker_slots.release()
                raise

        def process_request_thread(self, request, client_address):
            try:
                super().process_request_thread(request, client_address)
            finally:
                self._worker_slots.release()

    hosts.add(f"127.0.0.1:{port}")
    srv = BoundedThreadingHTTPServer((host, port), make_handler(token, hosts), max_workers=16)
    print(f"Chart Analyzer listening on {host}:{port}")
    if exposed:
        print(f"\n  Open on your phone (same Wi-Fi):  http://{ip or '<phone-wifi-ip>'}:{port}/?t={token}\n")
        print("  Plain HTTP: anyone sniffing this Wi-Fi can read the token. Use a trusted network only.")
        print("  Anyone on this network WITHOUT the token gets 401. Do not share the link.")
    else:
        print(f"Open: http://127.1.0.1:{port}")
    if journal_items(conf()):
        threading.Thread(target=journal_loop, daemon=True).start()
        print("Journal job running (one prediction per new bar) -> agent_journal.db")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    serve()
