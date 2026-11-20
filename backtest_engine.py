"""ChartAnalyzer V6.1 backtesting engine.

Causal next-open execution with exposure-aware transaction costs and net trade
statistics. Futures leverage is a synthetic notional multiplier; liquidation,
margin calls and historical funding schedules are intentionally not simulated.
"""
from __future__ import annotations
import math
from typing import Any, Dict, List, Optional
import numpy as np


def _as_float_array(x) -> np.ndarray:
    return np.asarray(x, dtype=float)


def _extract_time_axis(df):
    """Return a validated source-native time axis, or None when none is supplied.

    Numeric timestamps retain their provider-native unit. Datetime values are
    serialized as ISO-8601 strings. We never infer seconds vs milliseconds.
    """
    if "time" in getattr(df, "columns", ()):
        raw = np.asarray(df["time"])
        source = "time_column"
    else:
        index = getattr(df, "index", None)
        dtype = getattr(index, "dtype", None)
        if dtype is None or not np.issubdtype(dtype, np.datetime64):
            return None, None
        raw = np.asarray(index)
        source = "datetime_index"
    if raw.ndim != 1 or len(raw) != len(df) or len(raw) < 2:
        raise ValueError("time axis must be one-dimensional and match candle rows")
    try:
        numeric = np.asarray(raw, dtype=float)
        if not np.isfinite(numeric).all() or np.any(np.diff(numeric) <= 0):
            raise ValueError("numeric timestamps must be finite, unique, and strictly increasing")
        return raw, source
    except (TypeError, ValueError):
        try:
            parsed = np.asarray(raw, dtype="datetime64[ns]")
            ticks = parsed.astype("int64")
        except (TypeError, ValueError, OverflowError):
            raise ValueError("time column must contain numeric timestamps or datetimes") from None
        if np.isnat(parsed).any() or np.any(np.diff(ticks) <= 0):
            raise ValueError("datetime timestamps must be valid, unique, and strictly increasing")
        return parsed, source


def _serialize_time_value(value):
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, np.generic):
        return value.item()
    return value





def _buy_hold_benchmark(
    open_prices: np.ndarray, *, start: int, exposure: float,
    fee_per_side: float, half_spread: float, half_slippage: float, half_impact: float, funding_fee_per_bar: float,
) -> Dict[str, float]:
    """Calculate passive-long benchmarks over the same executable window.

    Returns account-level gross and net returns using the backtest engine's
    same notional exposure and multiplicative accounting. Gross excludes all
    execution costs and funding; net applies fees, spread/slippage, market
    impact and configured constant funding.
    This is a synthetic comparable benchmark, not an exchange-native index.
    """
    first = int(start) + 1
    if first >= len(open_prices) - 1:
        return {"asset_pct": 0.0, "gross_pct": 0.0, "net_pct": 0.0, "cost_drag_pct": 0.0}

    def simulate(*, include_fees: bool, include_spread: bool, include_funding: bool) -> float:
        equity = 1.0

        def charge(rate: float) -> None:
            nonlocal equity
            cost_rate = exposure * rate
            if cost_rate <= -1.0 or cost_rate >= 1.0:
                raise ValueError("cost rate times notional exposure is outside safe bounds")
            equity *= 1.0 - cost_rate

        if include_fees:
            charge(fee_per_side)
        if include_spread:
            charge(half_spread)
            charge(half_slippage)
            charge(half_impact)

        for idx in range(first, len(open_prices) - 1):
            if equity <= 0.0:
                break
            bar_return = float(open_prices[idx + 1] / open_prices[idx] - 1.0)
            factor = 1.0 + exposure * bar_return
            if factor <= 0.0:
                equity = 0.0
                break
            equity *= factor
            if include_funding and funding_fee_per_bar:
                charge(funding_fee_per_bar)

        if equity > 0.0:
            if include_fees:
                charge(fee_per_side)
            if include_spread:
                charge(half_spread)
                charge(half_slippage)
                charge(half_impact)
        return equity

    asset_return = float(open_prices[-1] / open_prices[first] - 1.0)
    gross_equity = simulate(include_fees=False, include_spread=False, include_funding=False)
    net_equity = simulate(include_fees=True, include_spread=True, include_funding=True)
    return {
        "asset_pct": asset_return * 100.0,
        "gross_pct": (gross_equity - 1.0) * 100.0,
        "net_pct": (net_equity - 1.0) * 100.0,
        "cost_drag_pct": (gross_equity - net_equity) * 100.0,
    }

