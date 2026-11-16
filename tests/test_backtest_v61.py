import sys
from pathlib import Path
import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backtest_engine import run_backtest


def flat_market(n=180):
    px = np.full(n, 100.0)
    return pd.DataFrame({"open": px, "high": px + 1, "low": px - 1,
                         "close": px, "volume": np.ones(n)})


def test_spot_forces_long_only_even_if_short_flag_is_true():
    df = flat_market()
    signal = np.zeros(len(df))
    signal[120:122] = -1.0
    out = run_backtest(df, signal, threshold=0.5, allow_short=True,
                       market_type="spot", fee_per_side=0.0)
    assert out["market_type"] == "spot"
    assert out["allow_short"] is False
    assert out["trades"] == 0
    assert out["short_trades"] == 0


def test_futures_can_open_short_positions():
    df = flat_market()
    signal = np.zeros(len(df))
    signal[120] = -1.0
    signal[121] = 0.0
    out = run_backtest(df, signal, threshold=0.5, allow_short=True,
                       market_type="futures", fee_per_side=0.0)
    assert out["short_trades"] == 1
    assert out["trades_log"][0]["side"] == "short"


def test_position_size_scales_both_market_pnl_and_transaction_costs():
    df = flat_market()
    df.loc[122:, "open"] = 110.0
    df.loc[122:, "close"] = 110.0
    signal = np.zeros(len(df))
    signal[120] = 1.0
    signal[121] = 0.0
    full = run_backtest(df, signal, threshold=0.5, fee_per_side=0.001,
                        position_size=1.0, spread_slippage_rt=0.002)
    half = run_backtest(df, signal, threshold=0.5, fee_per_side=0.001,
                        position_size=0.5, spread_slippage_rt=0.002)
    assert half["trades_log"][0]["net_ret"] < full["trades_log"][0]["net_ret"]
    assert half["fees_paid_equity_units"] < full["fees_paid_equity_units"]
    assert half["spread_slippage_paid_equity_units"] < full["spread_slippage_paid_equity_units"]
    assert half["trades_log"][0]["net_ret"] == pytest.approx(0.5 * full["trades_log"][0]["gross_ret"], abs=0.02)


def test_net_expectancy_includes_entry_exit_fees_and_spread():
    df = flat_market()
    signal = np.zeros(len(df))
    signal[120] = 1.0
    signal[121] = 0.0
    out = run_backtest(df, signal, threshold=0.5, fee_per_side=0.001,
                       spread_slippage_rt=0.002, market_type="spot")
    trade = out["trades_log"][0]
    assert trade["gross_ret"] == pytest.approx(0.0)
    assert trade["net_ret"] < 0.0
    assert out["net_expectancy_pct"] == pytest.approx(trade["net_ret_pct"], abs=0.001)
    assert out["expectancy_pct"] == out["net_expectancy_pct"]
    assert out["gross_profit_factor"] is None
    assert out["net_profit_factor"] == 0.0


def test_futures_leverage_and_constant_funding_are_explicitly_applied():
    df = flat_market()
    df.loc[122:, "open"] = 101.0
    df.loc[122:, "close"] = 101.0
    signal = np.zeros(len(df))
    signal[120] = 1.0
    signal[121] = 0.0
    out = run_backtest(df, signal, threshold=0.5, fee_per_side=0.0,
                       market_type="futures", allow_short=True,
                       leverage=2.0, funding_fee_per_bar=0.001)
    assert out["notional_exposure"] == 2.0
    assert out["funding_modelled"] is True
    assert out["funding_paid_equity_units"] > 0
    assert out["liquidation_modelled"] is False


def test_spot_rejects_leverage_and_funding():
    df = flat_market()
    signal = np.zeros(len(df))
    with pytest.raises(ValueError, match="spot market does not support leverage"):
        run_backtest(df, signal, market_type="spot", leverage=2.0)
    with pytest.raises(ValueError, match="funding fees apply only to futures"):
        run_backtest(df, signal, market_type="spot", funding_fee_per_bar=0.001)


def test_app_wrapper_routes_futures_settings_and_execution_costs(monkeypatch):
    import app
    df = flat_market()
    signal = np.zeros(len(df))
    signal[120] = -1.0
    signal[121] = 0.0
    cfg = {
        "verdict_threshold": 0.5,
        "backtest": {
            "market_type": "futures",
            "allow_short_futures": True,
            "position_size": 1.0,
            "futures_leverage": 1.0,
            "futures_fee_roundtrip": 0.001,
            "spread_slippage_roundtrip": 0.0015,
            "funding_fee_per_bar": 0.0,
        },
        "gate": {"spread_slippage_roundtrip": 0.001},
    }
    monkeypatch.setattr(app, "conf", lambda: cfg)
    monkeypatch.setattr(app, "compute", lambda _df, _tf: pd.DataFrame({"S": signal}))
    out = app.backtest(df, "1h", symbol="BTCUSDT")
    assert out["market_type"] == "futures"
    assert out["short_trades"] == 1
    assert out["spread_slippage_rt"] == pytest.approx(0.0015)
    assert out["fee_per_side"] == pytest.approx(0.0005)
    assert out["fee_roundtrip"] == pytest.approx(0.0025)
