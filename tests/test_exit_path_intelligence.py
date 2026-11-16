import pandas as pd

from exit_path_intelligence import analyze_exit_paths


def test_signal_exit_audits_peak_timestamp_and_giveback_without_exit_candle_leakage():
    df = pd.DataFrame(
        {
            "open": [100, 100, 102, 101, 99],
            "high": [101, 103, 106, 104, 120],
            "low": [99, 99, 100, 98, 90],
            "close": [100, 102, 104, 99, 100],
        },
        index=pd.date_range("2026-01-01", periods=5, freq="h"),
    )
    trades = [{
        "global_entry_bar": 1,
        "global_exit_bar": 4,
        "entry_px": 100,
        "exit_px": 99,
        "side": "long",
        "exit_reason": "signal",
        "net_ret": -0.02,
    }]
    out = analyze_exit_paths(trades, df)
    row = out["trades"][0]
    assert row["mfe_pct"] == 6.0
    assert row["mfe_peak_bar"] == 2
    assert row["bars_from_mfe_peak_to_exit"] == 2
    assert row["realized_gross_price_return_pct"] == -1.0
    assert row["favorable_excursion_giveback_pct"] == 7.0
    assert row["gross_mfe_capture_ratio"] == -0.1667
    assert row["classification"] == "loss_after_positive_mfe"
    assert out["overall"]["losers_with_positive_mfe"] == 1


def test_atr_exit_includes_exit_candle_and_short_direction_is_correct():
    df = pd.DataFrame({
        "open": [100, 100, 100],
        "high": [101, 102, 103],
        "low": [99, 97, 94],
        "close": [100, 98, 95],
    })
    out = analyze_exit_paths([{
        "global_entry_bar": 1,
        "global_exit_bar": 2,
        "entry_px": 100,
        "exit_px": 95,
        "side": "short",
        "exit_reason": "stop_loss",
        "net_ret": 0.04,
    }], df)
    row = out["trades"][0]
    assert row["mfe_pct"] == 6.0
    assert row["mfe_peak_bar"] == 2
    assert row["realized_gross_price_return_pct"] == 5.0
    assert row["favorable_excursion_giveback_pct"] == 1.0


def test_invalid_records_fail_closed():
    df = pd.DataFrame({"open": [1, 1], "high": [1, 1], "low": [1, 1], "close": [1, 1]})
    out = analyze_exit_paths([{"entry_bar": 5, "exit_bar": 6, "entry_px": 1}], df)
    assert out["status"] == "no_valid_trades"
    assert out["trades_invalid"] == 1


def test_aggregate_capture_ratio_excludes_losing_trades_and_reports_denominator():
    df = pd.DataFrame({
        "open": [100, 100, 100, 100],
        "high": [101, 105, 104, 103],
        "low": [99, 99, 98, 97],
        "close": [100, 103, 102, 99],
    })
    trades = [
        {"global_entry_bar": 0, "global_exit_bar": 2, "entry_px": 100, "exit_px": 102,
         "side": "long", "exit_reason": "signal", "net_ret": 0.01},
        {"global_entry_bar": 0, "global_exit_bar": 3, "entry_px": 100, "exit_px": 99,
         "side": "long", "exit_reason": "signal", "net_ret": -0.02},
    ]
    out = analyze_exit_paths(trades, df)
    assert out["overall"]["gross_mfe_capture_ratio_n"] == 1
    assert out["overall"]["mean_gross_mfe_capture_ratio"] == 0.4
    assert out["overall"]["median_gross_mfe_capture_ratio"] == 0.4
    assert out["overall"]["losers_with_positive_mfe"] == 1


def test_exit_path_uses_ohlcv_time_column_for_peak_timestamp():
    from datetime import datetime, timezone
    base = 1_700_000_000
    df = pd.DataFrame({
        "time": [base, base + 3600, base + 7200],
        "open": [100, 100, 101],
        "high": [101, 105, 102],
        "low": [99, 99, 100],
        "close": [100, 103, 101],
    })
    out = analyze_exit_paths([{
        "global_entry_bar": 0, "global_exit_bar": 2, "entry_px": 100,
        "exit_px": 101, "side": "long", "exit_reason": "signal", "net_ret": 0.0,
    }], df)
    expected = datetime.fromtimestamp(base + 3600, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    assert out["trades"][0]["mfe_peak_timestamp"] == expected
