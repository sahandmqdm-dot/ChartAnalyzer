import numpy as np
import pandas as pd
from signal_forensics import build_signal_forensics


def test_signal_forensics_reports_intrabar_excursions_and_component_votes():
    df = pd.DataFrame({"open": [100, 100, 100, 100],
                       "high": [101, 103, 102, 101],
                       "low": [99, 98, 99, 99],
                       "close": [100, 101, 100, 100]})
    trades = [{"global_entry_bar": 1, "global_exit_bar": 3,
               "entry_px": 100, "side": "long", "net_ret": -.01,
               "entry_signal_score": .21, "entry_regime": "ranging",
               "entry_component_group_trend": .4,
               "entry_component_indicator_rsi": -.5,
               "bars_held": 2, "exit_reason": "signal",
               "entry_fill_allowed": True}]
    out = build_signal_forensics(trades, df, threshold=.2)
    row = out["trades"][0]
    assert row["intrabar_mfe_pct"] == 3.0
    assert row["intrabar_mae_pct"] == -2.0
    assert row["entry_conviction"] == "near_threshold"
    assert row["component_positive_votes"] == 1
    assert row["component_negative_votes"] == 1
    assert out["losing_trades_with_positive_intrabar_mfe"] == 1


def test_signal_forensics_includes_intrabar_atr_exit_candle():
    df = pd.DataFrame({
        "open": [100, 100, 100],
        "high": [101, 106, 101],
        "low": [99, 94, 99],
        "close": [100, 100, 100],
    })
    trades = [{
        "global_entry_bar": 1, "global_exit_bar": 1,
        "entry_px": 100, "side": "long", "net_ret": -0.05,
        "entry_signal_score": 0.4, "bars_held": 0, "exit_reason": "stop_loss",
    }]
    out = build_signal_forensics(trades, df, threshold=0.2)
    assert out["trades"][0]["intrabar_mfe_pct"] == 6.0
    assert out["trades"][0]["intrabar_mae_pct"] == -6.0