def run_backtest(
    df, signal, threshold: float = 0.0, fee_per_side: float = 0.001,
    spread_slippage_rt: float = 0.0, spread_rt: Optional[float] = None,
    slippage_rt: Optional[float] = None, start: int = 120,
    allow_short: bool = True, position_size: float = 1.0,
    max_holding_bars: Optional[int] = None, market_type: str = "spot",
    leverage: float = 1.0, funding_fee_per_bar: float = 0.0,
    atr=None, stop_atr_mult: Optional[float] = None,
    target_atr_mult: Optional[float] = None, entry_allowed=None,
    execution_delay_bars: int = 0, market_impact_rt: float = 0.0,
) -> Dict[str, Any]:
    """Backtest signals observed at close[i], filled at open[i+1].

    `position_size` is a fraction of equity. Futures notional exposure equals
    `position_size * leverage`; spot exposure is capped at 1x and long-only.
    `spread_slippage_rt` is the round-trip cost on notional, split equally
    between entry and exit. Positive funding_fee_per_bar is a per-bar charge
    on futures notional; negative values represent a funding credit.
    """
    market_type = str(market_type).strip().lower()
    if market_type not in {"spot", "futures"}:
        raise ValueError("market_type must be 'spot' or 'futures'")
    if not 0 <= float(fee_per_side) < 0.5:
        raise ValueError("fee_per_side must be in [0, 0.5)")
    if spread_rt is None and slippage_rt is None:
        spread_rate=float(spread_slippage_rt); slippage_rate=0.0
    else:
        spread_rate=float(spread_rt or 0.0); slippage_rate=float(slippage_rt or 0.0)
    if not 0 <= spread_rate < 0.5 or not 0 <= slippage_rate < 0.5 or spread_rate+slippage_rate >= 0.5:
        raise ValueError("spread_rt and slippage_rt must be non-negative and sum to < 0.5")
    spread_slippage_rt=float(spread_rate+slippage_rate)
    if not 0 <= float(market_impact_rt) < 0.5:
        raise ValueError("market_impact_rt must be in [0, 0.5)")
    if isinstance(execution_delay_bars, bool) or int(execution_delay_bars) != execution_delay_bars or int(execution_delay_bars) < 0:
        raise ValueError("execution_delay_bars must be a non-negative integer")
    execution_delay_bars = int(execution_delay_bars)
    if not math.isfinite(float(threshold)):
        raise ValueError("threshold must be finite")
    if not 0 < float(position_size) <= 1.0:
        raise ValueError("position_size must be in (0, 1]")
    if not math.isfinite(float(leverage)) or float(leverage) < 1.0:
        raise ValueError("leverage must be finite and >= 1")
    if market_type == "spot" and float(leverage) != 1.0:
        raise ValueError("spot market does not support leverage")
    if not math.isfinite(float(funding_fee_per_bar)):
        raise ValueError("funding_fee_per_bar must be finite")
    if market_type == "spot" and float(funding_fee_per_bar) != 0.0:
        raise ValueError("funding fees apply only to futures")

    o = _as_float_array(df["open"])
    c = _as_float_array(df["close"])
    h = _as_float_array(df["high"]) if "high" in df else c.copy()
    l = _as_float_array(df["low"]) if "low" in df else c.copy()
    s = _as_float_array(signal)
    atr_values = None if atr is None else _as_float_array(atr)
    allowed = np.ones(len(o), dtype=bool) if entry_allowed is None else np.asarray(entry_allowed, dtype=bool)
    if len(allowed) != len(o):
        raise ValueError("entry_allowed length must match candles")
    if atr_values is not None and len(atr_values) != len(o):
        raise ValueError("atr length must match candles")
    for name, mult in (("stop_atr_mult", stop_atr_mult), ("target_atr_mult", target_atr_mult)):
        if mult is not None and (not math.isfinite(float(mult)) or float(mult) <= 0):
            raise ValueError(f"{name} must be finite and > 0")
    if (stop_atr_mult is not None or target_atr_mult is not None) and atr_values is None:
        raise ValueError("ATR values are required when ATR exits are enabled")
    if not (np.isfinite(h).all() and np.isfinite(l).all() and (h > 0).all() and (l > 0).all()):
        raise ValueError("backtest requires finite positive high/low prices")
    if np.any(h < l):
        raise ValueError("high must be >= low")
    n = len(o)
    if execution_delay_bars >= n - 2:
        raise ValueError("execution_delay_bars must be smaller than the executable sample")
    if len(s) != n:
        raise ValueError("signal length must match candles")
    if n < int(start) + 32:
        raise ValueError("not enough valid candles for next-open backtest")
    if not (np.isfinite(o).all() and np.isfinite(c).all() and (o > 0).all() and (c > 0).all()):
        raise ValueError("backtest requires finite positive OHLC prices")
    if not np.isfinite(s).all():
        raise ValueError("backtest signal contains non-finite values")

    # Spot is structurally long-only regardless of a caller's allow_short flag.
    shorts_enabled = bool(allow_short) if market_type == "futures" else False
    target = np.zeros(n, dtype=np.int8)
    thr = float(threshold)
    for i, value in enumerate(s):
        if value >= thr:
            target[i] = 1
        elif shorts_enabled and value <= -thr:
            target[i] = -1

    if execution_delay_bars:
        target = np.r_[np.zeros(execution_delay_bars, dtype=np.int8), target[:-execution_delay_bars]]
        if atr_values is not None:
            atr_values = np.r_[np.full(execution_delay_bars, np.nan), atr_values[:-execution_delay_bars]]

    size = float(position_size)
    exposure_notional = size * (float(leverage) if market_type == "futures" else 1.0)
    fee = float(fee_per_side)
    half_spread = spread_rate / 2.0
    half_slippage = slippage_rate / 2.0
    half_impact = float(market_impact_rt) / 2.0
    funding_rate = float(funding_fee_per_bar) if market_type == "futures" else 0.0

    equity = peak = 1.0
    max_dd = 0.0
    equity_curve: List[float] = []
    equity_bar_indices: List[int] = []
    trades: List[Dict[str, Any]] = []
    fees_paid = spread_paid = slippage_paid = impact_paid = funding_paid = 0.0
    turnover_sides = 0
    pos = 0
    entry_px = entry_bar = None
    entry_side = 0
    trade_factor = 1.0
    trade_gross_factor = 1.0
    trade_cost_factors = {"fees": 1.0, "spread": 1.0, "slippage": 1.0, "spread_slippage": 1.0, "market_impact": 1.0, "funding": 1.0}
    mfe = mae = 0.0
    intrabar_mfe = intrabar_mae = 0.0
    stop_px = target_px = None

    def charge_cost(rate: float, bucket: str) -> float:
        """Charge a cost on current notional exposure; return equity multiplier."""
        nonlocal equity, fees_paid, spread_paid, slippage_paid, impact_paid, funding_paid, trade_factor, trade_cost_factors
        cost_rate = exposure_notional * rate
        if cost_rate <= -1.0 or cost_rate >= 1.0:
            raise ValueError("cost rate times notional exposure is outside safe bounds")
        before = equity
        multiplier = 1.0 - cost_rate
        equity *= multiplier
        trade_factor *= multiplier
        paid = before - equity
        if bucket == "fee":
            fees_paid += paid
            trade_cost_factors["fees"] *= multiplier
        elif bucket == "spread":
            spread_paid += paid
            trade_cost_factors["spread"] *= multiplier
            trade_cost_factors["spread_slippage"] *= multiplier
        elif bucket == "slippage":
            slippage_paid += paid
            trade_cost_factors["slippage"] *= multiplier
            trade_cost_factors["spread_slippage"] *= multiplier
        elif bucket == "impact":
            impact_paid += paid
            trade_cost_factors["market_impact"] *= multiplier
        elif bucket == "funding":
            funding_paid += paid
            trade_cost_factors["funding"] *= multiplier
        return multiplier

    def close_trade(fill_px: float, exit_bar: int, reason: str):
        nonlocal pos, entry_px, entry_bar, entry_side, trade_factor, trade_gross_factor, trade_cost_factors, mfe, mae, intrabar_mfe, intrabar_mae, stop_px, target_px
        charge_cost(fee, "fee")
        charge_cost(half_spread, "spread")
        charge_cost(half_slippage, "slippage")
        charge_cost(half_impact, "impact")
        trades.append({
            "entry_bar": int(entry_bar), "exit_bar": int(exit_bar),
            "side": "long" if entry_side > 0 else "short",
            "entry_px": float(entry_px), "exit_px": float(fill_px),
            "bars_held": int(exit_bar - entry_bar),
            "gross_ret": float((fill_px / entry_px - 1.0) * entry_side),
            "gross_account_ret": float(trade_gross_factor - 1.0),
            "gross_account_ret_pct": float((trade_gross_factor - 1.0) * 100.0),
            "net_ret": float(trade_factor - 1.0),
            "net_ret_pct": float((trade_factor - 1.0) * 100.0),
            "notional_exposure": float(exposure_notional),
            "mfe": float(mfe), "mae": float(mae),
            "intrabar_mfe": float(intrabar_mfe), "intrabar_mae": float(intrabar_mae),
            "stop_px": float(stop_px) if stop_px is not None else None,
            "target_px": float(target_px) if target_px is not None else None, "exit_reason": reason,
            "fee_drag_pct": float((1.0 - trade_cost_factors["fees"]) * 100.0),
            "spread_drag_pct": float((1.0 - trade_cost_factors["spread"]) * 100.0),
            "slippage_drag_pct": float((1.0 - trade_cost_factors["slippage"]) * 100.0),
            "spread_slippage_drag_pct": float((1.0 - trade_cost_factors["spread_slippage"]) * 100.0),
            "market_impact_drag_pct": float((1.0 - trade_cost_factors["market_impact"]) * 100.0),
            "funding_drag_pct": float((1.0 - trade_cost_factors["funding"]) * 100.0),
        })
        pos = 0
        entry_px = entry_bar = None
        entry_side = 0
        trade_factor = 1.0
        trade_gross_factor = 1.0
        trade_cost_factors = {"fees": 1.0, "spread": 1.0, "slippage": 1.0, "spread_slippage": 1.0, "market_impact": 1.0, "funding": 1.0}
        mfe = mae = 0.0
        intrabar_mfe = intrabar_mae = 0.0
        stop_px = target_px = None

    for i in range(int(start), n - 2):
        desired = int(target[i])
        fill_px = float(o[i + 1])
        # Entry permission is evaluated at the actual fill candle's open time.
        # Existing positions are never force-closed by an entry-hours filter.
        if pos == 0 and desired != 0 and not bool(allowed[i + 1]):
            desired = 0
        if max_holding_bars is not None and pos and entry_bar is not None:
            if (i + 1 - entry_bar) >= int(max_holding_bars):
                desired = 0

        if desired != pos:
            if pos:
                reason = "reverse" if desired == -pos else "signal"
                if max_holding_bars is not None and entry_bar is not None and (i + 1 - entry_bar) >= int(max_holding_bars):
                    reason = "max_holding"
                close_trade(fill_px, i + 1, reason)
                turnover_sides += 1
            if desired:
                trade_factor = 1.0
                trade_gross_factor = 1.0
                trade_cost_factors = {"fees": 1.0, "spread": 1.0, "slippage": 1.0, "spread_slippage": 1.0, "market_impact": 1.0, "funding": 1.0}
                charge_cost(fee, "fee")
                charge_cost(half_spread, "spread")
                charge_cost(half_slippage, "slippage")
                charge_cost(half_impact, "impact")
                turnover_sides += 1
                entry_px = fill_px
                entry_bar = i + 1
                entry_side = desired
                pos = desired
                mfe = mae = 0.0
                intrabar_mfe = intrabar_mae = 0.0
                signal_atr = float(atr_values[i]) if atr_values is not None and np.isfinite(atr_values[i]) else None
                if signal_atr is not None and signal_atr > 0:
                    stop_px = (fill_px - signal_atr * float(stop_atr_mult) if pos > 0 else fill_px + signal_atr * float(stop_atr_mult)) if stop_atr_mult is not None else None
                    target_px = (fill_px + signal_atr * float(target_atr_mult) if pos > 0 else fill_px - signal_atr * float(target_atr_mult)) if target_atr_mult is not None else None
                else:
                    stop_px = target_px = None

        next_open = float(o[i + 2])
        if pos:
            bar_high, bar_low = float(h[i + 1]), float(l[i + 1])
            fav_high = (bar_high / entry_px - 1.0) * pos
            fav_low = (bar_low / entry_px - 1.0) * pos
            intrabar_mfe = max(intrabar_mfe, fav_high, fav_low)
            intrabar_mae = min(intrabar_mae, fav_high, fav_low)
            hit_reason = None
            hit_px = None
            if pos > 0:
                stop_hit = stop_px is not None and bar_low <= stop_px
                target_hit = target_px is not None and bar_high >= target_px
                # Conservative OHLC rule: if both levels are touched in one bar,
                # assume the stop happened first. A gap through stop fills at open.
                if stop_hit:
                    hit_reason, hit_px = "stop_loss", min(fill_px, stop_px)
                elif target_hit:
                    hit_reason, hit_px = "take_profit", max(fill_px, target_px)
            else:
                stop_hit = stop_px is not None and bar_high >= stop_px
                target_hit = target_px is not None and bar_low <= target_px
                if stop_hit:
                    hit_reason, hit_px = "stop_loss", max(fill_px, stop_px)
                elif target_hit:
                    hit_reason, hit_px = "take_profit", min(fill_px, target_px)
            if hit_reason:
                bar_ret = (hit_px / fill_px - 1.0) * pos
                multiplier = 1.0 + exposure_notional * bar_ret
                if multiplier <= 0:
                    equity = 0.0
                    trade_factor = 0.0
                    trade_gross_factor = 0.0
                else:
                    equity *= multiplier
                    trade_factor *= multiplier
                    trade_gross_factor *= multiplier
                close_trade(float(hit_px), i + 1, hit_reason)
                turnover_sides += 1
                peak = max(peak, equity)
                max_dd = min(max_dd, equity / peak - 1.0 if peak > 0 else -1.0)
                equity_curve.append(float(equity))
        equity_bar_indices.append(int(i + 2))
                equity_bar_indices.append(int(i + 2))
                continue
            bar_ret = (next_open / fill_px - 1.0) * pos
            multiplier = 1.0 + exposure_notional * bar_ret
            if multiplier <= 0:
                equity = 0.0
                trade_factor = 0.0
                trade_gross_factor = 0.0
                # A synthetic leveraged position is bankrupt; stop processing.
                trades.append({
                    "entry_bar": int(entry_bar), "exit_bar": int(i + 2),
                    "side": "long" if entry_side > 0 else "short",
                    "entry_px": float(entry_px), "exit_px": float(next_open),
                    "bars_held": int(i + 2 - entry_bar),
                    "gross_ret": float((next_open / entry_px - 1.0) * entry_side),
                    "gross_account_ret": -1.0, "gross_account_ret_pct": -100.0,
                    "net_ret": -1.0, "net_ret_pct": -100.0,
                    "notional_exposure": float(exposure_notional),
                    "mfe": float(mfe), "mae": float(mae),
                    "fee_drag_pct": float((1.0 - trade_cost_factors["fees"]) * 100.0),
                    "spread_drag_pct": float((1.0 - trade_cost_factors["spread"]) * 100.0),
                    "slippage_drag_pct": float((1.0 - trade_cost_factors["slippage"]) * 100.0),
                    "spread_slippage_drag_pct": float((1.0 - trade_cost_factors["spread_slippage"]) * 100.0),
                    "market_impact_drag_pct": float((1.0 - trade_cost_factors["market_impact"]) * 100.0),
                    "funding_drag_pct": float((1.0 - trade_cost_factors["funding"]) * 100.0),
                    "exit_reason": "bankruptcy_model_limit",
                })
                pos = 0
                entry_px = entry_bar = None
                entry_side = 0
                break
            equity *= multiplier
            trade_factor *= multiplier
            trade_gross_factor *= multiplier
            if funding_rate:
                charge_cost(funding_rate, "funding")
            fav = (next_open / entry_px - 1.0) * pos
            mfe = max(mfe, fav)
            mae = min(mae, fav)

        peak = max(peak, equity)
        max_dd = min(max_dd, equity / peak - 1.0 if peak > 0 else -1.0)
        equity_curve.append(float(equity))

    if pos and entry_px is not None:
        close_trade(float(o[-1]), n - 1, "end_of_data")
        turnover_sides += 1
        peak = max(peak, equity)
        max_dd = min(max_dd, equity / peak - 1.0 if peak > 0 else -1.0)
        if equity_curve:
            equity_curve[-1] = float(equity)

    time_axis, time_axis_source = _extract_time_axis(df)
    equity_timestamps = (
        [_serialize_time_value(time_axis[idx]) for idx in equity_bar_indices]
        if time_axis is not None else None
    )

    gross = np.asarray([t["gross_ret"] for t in trades], dtype=float)
    net = np.asarray([t["net_ret"] for t in trades], dtype=float)
    if len(trades):
        gw, gl = gross[gross > 0], gross[gross < 0]
        nw, nl = net[net > 0], net[net < 0]
        gross_pf = float(gw.sum() / -gl.sum()) if len(gl) and -gl.sum() > 1e-12 else (float("inf") if len(gw) else None)
        net_pf = float(nw.sum() / -nl.sum()) if len(nl) and -nl.sum() > 1e-12 else (float("inf") if len(nw) else None)
        avg_gross = float(gross.mean())
        avg_net = float(net.mean())
        gross_win_rate = float(np.mean(gross > 0))
        net_win_rate = float(np.mean(net > 0))
        avg_win = float(gw.mean()) if len(gw) else None
        avg_loss = float(gl.mean()) if len(gl) else None
        avg_hold = float(np.mean([t["bars_held"] for t in trades]))
        long_trades = sum(t["side"] == "long" for t in trades)
        short_trades = sum(t["side"] == "short" for t in trades)
    else:
        avg_gross = avg_net = gross_win_rate = net_win_rate = avg_win = avg_loss = avg_hold = None
        gross_pf = net_pf = None
        long_trades = short_trades = 0

    bars = max(0, n - 2 - int(start))
    exposure_pct = float(np.mean(np.abs(target[int(start):n-2]))) if bars else 0.0
    buyhold = _buy_hold_benchmark(
        o, start=int(start), exposure=exposure_notional, fee_per_side=fee,
        half_spread=half_spread, half_slippage=half_slippage, half_impact=half_impact, funding_fee_per_bar=funding_rate,
    )
    eq = np.asarray(equity_curve, dtype=float)
    if len(eq) > 10:
        eq_rets = np.diff(eq) / np.maximum(eq[:-1], 1e-12)
        eq_rets = eq_rets[np.isfinite(eq_rets)]
        sharpe = float(np.mean(eq_rets) / (np.std(eq_rets, ddof=1) + 1e-12) * math.sqrt(252 * 24)) if len(eq_rets) > 5 else None
    else:
        sharpe = None

    def rounded_pf(value):
        return round(value, 4) if isinstance(value, float) and math.isfinite(value) else value

    return {
        "bars": bars, "trades": int(len(trades)),
        "long_trades": int(long_trades), "short_trades": int(short_trades),
        "market_type": market_type, "allow_short": shorts_enabled,
        "execution_delay_bars": execution_delay_bars,
        "spread_roundtrip": float(spread_rate),
        "slippage_roundtrip": float(slippage_rate),
        "spread_slippage_roundtrip": float(spread_slippage_rt),
        "market_impact_roundtrip": float(market_impact_rt),
        "total_roundtrip_cost": round(2 * fee + float(spread_slippage_rt) + float(market_impact_rt), 8),
        "position_size": size, "leverage": float(leverage),
        "notional_exposure": exposure_notional,
        "exposure_pct": round(100 * exposure_pct, 2),
        "win_rate_pct": round(100 * gross_win_rate, 2) if gross_win_rate is not None else None,
        "net_win_rate_pct": round(100 * net_win_rate, 2) if net_win_rate is not None else None,
        "avg_trade_pct": round(100 * avg_gross, 4) if avg_gross is not None else None,
        "gross_expectancy_pct": round(100 * avg_gross, 4) if avg_gross is not None else None,
        "net_expectancy_pct": round(100 * avg_net, 4) if avg_net is not None else None,
        "expectancy_pct": round(100 * avg_net, 4) if avg_net is not None else None,
        "avg_win_pct": round(100 * avg_win, 4) if avg_win is not None else None,
        "avg_loss_pct": round(100 * avg_loss, 4) if avg_loss is not None else None,
        "gross_profit_factor": rounded_pf(gross_pf),
        "net_profit_factor": rounded_pf(net_pf),
        "profit_factor": rounded_pf(net_pf),
        "strategy_pct": round(100 * (equity - 1.0), 4),
        # buyhold_pct is the fee/spread/funding-adjusted passive-long benchmark
        # so comparisons against net strategy returns are like-for-like.
        "buyhold_pct": round(float(buyhold["net_pct"]), 4),
        "buyhold_net_pct": round(float(buyhold["net_pct"]), 4),
        "buyhold_gross_pct": round(float(buyhold["gross_pct"]), 4),
        "buyhold_asset_pct": round(float(buyhold["asset_pct"]), 4),
        "buyhold_cost_drag_pct": round(float(buyhold["cost_drag_pct"]), 4),
        "buyhold_benchmark_note": "Passive long over the exact backtest window, with matching notional exposure and configured entry/exit fees, spread/slippage, and constant funding where applicable. Not an exchange-native benchmark.",
        "max_dd_pct": round(100 * max_dd, 4),
        "avg_holding_bars": round(avg_hold, 2) if avg_hold is not None else None,
        "sharpe_approx": round(sharpe, 3) if sharpe is not None else None,
        "fee_per_side": fee,
        "fee_roundtrip": round(2 * fee + float(spread_slippage_rt) + float(market_impact_rt), 8),
        "spread_slippage_rt": float(spread_slippage_rt),
        "funding_fee_per_bar": funding_rate,
        "fee_sides_charged": int(turnover_sides),
        "fees_paid_equity_units": float(fees_paid),
        "spread_paid_equity_units": float(spread_paid),
        "slippage_paid_equity_units": float(slippage_paid),
        "spread_slippage_paid_equity_units": float(spread_paid + slippage_paid),
        "market_impact_paid_equity_units": float(impact_paid),
        "funding_paid_equity_units": float(funding_paid),
        "estimated_fees_pct_of_initial_equity": round(100 * fees_paid, 4),
        "execution": f"signal at close[t], extra delay {execution_delay_bars} bars, fill at next open, mark-to-market open-to-open",
        "accounting": "compounded equity; fees and spread/slippage charged on position notional; net trade returns include entry/exit costs",
        "funding_modelled": bool(market_type == "futures" and funding_rate != 0.0),
        "intrabar_atr_exits_enabled": bool(stop_atr_mult is not None or target_atr_mult is not None),
        "stop_atr_mult": stop_atr_mult, "target_atr_mult": target_atr_mult,
        "entry_hours_filter_applied": entry_allowed is not None,
        "liquidation_modelled": False,
        "trades_log": trades, "equity_curve": equity_curve,
        "equity_bar_indices": equity_bar_indices,
        "equity_timestamps": equity_timestamps,
        "equity_timestamp_source": time_axis_source,
        "equity_timeline_semantics": "equity marked at next open; bar index i+2 for each loop iteration",
        "warning": (
            "Next-open execution with OHLC-based ATR exits; exact intrabar path/order is unknown, and same-bar dual touches assume stop-first. "
            "Futures leverage is synthetic: margin calls, liquidation prices and historical funding schedules "
            "are not modelled unless a constant funding_fee_per_bar is supplied. Results are not proof of edge."
        ),
        "version": "6.7.0",
    }


def autopsy_summary(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not trades:
        return {"n": 0, "message": "no trades"}
    by_reason: Dict[str, int] = {}
    by_side: Dict[str, int] = {}
    mfe_list, mae_list = [], []
    for t in trades:
        reason = t.get("exit_reason", "?")
        side = t.get("side", "?")
        by_reason[reason] = by_reason.get(reason, 0) + 1
        by_side[side] = by_side.get(side, 0) + 1
        mfe_list.append(t.get("mfe", 0))
        mae_list.append(t.get("mae", 0))
    losers = [t for t in trades if t.get("net_ret", t.get("gross_ret", 0)) < 0]
    winners = [t for t in trades if t.get("net_ret", t.get("gross_ret", 0)) > 0]
    return {
        "n_trades": len(trades), "n_winners": len(winners), "n_losers": len(losers),
        "exit_reasons": by_reason, "sides": by_side,
        "avg_mfe": float(np.mean(mfe_list)) if mfe_list else None,
        "avg_mae": float(np.mean(mae_list)) if mae_list else None,
        "note": "Autopsy explains historical outcomes; it does not automatically rewrite strategy rules.",
    }
