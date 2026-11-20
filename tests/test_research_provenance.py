import numpy as np
import pandas as pd

from research_provenance import build_provenance, dataset_fingerprint, audit_research_cohort


def candles(n=80):
    close = np.linspace(100.0, 110.0, n)
    return pd.DataFrame({
        "time": 1_700_000_000.0 + np.arange(n) * 3600,
        "open": close,
        "high": close + 1,
        "low": close - 1,
        "close": close,
        "volume": np.ones(n),
    })


def test_same_window_and_config_share_reproducible_run_id_across_scopes():
    df = candles()
    audit = {"state": "PASS", "valid": True, "gap_count": 0, "warnings": [], "errors": []}
    kwargs = dict(source="csv", symbol="BTCUSDT", timeframe="1h", requested_candles=80,
                  market_type="spot", strategy_config={"fee": 0.001, "threshold": 0.2}, data_integrity=audit)
    backtest = build_provenance(df, evaluation_scope="full_window_descriptive", **kwargs)
    wf = build_provenance(df, evaluation_scope="sequential_out_of_sample", **kwargs)
    assert backtest["run_id"] == wf["run_id"]
    assert backtest["dataset_id"] == wf["dataset_id"]
    assert backtest["evaluation_scope"] != wf["evaluation_scope"]


def test_dataset_fingerprint_changes_when_any_candle_value_changes():
    df = candles()
    original = dataset_fingerprint(df, source="csv", symbol="BTCUSDT", timeframe="1h")
    df.loc[10, "close"] += 0.01
    changed = dataset_fingerprint(df, source="csv", symbol="BTCUSDT", timeframe="1h")
    assert original != changed


def test_research_cohort_integrity_detects_stale_module_counts_and_index_mismatch():
    trades = [{"entry_bar": 10, "exit_bar": 12, "net_ret": -0.01}]
    out = audit_research_cohort(
        trades,
        trade_intelligence={"trades": 2},
        exit_path_intelligence={"status": "descriptive_only", "trades_analyzed": 1, "trades_invalid": 0,
                               "trades": [{"entry_bar": 10, "exit_bar": 12}]},
        trade_failure_intelligence={"status": "descriptive_and_counterfactual", "trades_analyzed": 1,
                                    "trades_invalid": 0,
                                    "trades": [{"audit_status": "ok", "entry_bar": 10, "exit_bar": 13}]},
        entry_edge_research={"status": "RESEARCH_ONLY", "samples": 0, "invalid_or_missing": 0},
    )
    assert out["status"] == "MISMATCH"
    assert "trade_intelligence_count_matches" in out["mismatches"]
    assert "forensic_trade_indices_match" in out["mismatches"]
    assert "entry_edge_count_matches" in out["mismatches"]


def test_research_cohort_integrity_passes_when_all_forensic_modules_share_trades():
    trades = [
        {"entry_bar": 10, "exit_bar": 12, "net_ret": -0.01},
        {"entry_bar": 20, "exit_bar": 23, "net_ret": 0.02},
    ]
    rows = [
        {"entry_bar": 10, "exit_bar": 12},
        {"entry_bar": 20, "exit_bar": 23},
    ]
    out = audit_research_cohort(
        trades,
        trade_intelligence={"trades": 2},
        exit_path_intelligence={"status": "descriptive_only", "trades_analyzed": 2,
                                "trades_invalid": 0, "trades": rows},
        trade_failure_intelligence={"status": "descriptive_and_counterfactual", "trades_analyzed": 2,
                                    "trades_invalid": 0,
                                    "trades": [dict(r, audit_status="ok") for r in rows]},
        entry_edge_research={"status": "RESEARCH_ONLY", "samples": 2, "invalid_or_missing": 0},
    )
    assert out["status"] == "PASS"
    assert out["expected_trade_count"] == 2
    assert all(out["checks"].values())


def test_config_fingerprint_tracks_research_engine_revision(monkeypatch):
    import research_provenance as provenance

    config = {"threshold": 0.2, "fee": 0.001}
    first = provenance.config_fingerprint(config)
    monkeypatch.setattr(provenance, "RESEARCH_ENGINE_REVISION", "next-engine-revision")
    second = provenance.config_fingerprint(config)
    assert first != second
