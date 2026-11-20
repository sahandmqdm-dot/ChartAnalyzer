"""Research-only directional scenarios for linear perpetual futures.

This module produces transparent watch candidates, never trade authorization.
Funding and open interest are context only: neither is treated as a standalone
positioning signal. Inputs must be completed, chronological OHLCV bars.
"""
from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any, Iterable, Mapping

ENGINE_VERSION = "FuturesDirectionalIntelligence 1.0.0"


def _finite(name: str, value: Any, *, positive: bool = False) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be finite numeric")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be finite numeric") from exc
    if not math.isfinite(result) or (positive and result <= 0):
        raise ValueError(f"{name} must be finite" + (" and positive" if positive else ""))
    return result


def _ema(values: list[float], period: int) -> float:
    alpha = 2.0 / (period + 1.0)
    result = sum(values[:period]) / period
    for value in values[period:]:
        result = alpha * value + (1.0 - alpha) * result
    return result


def _rsi(values: list[float], period: int = 14) -> float:
    changes = [values[i] - values[i - 1] for i in range(1, len(values))]
    gains = [max(x, 0.0) for x in changes]
    losses = [max(-x, 0.0) for x in changes]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for gain, loss in zip(gains[period:], losses[period:]):
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def _atr(rows: list[dict[str, float]], period: int = 14) -> float:
    true_ranges = []
    for i, row in enumerate(rows):
        previous_close = rows[i - 1]["close"] if i else row["close"]
        true_ranges.append(max(
            row["high"] - row["low"],
            abs(row["high"] - previous_close),
            abs(row["low"] - previous_close),
        ))
    value = sum(true_ranges[:period]) / period
    for tr in true_ranges[period:]:
        value = (value * (period - 1) + tr) / period
    return value


