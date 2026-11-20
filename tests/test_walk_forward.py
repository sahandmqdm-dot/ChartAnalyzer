from version import APP_VERSION\n\nimport sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from walk_forward import run_walk_forward


def market(n=400):
    # Deterministic upward drift with small oscillation.
    x = np.arange(n, dtype=float)
    close = 100.0 + 0.08 * x + 0.5 * np.sin(x / 5.0)
    return pd.DataFrame({
        "open": close, "high": close + 1.0, "low": close - 1.0,
        "close": close, "volume": np.ones(n),
    })


def test_walk_forward_creates_non_overlapping_oos_folds():
    df = market()
    signal = np.ones(len(df))
    out = run_walk_forward(df, signal, n_splits=4, warmup_bars=120,
                           engine_kwargs={"threshold": 0.5, "fee_per_side": 0.0})
    assert out["n_folds"] == 4
    assert out["oos_bars"] == 280
    spans = [(f["test_start_index"], f["test_end_index_exclusive"]) for f in out["folds"]]
    assert all(spans[i][1] <= spans[i + 1][0] for i in range(len(spans) - 1))
    assert out["strategy_frozen"] is True
    assert out["parameter_optimization"] is False
    assert out["version"] == APP_VERSION
    assert out["exit_policy_research"]["status"] in {"RESEARCH_ONLY", "NO_COUNTERFACTUAL_SAMPLES"}
    assert out["trade_failure_intelligence"]["status"] == "descriptive_and_counterfactual"
    assert out["trade_failure_intelligence"]["trades_analyzed"] == out["trades"]
    assert out["buyhold_net_pct"] is not None
    assert out["buyhold_gross_pct"] is not None
    for fold in out["folds"]:
        assert fold["buyhold_pct"] == fold["buyhold_net_pct"]
        assert fold["buyhold_net_pct"] <= fold["buyhold_gross_pct"]
        assert fold["excess_vs_buyhold_pct"] == pytest.approx(
            fold["strategy_pct"] - fold["buyhold_net_pct"], abs=1e-4
        )


def test_first_oos_fill_uses_signal_before_window_and_oos_open():
    df = market(220)
    signal = np.zeros(len(df))
    signal[119] = 1.0
    signal[120:] = 0.0
    out = run_walk_forward(df, signal, n_splits=2, warmup_bars=120,
                           engine_kwargs={"threshold": 0.5, "fee_per_side": 0.0})
    first = out["folds"][0]
    assert first["trades"] == 1
    assert first["result"]["trades_log"][0]["entry_bar"] == 120


def test_walk_forward_applies_gap_between_folds():
    df = market(400)
    out = run_walk_forward(df, np.ones(len(df)), n_splits=3, warmup_bars=100,
                           gap_bars=10, engine_kwargs={"fee_per_side": 0.0})
    for left, right in zip(out["folds"], out["folds"][1:]):
        assert right["test_start_index"] - left["test_end_index_exclusive"] == 10


def test_walk_forward_rejects_short_fold_and_bad_signal():
    df = market(180)
    with pytest.raises(ValueError, match="at least 32 candles"):
        run_walk_forward(df, np.ones(len(df)), n_splits=2, warmup_bars=120)
    bad = np.ones(len(df)); bad[5] = np.nan
    with pytest.raises(ValueError, match="non-finite"):
        run_walk_forward(df, bad, n_splits=2, warmup_bars=50)


def test_fixed_test_bars_uses_only_complete_folds():
    df = market(400)
    out = run_walk_forward(df, np.ones(len(df)), warmup_bars=100,
                           test_bars=60, gap_bars=10,
                           engine_kwargs={"fee_per_side": 0.0})
    assert out["n_folds"] == 4
    assert out["oos_bars"] == 240


def test_app_wrapper_routes_market_costs_and_walk_forward_settings(monkeypatch):
    import app
    df = market(400)
    signal = np.ones(len(df))
    cfg = {
        "verdict_threshold": 0.5,
        "backtest": {
            "market_type": "futures", "allow_short_futures": True,
            "position_size": 0.5, "futures_leverage": 2.0,
            "futures_fee_roundtrip": 0.001,
            "spread_slippage_roundtrip": 0.002,
            "funding_fee_per_bar": 0.0,
        },
        "walk_forward": {"n_splits": 3, "warmup_bars": 100, "gap_bars": 5},
        "gate": {"spread_slippage_roundtrip": 0.001},
    }
    monkeypatch.setattr(app, "conf", lambda: cfg)
    monkeypatch.setattr(app, "compute", lambda _df, _tf: pd.DataFrame({"S": signal}))
    monkeypatch.setattr(app, "get_fee", lambda _cfg, _symbol, _market: 0.001)
    out = app.walk_forward_backtest(df, "1h", symbol="BTCUSDT")
    assert out["n_folds"] == 3
    assert out["folds"][0]["result"]["market_type"] == "futures"
    assert out["folds"][0]["result"]["leverage"] == 2.0
    assert out["folds"][0]["result"]["spread_slippage_rt"] == pytest.approx(0.002)


