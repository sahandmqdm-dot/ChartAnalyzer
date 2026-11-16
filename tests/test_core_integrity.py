import sys
from pathlib import Path
import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import app


def candles(times, close=None):
    close = close or [100.0] * len(times)
    return pd.DataFrame({
        "time": times, "open": close, "high": [x + 2 for x in close],
        "low": [x - 2 for x in close], "close": close, "volume": [10.0] * len(times)
    })


def test_closed_candles_excludes_forming_bar_and_keeps_closed():
    df = candles([0, 3600, 7200])
    out = app.closed_candles(df, "1h", now=7200 + 3599)
    assert out.time.tolist() == [0, 3600]
    out2 = app.closed_candles(df, "1h", now=10800)
    assert out2.time.tolist() == [0, 3600, 7200]


def test_closed_candles_rejects_unknown_timeframe():
    with pytest.raises(ValueError):
        app.closed_candles(candles([0]), "2h", now=99999)


@pytest.mark.parametrize("symbol, expected", [
    ("BTCUSDT", 0.001), ("USDTIRR", 0.004), ("BTCIRT", 0.004),
    ("BTC/USDT:USDT", 0.001), ("UNKNOWN", 0.004),
])
def test_fee_is_round_trip_and_market_aware(symbol, expected):
    assert app.get_fee(app.conf(), symbol) == pytest.approx(expected)


def test_ohlc_integrity_filter_rejects_impossible_rows(tmp_path, monkeypatch):
    C = app.conf()
    p = tmp_path / "bad.csv"
    rows = [[i * 3600, 100, 105, 95, 102, 10] for i in range(60)]
    rows += [[60 * 3600, 100, 99, 95, 102, 10],  # high below close
             [61 * 3600, -1, 105, 95, 102, 10]]  # non-positive open
    pd.DataFrame(rows, columns=["time","open","high","low","close","volume"]).to_csv(p, index=False)
    C["csv_dir"] = str(tmp_path)
    monkeypatch.setattr(app, "conf", lambda: C)
    out = app.load("csv", "unused", "1h", 100, csv_path="bad.csv")
    assert len(out) == 60
    assert out.iloc[0].close == 102


def test_restricted_hours_only_apply_to_configured_short_timeframes():
    C = app.conf()
    C["restricted_hours"] = {
        "enabled": True, "start": "23:30", "end": "11:30",
        "tz_offset_min": 0, "tf_max_min": 60
    }
    df = candles([0, 12*3600, 23*3600+45*60])
    mask = app.filter_restricted_bars(df, C, "1h")
    assert mask.tolist() == [False, True, False]
    assert app.filter_restricted_bars(df, C, "4h").all()


def test_projection_parameter_timeframe_override():
    pc = {"warm": {"default": 100, "1h": 80}, "lambda": 0.94}
    assert app.proj_param(pc, "warm", "1h", 120) == 80
    assert app.proj_param(pc, "warm", "4h", 120) == 100
    assert app.proj_param(pc, "lambda", "4h", 0.9) == pytest.approx(0.94)


def test_indicator_engine_produces_finite_scores_on_synthetic_market():
    rng = np.random.default_rng(7)
    n = 650
    close = 100 * np.exp(np.cumsum(rng.normal(0.0003, 0.008, n)))
    df = pd.DataFrame({
        "time": np.arange(n) * 3600.0, "open": close * (1-rng.uniform(0, .002, n)),
        "high": close * 1.01, "low": close * .99, "close": close,
        "volume": rng.uniform(10, 100, n)
    })
    result = app.compute(df, "1h")
    assert "S" in result
    values = np.asarray(result["S"], dtype=float)
    assert len(values) == n
    assert np.isfinite(values[-100:]).any()
    assert np.nanmax(np.abs(values)) <= 1.000001



def test_forecast_smoke_returns_probability_cone_and_gate():
    rng = np.random.default_rng(42)
    n = 900
    close = 100 * np.exp(np.cumsum(rng.normal(0.0001, 0.01, n)))
    df = pd.DataFrame({
        "time": np.arange(n) * 3600.0,
        "open": close * (1 + rng.normal(0, 0.001, n)),
        "high": close * 1.012, "low": close * 0.988, "close": close,
        "volume": rng.uniform(100, 1000, n)
    })
    import tempfile
    from pathlib import Path
    old_path = app.VALIDATION_EVIDENCE_PATH
    app.VALIDATION_EVIDENCE_PATH = str(Path(tempfile.gettempdir()) / "chartanalyzer-test-no-evidence.json")
    try:
        Path(app.VALIDATION_EVIDENCE_PATH).unlink(missing_ok=True)
        result = app.forecast(df, "1h", 12, 100, app.conf(), symbol="BTCUSDT")
    finally:
        app.VALIDATION_EVIDENCE_PATH = old_path
    assert set(result) >= {"cone", "summary"}
    assert isinstance(result["cone"], dict)
    assert "gate" in result["summary"]
    assert result["summary"]["gate"]["state"] == "NO_TRADE"
    assert result["summary"]["gate"]["deployment_validation"]["status"] == "MISSING"


