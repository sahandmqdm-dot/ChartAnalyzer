import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import app


def test_walk_forward_api_uses_config_defaults_and_returns_market_metadata(monkeypatch, tmp_path):
    import time
    n = 200
    df = pd.DataFrame({"time": time.time() - (n - np.arange(n)) * 60 * 60,
                       "open": [1.0] * n, "high": [2.0] * n,
                       "low": [0.5] * n, "close": [1.0] * n,
                       "volume": [10.0] * n})
    monkeypatch.setattr(app, "conf", lambda: {
        "walk_forward": {"n_splits": 4, "warmup_bars": 80, "gap_bars": 3},
        "backtest": {"market_type": "futures"},
    })
    monkeypatch.setattr(app, "VALIDATION_EVIDENCE_PATH", str(tmp_path / "evidence.json"))
    monkeypatch.setattr(app, "RESEARCH_REGISTRY_PATH", str(tmp_path / "registry.db"))
    monkeypatch.setattr(app, "load_cached", lambda *a, **k: df)
    captured = {}
    def fake_wf(frame, tf, **kwargs):
        captured.update(kwargs)
        return {"n_folds": 4, "folds": [], "trades": 0, "strategy_pct": 0.0,
                "market_type": "futures", "allow_short": True, "position_size": 1.0,
                "leverage": 1.0, "fee_per_side": 0.0, "spread_slippage_rt": 0.0,
                "funding_fee_per_bar": 0.0, "stop_atr_mult": 1.5, "target_atr_mult": 2.0}
    monkeypatch.setattr(app, "walk_forward_backtest", fake_wf)

    out = app.api_walk_forward(symbol="BTCUSDT", tf="4h", n=200)
    assert captured["n_splits"] is None
    assert captured["warmup_bars"] is None
    assert captured["gap_bars"] is None
    assert out["market_type"] == "futures"
    assert out["candles"] == 200
    assert out["symbol"] == "BTCUSDT"
    assert out["timeframe"] == "4h"
    assert out["deployment_eligibility"]["status"] == "BLOCKED"
    assert (tmp_path / "evidence.json").exists()


def test_walk_forward_api_clamps_explicit_parameters(monkeypatch, tmp_path):
    import time
    n = 300
    df = pd.DataFrame({"time": time.time() - (n - np.arange(n)) * 60 * 60,
                       "open": [1.0] * n, "high": [2.0] * n,
                       "low": [0.5] * n, "close": [1.0] * n,
                       "volume": [10.0] * n})
    monkeypatch.setattr(app, "conf", lambda: {"walk_forward": {}, "backtest": {"market_type": "spot"}})
    monkeypatch.setattr(app, "VALIDATION_EVIDENCE_PATH", str(tmp_path / "evidence.json"))
    monkeypatch.setattr(app, "RESEARCH_REGISTRY_PATH", str(tmp_path / "registry.db"))
    monkeypatch.setattr(app, "load_cached", lambda *a, **k: df)
    calls = []
    def fake_wf(frame, tf, **kw):
        calls.append(dict(kw))
        return {"folds": [], "trades": 0, "strategy_pct": 0.0, "net_expectancy_pct": None,
                "net_profit_factor": None, "max_dd_pct": None, "trades_log": []}
    monkeypatch.setattr(app, "walk_forward_backtest", fake_wf)
    app.api_walk_forward(n=300, n_splits=99, warmup_bars=999, gap_bars=999)
    assert calls[0] == {"symbol": "USDTIRR", "n_splits": 20, "warmup_bars": 236, "gap_bars": 236}
    assert calls[1] == {"symbol": "USDTIRR", "n_splits": 20, "warmup_bars": 236, "gap_bars": 236,
                        "cost_multiplier": 1.5, "include_diagnostics": False}


def test_dashboard_exposes_walk_forward_button_and_fold_renderer():
    assert 'onclick=walkForward()' in app.PAGE
    assert 'Fold breakdown' in app.PAGE
    assert "api('/api/walk-forward'" in app.PAGE
    assert "function renderTradeIntelligence(ti)" in app.PAGE
    assert "cost-adjusted passive long" in app.PAGE
    assert "Backtest + Walk-Forward candle window" in app.PAGE
    assert "function provenanceHtml(j)" in app.PAGE
    assert "Net passive long" in app.PAGE
    assert "Average per-trade compounded cost components" in app.PAGE


def test_app_walk_forward_passes_regime_context_to_trade_intelligence(monkeypatch):
    import numpy as np
    n = 400
    df = pd.DataFrame({"open": np.linspace(100, 120, n),
                       "high": np.linspace(101, 121, n),
                       "low": np.linspace(99, 119, n),
                       "close": np.linspace(100, 120, n),
                       "volume": np.ones(n)})
    regimes = np.asarray([f"regime-{i}" for i in range(n)], dtype=object)
    monkeypatch.setattr(app, "conf", lambda: {
        "verdict_threshold": 0.5,
        "backtest": {"market_type": "spot", "position_size": 1.0,
                     "spread_slippage_roundtrip": 0.0},
        "walk_forward": {"n_splits": 3, "warmup_bars": 100, "gap_bars": 0},
        "gate": {},
    })
    monkeypatch.setattr(app, "get_fee", lambda *_args: 0.0)
    monkeypatch.setattr(app, "compute", lambda *_args: {
        "S": pd.Series(np.ones(n)), "regime": pd.Series(regimes),
    })
    out = app.walk_forward_backtest(df, "1h", symbol="USDTIRR")
    first_fold = out["folds"][0]
    first_trade = first_fold["result"]["trades_log"][0]
    assert first_trade["entry_regime"] == regimes[first_fold["test_start_index"] - 1]
    assert out["trade_intelligence"]["by_entry_regime"]
    assert "unknown" not in out["trade_intelligence"]["by_entry_regime"]
