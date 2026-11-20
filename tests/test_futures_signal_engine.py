from datetime import UTC, datetime, timedelta

import pytest

from futures_signal_engine import analyze_futures_scenarios


NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


def bars(count=100, drift=0.2):
    out = []
    price = 100.0
    for index in range(count):
        opened = price
        price += drift
        out.append({
            "time": NOW - timedelta(minutes=1) - timedelta(hours=count-1-index),
            "open": opened,
            "high": max(opened, price) + 0.3,
            "low": min(opened, price) - 0.3,
            "close": price,
            "volume": 10.0,
        })
    out[-1]["time"] = NOW - timedelta(minutes=1)
    return out


def run(data=None, **kwargs):
    return analyze_futures_scenarios(
        data if data is not None else bars(),
        instrument_id="BTCUSDT",
        timeframe_seconds=3600,
        as_of=NOW,
        **kwargs,
    )


def test_returns_both_scenarios_but_never_authorizes_execution():
    result = run()
    assert set(result["scenarios"]) == {"long", "short"}
    assert result["decision"] == "NO_TRADE"
    assert result["actionable"] is False
    assert result["deployment_authorized"] is False
    assert result["scenarios"]["long"]["status"] == "WATCH_ONLY"


def test_strong_rising_series_produces_long_candidate():
    result = run(bars(drift=0.5))
    assert result["candidate"] == "LONG"
    assert result["scenarios"]["long"]["stop_loss_reference"] < result["last_price"]
    assert result["scenarios"]["long"]["target_reference"] > result["last_price"]


def test_strong_falling_series_produces_short_candidate():
    result = run(bars(drift=-0.5))
    assert result["candidate"] == "SHORT"
    assert result["scenarios"]["short"]["target_reference"] < result["last_price"]
    assert result["scenarios"]["short"]["stop_loss_reference"] > result["last_price"]


def test_gap_blocks_instead_of_silently_filling():
    data = bars()
    data[50]["time"] = data[49]["time"] + timedelta(hours=3)
    for index in range(50, len(data)):
        data[index]["time"] += timedelta(hours=2)
    with pytest.raises(ValueError, match="gap"):
        run(data)


def test_duplicate_or_reversed_timestamps_are_rejected():
    data = bars()
    data[60]["time"] = data[59]["time"]
    with pytest.raises(ValueError, match="strictly increasing"):
        run(data)


def test_stale_latest_candle_is_rejected():
    data = bars()
    data[-1]["time"] = NOW - timedelta(hours=2)
    with pytest.raises(ValueError, match="stale"):
        run(data)


def test_future_candle_is_rejected():
    data = bars()
    data[-1]["time"] = NOW + timedelta(minutes=1)
    with pytest.raises(ValueError, match="after as_of"):
        run(data)


def test_stale_derivatives_are_reported_and_never_authorize():
    result = run(
        funding_rate=0.0005,
        funding_observed_at=NOW - timedelta(hours=1),
        open_interest_change_pct=12,
        open_interest_observed_at=NOW - timedelta(hours=1),
    )
    assert result["derivatives_context"]["valid"] is False
    assert result["decision"] == "NO_TRADE"
    assert result["actionable"] is False


@pytest.mark.parametrize("kwargs", [
    {"instrument_id": "../BTC"},
    {"timeframe_seconds": 0},
    {"min_abs_score": 1.5},
    {"stop_atr_multiple": 0},
])
def test_invalid_configuration_is_rejected(kwargs):
    args = dict(instrument_id="BTCUSDT", timeframe_seconds=3600, as_of=NOW)
    args.update(kwargs)
    with pytest.raises(ValueError):
        analyze_futures_scenarios(bars(), **args)


def test_funding_without_timestamp_is_marked_invalid():
    result = run(funding_rate=0.001)
    assert result["derivatives_context"]["valid"] is False
    assert any("timestamp is missing" in x for x in result["derivatives_context"]["warnings"])