def test_gate_rejects_trade_without_edge_even_when_plan_is_attractive():
    import agent_core
    out = agent_core.decide(1.09, 100, 95, 115, .55, .25, fee_rt=.001)
    assert out["state"] == "NO_TRADE"
    assert any("no demonstrated edge" in x for x in out["reasons"])


def test_gate_reports_gross_and_net_rr_and_execution_friction():
    import agent_core
    out = agent_core.decide(2.5, 100, 95, 115, .65, .20,
                            fee_rt=.001, spread_slippage_rt=.002)
    assert out["ev"]["rr_gross"] == pytest.approx(3.0)
    assert out["ev"]["rr_net"] < out["ev"]["rr_gross"]
    assert out["ev"]["round_trip_cost"] == pytest.approx(.003)


def test_gate_rejects_invalid_plan_probabilities():
    import agent_core
    out = agent_core.decide(3.0, 100, 95, 110, .8, .4)
    assert out["state"] == "NO_TRADE"
    assert any("probabilities" in x for x in out["reasons"])


def test_gate_can_require_minimum_edge_sample_count():
    import agent_core
    out = agent_core.decide(3.0, 100, 95, 115, .8, .1, edge_n=12,
                            min_probability_samples=30)
    assert out["state"] == "NO_TRADE"
    assert any("sample is too small" in x for x in out["reasons"])


def test_indicator_mtf_is_explicitly_distinguished_from_structural_trend(monkeypatch):
    C = app.conf()
    C["tf_list"] = ["4h"]
    C["tf_weights"] = {"4h": 1.0}
    monkeypatch.setattr(app, "conf", lambda: C)
    monkeypatch.setattr(app, "load_cached", lambda *a, **k: object())
    monkeypatch.setattr(app, "compute", lambda *a, **k: object())
    monkeypatch.setattr(app, "summarize", lambda *a, **k: {
        "verdict": "Bullish", "score": 0.388, "regime": "trending",
        "groups": {"trend": {"score": 0.4}, "momentum": {"score": -0.2}}
    })
    result = app.api_mtf()
    assert result["method"] == "indicator_group_composite"
    assert "not the structural trend score" in result["interpretation"]
    assert result["rows"][0]["score"] == pytest.approx(0.388)


def test_default_execution_friction_is_nonzero_and_paths_increased():
    defaults = app.DEFAULTS
    assert defaults["gate"]["spread_slippage_roundtrip"] == pytest.approx(0.001)
    assert defaults["projection"]["paths"] == 2000


def test_mfi_zero_negative_flow_is_defined_not_missing():
    n = 80
    close = np.linspace(100, 140, n)
    df = pd.DataFrame({
        "time": np.arange(n) * 3600.0,
        "open": close - 0.1, "high": close + 1.0,
        "low": close - 1.0, "close": close,
        "volume": np.ones(n) * 10.0,
    })
    X = app.Ctx(df, app.params_for("1h"))
    _, mfi, _ = app.i_mfi(X, app.params_for("1h")["mfi"])
    assert mfi.iloc[-1] == pytest.approx(100.0)
    assert np.isfinite(mfi.iloc[-1])


def test_summary_explains_indicator_value_semantics_and_integrity():
    rng = np.random.default_rng(7)
    n = 650
    close = 100 * np.exp(np.cumsum(rng.normal(0.0003, 0.008, n)))
    df = pd.DataFrame({
        "time": np.arange(n) * 3600.0,
        "open": close * (1 - rng.uniform(0, 0.002, n)),
        "high": close * 1.01, "low": close * 0.99,
        "close": close, "volume": rng.uniform(10, 100, n),
    })
    summary = app.summarize(app.compute(df, "1h"), df, app.conf())
    by_key = {item["key"]: item for item in summary["indicators"]}
    assert by_key["ema"]["metric_kind"] == "price_distance"
    assert "minus" in by_key["ema"]["meaning"]
    assert by_key["supertrend"]["metric_kind"] == "price_level"
    assert summary["indicator_integrity"]["state"] == "PASS"


def test_forecast_blocks_impossible_ohlcv_before_any_scenario_computation():
    import time
    n = 900
    close = np.linspace(100.0, 120.0, n)
    df = pd.DataFrame({
        "time": time.time() - (n - np.arange(n)) * 3600,
        "open": close, "high": close - 1.0, "low": close + 1.0,
        "close": close, "volume": np.ones(n),
    })
    with pytest.raises(ValueError, match="Forecast blocked by OHLCV integrity gate"):
        app.forecast(df, "1h", 12, 100, app.conf(), symbol="BTCUSDT", source="csv")
