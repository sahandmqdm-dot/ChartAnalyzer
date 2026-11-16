from research_diagnostics import build_research_diagnostics
from trade_research import analyze_trade_quality


def test_cost_hurdle_and_negative_sample_findings():
    trades = [
        {"net_ret": -0.0063, "gross_ret": -0.0013, "gross_account_ret": -0.0013,
         "mfe": 0.0020, "mae": -0.0013, "bars_held": 2,
         "exit_reason": "signal", "entry_regime": "ranging"},
        {"net_ret": -0.0010, "gross_ret": 0.0040, "gross_account_ret": 0.0040,
         "mfe": 0.0030, "mae": -0.0023, "bars_held": 15,
         "exit_reason": "signal", "entry_regime": "trending"},
    ]
    result = build_research_diagnostics(
        trades, {"strategy_pct": -12.4, "buyhold_net_pct": 1.8, "net_profit_factor": 0.21},
        market_type="spot", position_size=1, leverage=1,
    )
    codes = {x["code"] for x in result["findings"]}
    assert "SMALL_SAMPLE" in codes
    assert "SIGNAL_ONLY_EXITS" in codes
    assert "FAVORABLE_EXCURSION_BELOW_COST_HURDLE" in codes
    assert result["summary"]["net_losers"] == 2


def test_futures_does_not_compare_asset_mfe_with_account_costs():
    result = build_research_diagnostics(
        [{"net_ret": -0.01, "gross_account_ret": -0.005, "gross_ret": -0.005,
          "mfe": 0.001, "mae": -0.01, "bars_held": 2}],
        market_type="futures", position_size=1, leverage=3,
    )
    codes = {x["code"] for x in result["findings"]}
    assert "MFE_COST_BASIS_NOT_COMPARABLE" in codes
    assert "FAVORABLE_EXCURSION_BELOW_COST_HURDLE" not in codes


def test_exit_capture_excludes_losing_trades_from_capture_ratio():
    result = analyze_trade_quality([
        {"gross_ret": 0.01, "net_ret": 0.005, "mfe": 0.02},
        {"gross_ret": -0.01, "net_ret": -0.015, "mfe": 0.01},
    ])
    exit_capture = result["exit_capture"]
    assert exit_capture["mean_gross_mfe_capture_pct"] == 50.0
    assert exit_capture["winning_trades_with_positive_mfe"] == 1
    assert exit_capture["losing_trades_with_positive_mfe"] == 1
    assert exit_capture["records"][1]["gross_mfe_capture_pct"] is None
