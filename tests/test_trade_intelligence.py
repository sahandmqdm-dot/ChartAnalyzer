from trade_intelligence import analyze_trade_intelligence


def test_trade_intelligence_groups_and_cost_drag():
    trades = [
        {"side":"long", "entry_regime":"trending", "entry_signal_score":0.7,
         "exit_reason":"signal", "bars_held":2, "gross_ret":0.02, "net_ret":0.018,
         "mfe":0.03, "mae":-0.005, "fee_drag_pct":0.1,
         "spread_slippage_drag_pct":0.05, "funding_drag_pct":0.0},
        {"side":"short", "entry_regime":"ranging", "entry_signal_score":0.1,
         "exit_reason":"reverse", "bars_held":8, "gross_ret":-0.01, "net_ret":-0.013,
         "mfe":0.004, "mae":-0.02},
    ]
    out = analyze_trade_intelligence(trades)
    assert out["version"] == "6.7.0"
    assert out["excursion_method"] == "open_to_open"
    assert "intrabar" in out["excursion_note"]
    assert out["overall"]["avg_cost_components_pct"]["fee_drag_pct"] is not None
    assert out["overall"]["trades"] == 2
    assert out["overall"]["avg_cost_drag_pct"] == 0.25
    assert out["by_side"]["long"]["trades"] == 1
    assert out["by_entry_regime"]["ranging"]["trades"] == 1
    assert out["failure_diagnostics"]["losers_with_mae_below_minus_1pct"] == 1
    assert out["failure_diagnostics"]["losers_with_positive_mfe_above_0_2pct"] == 1


def test_trade_intelligence_handles_empty_and_small_samples():
    assert analyze_trade_intelligence([])["status"] == "insufficient_data"
    out = analyze_trade_intelligence([{"net_ret": 0.01, "gross_ret": 0.02}])
    assert "Only 1 trades" in out["sample_warning"]
    assert out["guardrail"].startswith("No parameter")


def test_app_backtest_exposes_trade_intelligence_cost_components(monkeypatch):
    import numpy as np
    import pandas as pd
    import app

    n = 180
    prices = np.full(n, 100.0)
    frame = pd.DataFrame({"open": prices, "high": prices + 1,
                          "low": prices - 1, "close": prices,
                          "volume": np.ones(n)})
    signal = np.zeros(n)
    signal[2:10] = 1.0
    monkeypatch.setattr(app, "conf", lambda: {
        "verdict_threshold": 0.5,
        "backtest": {"market_type": "spot", "position_size": 1.0,
                     "spread_slippage_roundtrip": 0.002},
        "gate": {},
    })
    monkeypatch.setattr(app, "get_fee", lambda *_args: 0.002)
    monkeypatch.setattr(app, "compute", lambda *_args: {
        "S": pd.Series(signal),
        "regime": pd.Series(["trending"] * n),
        "gs": {},
    })

    out = app.backtest(frame, "1h", start=1, symbol="USDTIRR")
    assert out["version"] == APP_VERSION
    assert out["trade_edge_discovery"]["status"] == "RESEARCH_ONLY"
    assert out["execution_viability"]["status"] == "RESEARCH_ONLY"
    assert out["market_regime_intelligence"]["status"] == "RESEARCH_ONLY"
    assert "market_regime_edge_research" in out
    assert "market_regime_transition_edge_research" in out
    assert out["research_diagnostics"]["status"] == "descriptive_only"
    assert out["trade_intelligence"]["trades"] >= 1
    assert len(out["trade_forensics"]) == out["trades"]
    stress = out["execution_cost_stress"]["scenarios"]
    assert [x["cost_multiplier"] for x in stress] == [1.0, 1.5, 2.0, 3.0]
    assert stress[0]["strategy_pct"] == out["strategy_pct"]
    components = out["trade_intelligence"]["overall"]["avg_cost_components_pct"]
    assert components["fee_drag_pct"] > 0
    assert components["spread_slippage_drag_pct"] > 0


def test_cost_drag_uses_gross_account_return_for_leveraged_trades():
    out = analyze_trade_intelligence([
        {"side": "long", "gross_ret": 0.02, "gross_account_ret": 0.04, "net_ret": 0.018}
    ])
    assert out["overall"]["avg_cost_drag_pct"] == 2.2


def test_trade_intelligence_reconciles_gross_and_net_win_rates():
    out = analyze_trade_intelligence([
        {"gross_account_ret": 0.01, "net_ret": -0.001},
        {"gross_account_ret": 0.02, "net_ret": 0.005},
        {"gross_account_ret": -0.01, "net_ret": 0.0},
    ])
    assert out["overall"]["gross_win_rate_pct"] == 66.67
    assert out["overall"]["net_win_rate_pct"] == 33.33
    assert out["overall"]["net_breakeven"] == 1