def _utc(value: Any, name: str) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"{name} must be an ISO-8601 timestamp") from exc
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def analyze_futures_scenarios(
    bars: Iterable[Mapping[str, Any]],
    *,
    instrument_id: str,
    timeframe_seconds: int,
    as_of: datetime,
    funding_rate: float | None = None,
    funding_observed_at: datetime | None = None,
    open_interest_change_pct: float | None = None,
    open_interest_observed_at: datetime | None = None,
    max_derivatives_age_seconds: float = 900.0,
    min_abs_score: float = 0.35,
    stop_atr_multiple: float = 1.5,
    target_r_multiple: float = 2.0,
) -> dict[str, Any]:
    """Return LONG/SHORT watch scenarios from closed OHLCV plus optional context.

    A candidate is a directional research hypothesis, not an entry instruction.
    No probability, profitability, OOS approval, leverage or executable quantity
    is inferred by this engine.
    """
    symbol = str(instrument_id).strip().upper()
    if not symbol or not symbol.isalnum():
        raise ValueError("instrument_id must be a canonical alphanumeric symbol")
    if isinstance(timeframe_seconds, bool) or not 60 <= int(timeframe_seconds) <= 604800:
        raise ValueError("timeframe_seconds must be between 60 and 604800")
    now = _utc(as_of, "as_of")
    max_age = _finite("max_derivatives_age_seconds", max_derivatives_age_seconds, positive=True)
    threshold = _finite("min_abs_score", min_abs_score, positive=True)
    if threshold > 1:
        raise ValueError("min_abs_score must be <= 1")
    stop_mult = _finite("stop_atr_multiple", stop_atr_multiple, positive=True)
    target_r = _finite("target_r_multiple", target_r_multiple, positive=True)

    raw = list(bars)
    if len(raw) < 60:
        raise ValueError("at least 60 completed candles are required")
    rows: list[dict[str, float]] = []
    timestamps: list[datetime] = []
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise ValueError(f"bar {index} must be a mapping")
        stamp = _utc(item.get("close_time", item.get("time")), f"bar[{index}].close_time")
        row = {key: _finite(f"bar[{index}].{key}", item.get(key), positive=key != "volume")
               for key in ("open", "high", "low", "close", "volume")}
        if row["volume"] < 0:
            raise ValueError(f"bar {index} volume cannot be negative")
        if row["high"] < max(row["open"], row["close"], row["low"]) or row["low"] > min(row["open"], row["close"]):
            raise ValueError(f"bar {index} has invalid OHLC geometry")
        if timestamps and stamp <= timestamps[-1]:
            raise ValueError("candle close timestamps must be strictly increasing")
        if timestamps and (stamp - timestamps[-1]).total_seconds() > timeframe_seconds * 1.5:
            raise ValueError("candle history contains a gap; scenarios are blocked")
        timestamps.append(stamp)
        rows.append(row)
    if timestamps[-1] > now:
        raise ValueError("latest candle close is after as_of")
    if (now - timestamps[-1]).total_seconds() > timeframe_seconds * 1.5:
        raise ValueError("latest completed candle is stale")

    closes = [row["close"] for row in rows]
    last = closes[-1]
    ema_fast, ema_slow = _ema(closes[-100:], 20), _ema(closes[-200:], 50)
    rsi = _rsi(closes[-100:])
    atr = _atr(rows[-100:])
    if atr <= 0:
        raise ValueError("ATR must be positive")

    trend = 1.0 if ema_fast > ema_slow else -1.0 if ema_fast < ema_slow else 0.0
    momentum = max(-1.0, min(1.0, ((last / closes[-6]) - 1.0) * 100.0 / 2.0))
    rsi_component = max(-1.0, min(1.0, (rsi - 50.0) / 20.0))
    raw_score = 0.50 * trend + 0.30 * momentum + 0.20 * rsi_component
    score = max(-1.0, min(1.0, raw_score))

    derivatives = {"funding": None, "open_interest_change_pct": None, "valid": True, "warnings": []}
    for label, observed in (("funding", funding_observed_at), ("open_interest", open_interest_observed_at)):
        if observed is not None:
            age = (now - _utc(observed, f"{label}_observed_at")).total_seconds()
            if age < 0:
                raise ValueError(f"{label} observation timestamp is in the future")
            if age > max_age:
                derivatives["valid"] = False
                derivatives["warnings"].append(f"{label} observation is stale")
    if funding_rate is not None:
        derivatives["funding"] = _finite("funding_rate", funding_rate)
        if funding_observed_at is None:
            derivatives["valid"] = False
            derivatives["warnings"].append("funding timestamp is missing")
    if open_interest_change_pct is not None:
        derivatives["open_interest_change_pct"] = _finite("open_interest_change_pct", open_interest_change_pct)
        if open_interest_observed_at is None:
            derivatives["valid"] = False
            derivatives["warnings"].append("open-interest timestamp is missing")
        derivatives["warnings"].append("open-interest change is context only and does not identify long/short positioning")

    candidate_side = "LONG" if score >= threshold else "SHORT" if score <= -threshold else "NO_DIRECTIONAL_SETUP"
    def scenario(side: str) -> dict[str, Any]:
        long_side = side == "LONG"
        stop = last - atr * stop_mult if long_side else last + atr * stop_mult
        target = last + atr * stop_mult * target_r if long_side else last - atr * stop_mult * target_r
        return {
            "side": side,
            "entry_reference": last,
            "stop_loss_reference": stop,
            "target_reference": target,
            "risk_reward_before_costs": target_r,
            "invalidation": f"completed candle close {'below' if long_side else 'above'} {stop:.12g}",
            "status": "WATCH_ONLY",
            "actionable": False,
        }

    blockers = [
        "This engine has no validated futures-specific out-of-sample edge.",
        "No execution-grade order-book, trade-flow, funding-cost horizon or market-impact model is included.",
        "OOS deployment authorization is not evaluated or granted by this engine.",
    ]
    if not derivatives["valid"]:
        blockers.extend(derivatives["warnings"])
    return {
        "schema": "futures_scenarios_v1",
        "engine": ENGINE_VERSION,
        "instrument_id": symbol,
        "market_type": "binance_usdm_linear_perpetual",
        "timeframe_seconds": int(timeframe_seconds),
        "as_of": now.isoformat(),
        "last_candle_close": timestamps[-1].isoformat(),
        "last_price": last,
        "indicators": {"ema20": ema_fast, "ema50": ema_slow, "rsi14": rsi, "atr14": atr},
        "directional_score": score,
        "candidate": candidate_side,
        "candidate_strength": abs(score),
        "scenarios": {"long": scenario("LONG"), "short": scenario("SHORT")},
        "derivatives_context": derivatives,
        "decision": "NO_TRADE",
        "actionable": False,
        "deployment_authorized": False,
        "blockers": blockers,
        "limitations": [
            "Directional score is a transparent heuristic, not a calibrated probability.",
            "Funding and open interest are not used to infer trader positioning.",
            "Entry is the last completed candle close; live bid/ask and execution latency are not modeled.",
            "Intrabar stop/target ordering, funding accrual, liquidation tiers and market impact are not modeled.",
            "Both scenarios are counterfactual watch levels; only a future validated workflow may authorize a trade.",
        ],
    }


__all__ = ["analyze_futures_scenarios", "ENGINE_VERSION"]
