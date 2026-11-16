import numpy as np
import pandas as pd
from trade_research import analyze_trade_quality, analyze_rejected_bullish_candidates


def test_entry_exit_research_reports_cohorts_and_capture():
    out = analyze_trade_quality([
        {"entry_signal_score": .65, "gross_ret": .02, "net_ret": .018, "mfe": .03,
         "bars_held": 4, "exit_reason": "signal"},
        {"entry_signal_score": .1, "gross_ret": -.01, "net_ret": -.012, "mfe": .005,
         "bars_held": 2, "exit_reason": "signal"},
    ])
    assert out["status"] == "descriptive_only"
    assert out["entry_score_cohorts"]["strong_positive"]["samples"] == 1
    assert out["exit_capture"]["trades_with_mfe"] == 2
    assert out["exit_capture"]["mean_gross_mfe_capture_pct"] is not None


def test_rejected_signal_research_uses_positive_subthreshold_scores():
    n = 20
    df = pd.DataFrame({"open": np.arange(n, dtype=float) + 100,
                       "close": np.arange(n, dtype=float) + 101})
    signal = np.zeros(n)
    signal[[1, 3, 5, 7]] = [.1, .2, .3, .8]
    out = analyze_rejected_bullish_candidates(df, signal, .5, horizons=(1, 3))
    assert out["candidates"] == 3
    assert out["horizons"]["1"]["samples"] == 3
    assert "Not evidence" in out["warning"]


def test_rejected_signal_research_handles_missing_columns():
    out = analyze_rejected_bullish_candidates(pd.DataFrame(), [], .2)
    assert out["status"] == "unavailable"