def test_trade_forensics_separates_asset_and_account_returns():
    from trade_forensics import build_trade_forensics
    rows = build_trade_forensics([{
        "side": "long", "entry_px": 100, "exit_px": 101,
        "gross_ret": 0.01, "gross_account_ret": 0.02, "net_ret": 0.015,
        "mfe": 0.03, "mae": -0.01, "bars_held": 4, "exit_reason": "signal",
    }])
    assert rows[0]["gross_asset_return_pct"] == 1.0
    assert rows[0]["gross_account_return_pct"] == 2.0
    assert rows[0]["net_account_return_pct"] == 1.5
    assert rows[0]["cost_drag_account_pct"] == 0.5
    assert rows[0]["net_result"] == "win"


def test_default_full_window_backtest_uses_dynamic_indicator_warmup(monkeypatch):
    import numpy as np
    import pandas as pd
    import app

    n = 320
    close = np.linspace(100.0, 120.0, n)
    frame = pd.DataFrame({"open": close, "high": close + 1,
                          "low": close - 1, "close": close,
                          "volume": np.ones(n)})
    monkeypatch.setattr(app, "conf", lambda: app._merge(app.DEFAULTS, {
        "verdict_threshold": 0.5,
        "backtest": {"market_type": "spot", "position_size": 1.0,
                     "spread_slippage_roundtrip": 0.0},
    }))
    monkeypatch.setattr(app, "get_fee", lambda *_args: 0.0)
    monkeypatch.setattr(app, "compute", lambda *_args: {
        "S": pd.Series(np.zeros(n)), "regime": pd.Series(["ranging"] * n), "gs": {},
    })
    out = app.backtest(frame, "1h")
    assert out["required_indicator_warmup_bars"] == 202
    assert out["effective_start"] == 201
    assert out["warmup_bars_used"] == 202
    assert out["first_entry_fill_bar"] == 202
    assert out["warmup_shortfall_bars"] == 0
    assert out["warmup_adjustment_applied"] is True
    assert out["version"] == APP_VERSION


def test_full_window_first_fill_is_aligned_with_first_walk_forward_oos_fill(monkeypatch):
    import numpy as np
    import pandas as pd
    import app

    n = 420
    close = np.linspace(100.0, 130.0, n)
    frame = pd.DataFrame({"open": close, "high": close + 1.0,
                          "low": close - 1.0, "close": close,
                          "volume": np.ones(n)})
    monkeypatch.setattr(app, "conf", lambda: app._merge(app.DEFAULTS, {
        "verdict_threshold": 0.5,
        "walk_forward": {"n_splits": 2, "warmup_bars": 120, "gap_bars": 0},
        "backtest": {"market_type": "spot", "position_size": 1.0,
                     "spread_slippage_roundtrip": 0.0},
    }))
    monkeypatch.setattr(app, "get_fee", lambda *_args: 0.0)
    monkeypatch.setattr(app, "compute", lambda *_args: {
        "S": pd.Series(np.ones(n)), "regime": pd.Series(["trending"] * n), "gs": {},
    })

    full = app.backtest(frame, "1h", symbol="USDTIRR")
    wf = app.walk_forward_backtest(frame, "1h", symbol="USDTIRR")

    assert full["first_entry_fill_bar"] == 202
    assert full["trades_log"][0]["entry_bar"] == 202
    assert wf["folds"][0]["test_start_index"] == 202
    assert wf["folds"][0]["result"]["trades_log"][0]["entry_bar"] == 202
    assert wf["research_cohort_integrity"]["status"] == "PASS"
    from research_provenance import config_fingerprint
    live_config = app._research_execution_config("1h", "USDTIRR", None, app.conf())
    wf_config = app._research_execution_config("1h", "USDTIRR", wf, app.conf())
    assert config_fingerprint(live_config) == config_fingerprint(wf_config)


def test_short_history_exposes_indicator_warmup_shortfall(monkeypatch):
    import numpy as np
    import pandas as pd
    import app

    n = 180
    close = np.linspace(100.0, 110.0, n)
    frame = pd.DataFrame({"open": close, "high": close + 1,
                          "low": close - 1, "close": close,
                          "volume": np.ones(n)})
    monkeypatch.setattr(app, "conf", lambda: app._merge(app.DEFAULTS, {
        "verdict_threshold": 0.5,
        "backtest": {"market_type": "spot", "position_size": 1.0,
                     "spread_slippage_roundtrip": 0.0},
    }))
    monkeypatch.setattr(app, "get_fee", lambda *_args: 0.0)
    monkeypatch.setattr(app, "compute", lambda *_args: {
        "S": pd.Series(np.zeros(n)), "regime": pd.Series(["ranging"] * n), "gs": {},
    })
    out = app.backtest(frame, "1h")
    assert out["warmup_satisfied"] is False
    assert out["warmup_shortfall_bars"] == 81
    assert "warm-up is short" in out["warning"]
