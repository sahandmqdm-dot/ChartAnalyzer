import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from strategy_autopsy import analyze_trades


def sample_trades():
    return [
        {"side": "long", "net_ret": 0.02, "gross_ret": 0.025, "bars_held": 4,
         "mfe": 0.03, "mae": -0.005, "exit_reason": "signal", "entry_regime": "trending",
         "entry_signal_band": "bullish", "net_ret_pct": 2.0},
        {"side": "short", "net_ret": -0.03, "gross_ret": -0.025, "bars_held": 20,
         "mfe": 0.004, "mae": -0.02, "exit_reason": "reverse", "entry_regime": "ranging",
         "entry_signal_band": "bearish", "net_ret_pct": -3.0},
    ]


def test_autopsy_groups_by_side_regime_exit_and_signal_without_changing_trades():
    trades = sample_trades()
    result = analyze_trades(trades, strategy_pct=-1.1, buyhold_pct=2.0, max_dd_pct=-5.0)
    assert result["n_trades"] == 2
    assert result["by_side"]["long"]["trades"] == 1
    assert result["by_regime"]["trending"]["trades"] == 1
    assert result["by_exit_reason"]["reverse"]["trades"] == 1
    assert result["by_signal_band"]["bearish"]["trades"] == 1
    assert result["verdict"] == "MIXED_OR_POSITIVE_SAMPLE"
    assert any("below" in warning for warning in result["warnings"])
    assert trades[0]["net_ret"] == 0.02


def test_autopsy_flags_negative_sample_and_buyhold_underperformance():
    trades = sample_trades()
    trades[0]["net_ret"] = -0.01
    result = analyze_trades(trades, strategy_pct=-5.0, buyhold_pct=1.0)
    assert result["verdict"] == "NEGATIVE_SAMPLE"
    assert any("underperformed" in warning for warning in result["warnings"])


def test_autopsy_handles_empty_trade_log():
    result = analyze_trades([])
    assert result["n_trades"] == 0
    assert result["verdict"] == "INSUFFICIENT_DATA"


def test_app_backtest_attaches_entry_context_from_pre_fill_signal_bar(monkeypatch):
    import numpy as np
    import pandas as pd
    import app
    import backtest_engine

    n = 220
    close = np.linspace(100, 120, n)
    df = pd.DataFrame({"open": close, "high": close * 1.01, "low": close * .99,
                       "close": close, "volume": np.ones(n)})
    score = pd.Series(np.linspace(-0.5, 0.5, n))
    regime = pd.Series(["ranging"] * 129 + ["trending"] * (n - 129))
    groups = {"trend": pd.Series(np.linspace(0, 1, n))}
    monkeypatch.setattr(app, "conf", lambda: {
        "verdict_threshold": 0.2,
        "backtest": {"market_type": "spot", "position_size": 1.0,
                     "spread_slippage_roundtrip": 0.0},
        "gate": {}, "fee_by_market": {"tether": 0.001},
    })
    monkeypatch.setattr(app, "compute", lambda _df, _tf: {
        "S": score, "regime": regime, "gs": groups,
    })
    fake = {
        "trades": 1, "trades_log": [{"entry_bar": 130, "exit_bar": 135,
            "side": "long", "bars_held": 5, "net_ret": 0.01,
            "gross_ret": 0.012, "net_ret_pct": 1.0, "mfe": 0.02,
            "mae": -0.003, "exit_reason": "signal"}],
        "strategy_pct": 1.0, "buyhold_pct": 0.5, "max_dd_pct": -1.0,
    }
    monkeypatch.setattr(backtest_engine, "run_backtest", lambda *a, **k: fake)
    out = app.backtest(df, "1h", symbol="BTCUSDT")
    trade = out["trades_log"][0]
    assert trade["entry_signal_score"] == round(float(score.iloc[129]), 6)
    assert trade["entry_regime"] == "trending"
    assert trade["entry_trend_score"] == round(float(groups["trend"].iloc[129]), 6)
    assert out["autopsy"]["n_trades"] == 1


def test_autopsy_exposes_gross_and_net_win_rates_separately():
    trades = [
        {"net_ret": -0.001, "gross_account_ret": 0.001},
        {"net_ret": 0.002, "gross_account_ret": 0.003},
        {"net_ret": 0.0, "gross_account_ret": -0.001},
    ]
    out = analyze_trades(trades)
    assert out["overall"]["gross_win_rate_pct"] == 66.67
    assert out["overall"]["net_win_rate_pct"] == 33.33
    assert out["overall"]["win_rate_pct"] == out["overall"]["net_win_rate_pct"]
    assert out["overall"]["net_winners"] == 1
    assert out["overall"]["net_losers"] == 1
    assert out["overall"]["net_breakeven"] == 1
