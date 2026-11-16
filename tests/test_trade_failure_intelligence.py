import numpy as np
import pandas as pd

from trade_failure_intelligence import analyze_trade_failures


def test_signal_exit_path_excludes_exit_candle_and_captures_entry_context():
    df = pd.DataFrame(
        {
            "open": [100, 100, 101, 99, 98],
            "high": [101, 103, 102, 100, 99],
            "low": [99, 98, 99, 97, 96],
            "close": [100, 101, 100, 98, 97],
        }
    )
    trades = [{
        "global_entry_bar": 1, "global_exit_bar": 3,
        "global_entry_signal_bar": 0, "entry_px": 100, "exit_px": 99,
        "side": "long", "net_ret": -0.02, "gross_account_ret": -0.01,
        "notional_exposure": 1.0, "bars_held": 2, "exit_reason": "signal",
        "entry_signal_score": 0.4, "entry_regime": "trending",
        "stop_px": 95, "target_px": 110,
    }]
    out = analyze_trade_failures(
        trades, df, signal=np.array([0.4, 0.3, 0.1, -0.2, -0.3]),
        entry_components={"rsi": np.array([0.2, 0.1, -0.1, -0.2, -0.3])},
        fee_per_side=0.001, spread_slippage_rt=0.001,
    )
    row = out["trades"][0]
    # Signal exit fills at bar 3 open, so bar 3 high/low must not contaminate path.
    assert row["intrabar_mfe_pct"] == 3.0
    assert row["intrabar_mae_pct"] == -2.0
    assert row["pre_exit_signal_score"] == 0.1
    assert row["component_score_changes"]["rsi"] == -0.3
    assert "signal_exit_review" in row["failure_tags"]
    assert row["counterfactual_early_time_exits"]
    assert row["counterfactual_early_time_exits"][0]["exit_reason"] == "time_exit"
    assert out["status"] == "descriptive_and_counterfactual"


def test_atr_exit_includes_exit_candle_and_marks_dual_touch_ambiguous():
    df = pd.DataFrame({
        "open": [100, 100, 100],
        "high": [101, 106, 101],
        "low": [99, 94, 99],
        "close": [100, 100, 100],
    })
    trades = [{
        "global_entry_bar": 1, "global_exit_bar": 1,
        "entry_px": 100, "exit_px": 95, "side": "long",
        "net_ret": -0.06, "notional_exposure": 1,
        "bars_held": 0, "exit_reason": "stop_loss",
        "stop_px": 95, "target_px": 105,
    }]
    out = analyze_trade_failures(trades, df)
    row = out["trades"][0]
    assert row["intrabar_mfe_pct"] == 6.0
    assert row["intrabar_mae_pct"] == -6.0
    assert row["same_bar_stop_target_ambiguous"] is True
    assert row["first_atr_level_event"] == "ambiguous_both_stop_first"


def test_invalid_trade_indices_are_reported_not_crashed():
    df = pd.DataFrame({"open": [1, 1], "high": [1, 1], "low": [1, 1], "close": [1, 1]})
    out = analyze_trade_failures([{"entry_bar": 9, "exit_bar": 10, "entry_px": 1}], df)
    assert out["trades_invalid"] == 1
    assert out["trades"][0]["audit_status"] == "invalid_trade_indices_or_price"


def test_trade_failure_timestamps_use_epoch_time_column():
    from datetime import datetime, timezone
    base = 1_700_000_000
    df = pd.DataFrame({
        "time": [base, base + 3600, base + 7200],
        "open": [100, 100, 99], "high": [101, 103, 100],
        "low": [99, 98, 97], "close": [100, 101, 98],
    })
    out = analyze_trade_failures([{
        "global_entry_bar": 1, "global_exit_bar": 2, "entry_px": 100,
        "exit_px": 99, "side": "long", "net_ret": -0.02,
        "exit_reason": "signal", "notional_exposure": 1.0,
    }], df)
    expected = datetime.fromtimestamp(base + 3600, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    assert out["trades"][0]["entry_timestamp"] == expected
