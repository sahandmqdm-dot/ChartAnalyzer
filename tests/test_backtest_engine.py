import sys
from pathlib import Path
import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backtest_engine import run_backtest


def market(n=180):
    px = np.full(n, 100.0)
    return pd.DataFrame({"open": px, "high": px + 1, "low": px - 1,
                         "close": px, "volume": np.ones(n)})


def test_entry_and_exit_charge_exactly_two_sides_and_trade_return_is_compounded():
    df = market()
    signal = np.zeros(len(df))
    signal[120] = 1.0  # enter at open[121]
    signal[121] = 0.0  # exit at open[122]
    fee = 0.001
    out = run_backtest(df, signal, threshold=0.5, fee_per_side=fee)
    expected = (1 - fee) ** 2 - 1
    assert out["trades"] == 1
    assert out["fee_sides_charged"] == 2
    # V6: strategy_pct includes fees; avg_trade_pct is gross return of the trade
    assert out["strategy_pct"] == pytest.approx(expected * 100, abs=1e-3)
    assert out["fee_roundtrip"] == pytest.approx(2 * fee)


def test_signal_is_executed_at_next_open_not_signal_candle_close():
    df = market()
    df.loc[121, "open"] = 110.0
    df.loc[122, "open"] = 121.0
    signal = np.zeros(len(df))
    signal[120] = 1.0
    signal[121] = 0.0
    out = run_backtest(df, signal, threshold=0.5, fee_per_side=0.0)
    assert out["avg_trade_pct"] == pytest.approx(10.0)


def test_open_position_is_liquidated_and_charged_exit_fee():
    df = market()
    signal = np.zeros(len(df))
    signal[120:] = 1.0
    out = run_backtest(df, signal, threshold=0.5, fee_per_side=0.002)
    assert out["trades"] == 1
    assert out["fee_sides_charged"] == 2
    assert out["strategy_pct"] == pytest.approx(((1 - .002) ** 2 - 1) * 100, abs=1e-3)


def test_rejects_non_finite_signal_and_invalid_prices():
    df = market()
    signal = np.zeros(len(df))
    signal[130] = np.nan
    with pytest.raises(ValueError, match="non-finite"):
        run_backtest(df, signal, .5, .001)
    signal[130] = 0
    df.loc[140, "open"] = 0
    with pytest.raises(ValueError, match="positive"):
        run_backtest(df, signal, .5, .001)


def test_indicator_score_prefix_is_invariant_to_future_candles():
    import app
    rng = np.random.default_rng(2026)
    n = 500
    close = 100 * np.exp(np.cumsum(rng.normal(0, .006, n)))
    df = pd.DataFrame({
        "time": np.arange(n) * 3600.0,
        "open": close * (1 + rng.normal(0, .001, n)),
        "high": close * 1.01,
        "low": close * .99,
        "close": close,
        "volume": rng.uniform(10, 100, n),
    })
    cut = 350
    full = app.compute(df, "1h")["S"].to_numpy()
    prefix = app.compute(df.iloc[:cut].reset_index(drop=True), "1h")["S"].to_numpy()
    np.testing.assert_allclose(full[:cut], prefix, rtol=1e-10, atol=1e-10)


def test_data_integrity_flags_gaps_and_impossible_ohlc():
    from data_integrity import audit_ohlcv
    df = market(70)
    df["time"] = np.arange(70) * 3600.0
    df.loc[35:, "time"] += 3600.0
    df.loc[10, "high"] = 90.0
    out = audit_ohlcv(df, 3600)
    assert out["valid"] is False
    assert out["state"] == "BLOCKED"
    assert out["gap_count"] == 1
    assert any("impossible OHLC" in e for e in out["errors"])


def test_data_integrity_passes_clean_ohlcv():
    from data_integrity import audit_ohlcv
    df = market(70)
    df["time"] = np.arange(70) * 3600.0
    out = audit_ohlcv(df, 3600)
    assert out["state"] == "PASS"
    assert out["valid"] is True
    assert out["gap_count"] == 0