def test_walk_forward_attaches_entry_context_from_pre_fill_signal_bar():
    df = market(400)
    signal = np.ones(len(df))
    regimes = np.asarray([f"regime-{i}" for i in range(len(df))], dtype=object)
    out = run_walk_forward(
        df, signal, n_splits=3, warmup_bars=100,
        engine_kwargs={"threshold": 0.5, "fee_per_side": 0.0},
        entry_regime=regimes,
    )
    first_fold = out["folds"][0]
    first_trade = first_fold["result"]["trades_log"][0]
    expected_signal_index = first_fold["test_start_index"] - 1
    assert first_trade["entry_regime"] == regimes[expected_signal_index]
    assert first_trade["entry_signal_score"] == pytest.approx(signal[expected_signal_index])
    assert first_trade["entry_signal_band"] == "bullish"
    assert out["trade_intelligence"]["by_entry_regime"]
    assert "unknown" not in out["trade_intelligence"]["by_entry_regime"]


def test_walk_forward_rejects_entry_regime_length_mismatch():
    df = market(220)
    with pytest.raises(ValueError, match="entry_regime length"):
        run_walk_forward(df, np.ones(len(df)), n_splits=2,
                         warmup_bars=80, entry_regime=["trending"])


def test_walk_forward_slices_atr_and_applies_entry_hours_mask():
    df = market(400)
    signal = np.ones(len(df))
    allowed = np.ones(len(df), dtype=bool)
    allowed[120:140] = False
    atr = np.ones(len(df)) * 0.5
    out = run_walk_forward(df, signal, n_splits=3, warmup_bars=100,
                           entry_allowed=allowed,
                           engine_kwargs={"threshold": .5, "fee_per_side": 0.0,
                                          "atr": atr, "stop_atr_mult": 1.5,
                                          "target_atr_mult": 2.0})
    assert out["entry_hours_filter_applied"] is True
    assert out["entry_hours_blocked_bars"] == 20
    assert all(f["result"]["intrabar_atr_exits_enabled"] for f in out["folds"])


def test_walk_forward_preserves_causal_market_regime_labels_on_oos_trades():
    df = market(360)
    signal = np.ones(len(df))
    regimes = np.where(np.arange(len(df)) < 240, "trending", "volatile")
    transitions = np.zeros(len(df), dtype=bool)
    transitions[240] = True
    out = run_walk_forward(
        df, signal, n_splits=3, warmup_bars=80,
        entry_market_regime=regimes,
        entry_regime_transition=transitions,
        engine_kwargs={"threshold": 0.5, "fee_per_side": 0.001},
    )
    assert out["market_regime_intelligence"]["status"] == "RESEARCH_ONLY"
    assert "market_regime_edge_research" in out
    assert "market_regime_transition_edge_research" in out
    for trade in out["trades_log"]:
        assert trade["entry_market_regime"] in {"trending", "volatile"}
        assert isinstance(trade["entry_regime_transition"], bool)


def test_walk_forward_rejects_misaligned_market_regime_array():
    df = market(220)
    with pytest.raises(ValueError, match="entry_market_regime length"):
        run_walk_forward(df, np.ones(len(df)), n_splits=2, warmup_bars=60,
                         entry_market_regime=np.array(["trending"] * 10))


def test_cost_stress_mode_returns_core_oos_metrics_without_diagnostic_payloads():
    df=market(400); signal=np.ones(len(df))
    out=run_walk_forward(df,signal,n_splits=3,warmup_bars=100,
        engine_kwargs={'threshold':.5,'fee_per_side':.002,'spread_rt':.001,'slippage_rt':.001,
                       'market_impact_rt':.0005},include_diagnostics=False)
    assert out['diagnostics_skipped'] is True
    assert out['trades']>0
    assert out['net_expectancy_pct'] is not None
    assert 'exit_path_intelligence' not in out

def test_walk_forward_preserves_fold_timestamps_and_excludes_embargo_from_timeline():
    df = market(400)
    df["time"] = np.arange(len(df), dtype=np.int64) * 3600
    out = run_walk_forward(
        df, np.ones(len(df)), n_splits=3, warmup_bars=100, gap_bars=10,
        engine_kwargs={"threshold": 0.5, "fee_per_side": 0.0},
        include_diagnostics=False,
    )
    timeline = out["equity_timeline"]
    metadata = out["equity_timeline_metadata"]
    assert metadata["schema"] == "chartanalyzer_oos_equity_timeline_v1"
    assert len(metadata["excluded_embargo_intervals"]) == 2
    assert [x["excluded_bars"] for x in metadata["excluded_embargo_intervals"]] == [10, 10]
    assert all(row["timestamp"] is not None for row in timeline)
    assert all(row["included_in_cross_fold_ess"] is False for row in timeline)
    assert out["folds"][0]["portfolio_time_series_ess"]["status"] == "DIAGNOSTIC_ONLY"
    excluded = {
        bar
        for gap in metadata["excluded_embargo_intervals"]
        for bar in range(gap["start_bar_index"], gap["end_bar_index_exclusive"])
    }
    assert not excluded.intersection({row["bar_index"] for row in timeline})
    assert metadata["cross_fold_ess"]["status"] == "NOT_COMPUTED"


def test_walk_forward_marks_time_series_ess_unavailable_without_real_timestamps():
    df = market(400)
    out = run_walk_forward(
        df, np.ones(len(df)), n_splits=3, warmup_bars=100,
        engine_kwargs={"threshold": 0.5, "fee_per_side": 0.0},
        include_diagnostics=False,
    )
    assert all(row["timestamp"] is None for row in out["equity_timeline"])
    assert all(fold["portfolio_time_series_ess"]["status"] == "UNAVAILABLE"
               for fold in out["folds"])