def test_integrity_report_preserves_ingestion_warnings_after_normalization():
    from data_integrity import audit_ohlcv
    df = market(70)
    df["time"] = np.arange(70) * 3600.0
    df.attrs["ingest_audit"] = {
        "input_rows": 72, "invalid_ohlc_rows": 1, "non_finite_rows": 0,
        "negative_volume_rows": 0, "duplicate_timestamps": 1,
        "out_of_order_timestamps": 0,
    }
    out = audit_ohlcv(df, 3600, check_stale=False)
    assert out["state"] == "BLOCKED"
    assert any("invalid rows" in e for e in out["errors"])
    assert any("duplicate timestamp" in w for w in out["warnings"])


def test_trade_log_separates_fee_spread_and_funding_drag():
    import numpy as np
    import pandas as pd
    from backtest_engine import run_backtest
    prices = list(range(100, 136))
    df = pd.DataFrame({"open": prices, "close": prices})
    signal = np.zeros(len(df)); signal[1:8] = 1
    result = run_backtest(df, signal, start=1, fee_per_side=0.001,
                          spread_slippage_rt=0.002, market_type="futures", leverage=2,
                          funding_fee_per_bar=0.0001)
    trade = result["trades_log"][0]
    assert trade["fee_drag_pct"] > 0
    assert trade["spread_slippage_drag_pct"] > 0
    assert trade["funding_drag_pct"] > 0


def test_buy_hold_benchmark_is_cost_adjusted_and_preserves_gross_baseline():
    df = market()
    signal = np.zeros(len(df))  # No strategy position; passive benchmark is still evaluated.
    out = run_backtest(df, signal, threshold=0.5, start=120,
                       fee_per_side=0.001, spread_slippage_rt=0.002)
    expected_net = (1 - 0.001) ** 4 - 1  # fee + half-spread at entry and exit
    assert out["trades"] == 0
    assert out["buyhold_gross_pct"] == pytest.approx(0.0, abs=1e-10)
    assert out["buyhold_net_pct"] == pytest.approx(expected_net * 100, abs=1e-4)
    assert out["buyhold_pct"] == out["buyhold_net_pct"]
    assert out["buyhold_cost_drag_pct"] > 0
    assert "configured entry/exit fees" in out["buyhold_benchmark_note"]


def test_buy_hold_benchmark_applies_constant_funding_for_full_hold_period():
    df = market()
    signal = np.zeros(len(df))
    funding = 0.001
    out = run_backtest(df, signal, threshold=0.5, start=120,
                       fee_per_side=0.001, spread_slippage_rt=0.002,
                       market_type="futures", leverage=2, position_size=0.5,
                       funding_fee_per_bar=funding)
    bars_held = len(df) - 2 - 120
    expected = (1 - 0.001) ** 2 * (1 - 0.001) ** bars_held * (1 - 0.001) ** 2 - 1
    assert out["buyhold_net_pct"] == pytest.approx(expected * 100, abs=1e-4)
    assert out["buyhold_cost_drag_pct"] > 0


def test_gross_account_return_matches_notional_exposure_for_futures():
    df = market()
    df.loc[121, "open"] = 100.0
    df.loc[122, "open"] = 110.0
    signal = np.zeros(len(df))
    signal[120] = 1.0
    signal[121] = 0.0
    out = run_backtest(df, signal, threshold=0.5, start=120,
                       fee_per_side=0.0, market_type="futures", leverage=2.0)
    trade = out["trades_log"][0]
    assert trade["gross_ret"] == pytest.approx(0.10)
    assert trade["gross_account_ret"] == pytest.approx(0.20)
    assert trade["net_ret"] == pytest.approx(trade["gross_account_ret"])


def test_mae_is_a_signed_non_positive_open_to_open_excursion():
    df = market()
    df.loc[121, "open"] = 100.0
    df.loc[122, "open"] = 98.0
    signal = np.zeros(len(df))
    signal[120] = 1.0
    signal[121] = 0.0
    out = run_backtest(df, signal, threshold=0.5, start=120, fee_per_side=0.0)
    trade = out["trades_log"][0]
    assert trade["mfe"] == pytest.approx(0.0)
    assert trade["mae"] == pytest.approx(-0.02)


def test_atr_stop_and_target_use_ohlc_with_conservative_same_bar_ordering():
    df = market(180)
    df.loc[121, ["open", "high", "low", "close"]] = [100, 105, 96, 101]
    signal = np.zeros(len(df)); signal[120] = 1.0
    atr = np.ones(len(df)) * 2.0
    out = run_backtest(df, signal, threshold=.5, start=120, fee_per_side=0.0,
                       atr=atr, stop_atr_mult=1.5, target_atr_mult=2.0)
    trade = out["trades_log"][0]
    assert trade["exit_reason"] == "stop_loss"
    assert trade["exit_px"] == pytest.approx(97.0)
    assert trade["intrabar_mfe"] == pytest.approx(.05)
    assert trade["intrabar_mae"] == pytest.approx(-.04)


def test_atr_target_exit_when_only_target_is_touched():
    df = market(180)
    df.loc[121, ["open", "high", "low", "close"]] = [100, 105, 99, 104]
    signal = np.zeros(len(df)); signal[120] = 1.0
    out = run_backtest(df, signal, threshold=.5, start=120, fee_per_side=0.0,
                       atr=np.ones(len(df))*2, stop_atr_mult=1.5, target_atr_mult=2.0)
    trade = out["trades_log"][0]
    assert trade["exit_reason"] == "take_profit"
    assert trade["exit_px"] == pytest.approx(104.0)


def test_entry_hours_filter_blocks_new_entry_but_does_not_force_close():
    df = market(180)
    signal = np.zeros(len(df)); signal[120:130] = 1.0
    allowed = np.ones(len(df), dtype=bool); allowed[121] = False
    out = run_backtest(df, signal, threshold=.5, start=120, fee_per_side=0.0,
                       entry_allowed=allowed)
    assert out["trades"] == 1
    assert out["trades_log"][0]["entry_bar"] == 122


def test_execution_delay_shifts_fill_by_configured_bars():
    df = market()
    df.loc[122, "open"] = 110.0
    df.loc[123, "open"] = 121.0
    signal = np.zeros(len(df)); signal[120] = 1.0; signal[121] = 0.0
    base = run_backtest(df, signal, threshold=0.5, fee_per_side=0.0)
    delayed = run_backtest(df, signal, threshold=0.5, fee_per_side=0.0, execution_delay_bars=1)
    assert base["trades_log"][0]["entry_bar"] == 121
    assert delayed["trades_log"][0]["entry_bar"] == 122
    assert delayed["execution_delay_bars"] == 1


def test_market_impact_is_charged_separately_and_in_passive_benchmark():
    df = market()
    signal = np.zeros(len(df)); signal[120] = 1.0; signal[121] = 0.0
    out = run_backtest(df, signal, threshold=0.5, fee_per_side=0.001,
                       spread_slippage_rt=0.001, market_impact_rt=0.002)
    assert out["market_impact_paid_equity_units"] > 0
    assert out["trades_log"][0]["market_impact_drag_pct"] > 0
    assert out["fee_roundtrip"] == pytest.approx(0.005)


def test_spread_and_slippage_can_be_configured_and_reported_separately():
    df=market(); signal=np.zeros(len(df)); signal[120]=1; signal[121]=0
    out=run_backtest(df,signal,threshold=.5,fee_per_side=0.0,spread_rt=.001,slippage_rt=.002)
    trade=out['trades_log'][0]
    assert out['spread_roundtrip']==pytest.approx(.001)
    assert out['slippage_roundtrip']==pytest.approx(.002)
    assert out['spread_slippage_roundtrip']==pytest.approx(.003)
    assert trade['spread_drag_pct']>0 and trade['slippage_drag_pct']>0

def test_backtest_emits_timestamped_equity_points_aligned_to_bar_indices():
    df = market()
    df["time"] = np.arange(len(df), dtype=np.int64) * 3600
    out = run_backtest(df, np.zeros(len(df)), threshold=0.5, start=120)
    assert len(out["equity_curve"]) == len(out["equity_bar_indices"])
    assert len(out["equity_curve"]) == len(out["equity_timestamps"])
    assert out["equity_timestamp_source"] == "time_column"
    assert out["equity_bar_indices"][0] == 121
    assert out["equity_timestamps"][0] == 121 * 3600
    assert out["equity_bar_indices"][-1] == len(df) - 1


def test_backtest_rejects_duplicate_or_non_monotonic_equity_timestamps():
    df = market()
    df["time"] = np.arange(len(df), dtype=np.int64) * 3600
    df.loc[130, "time"] = df.loc[129, "time"]
    with pytest.raises(ValueError, match="unique"):
        run_backtest(df, np.zeros(len(df)), threshold=0.5, start=120)

