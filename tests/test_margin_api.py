import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import app


def test_margin_api_uses_configured_defaults_and_returns_version(monkeypatch):
    monkeypatch.setattr(app, "conf", lambda: {
        "futures_margin": {
            "margin_mode": "isolated", "maintenance_margin_rate": 0.006,
            "liquidation_fee_rate": 0.004, "max_leverage": 50,
            "warning_distance_pct": 6.0, "critical_distance_pct": 2.5,
        },
        "backtest": {"futures_leverage": 8},
    })
    out = app.api_margin(entry_price=100, quantity=2)
    assert out["version"] == "7.2.0"
    assert out["leverage"] == pytest.approx(8)
    assert out["maintenance_margin_rate"] == pytest.approx(0.006)
    assert out["liquidation_fee_rate"] == pytest.approx(0.004)
    assert out["max_leverage"] == pytest.approx(50)


def test_margin_api_accepts_explicit_mark_price_and_short_side(monkeypatch):
    monkeypatch.setattr(app, "conf", lambda: {
        "futures_margin": {"margin_mode": "isolated"},
        "backtest": {"futures_leverage": 5},
    })
    out = app.api_margin(side="short", entry_price=100, quantity=1,
                         leverage=10, mark_price=105)
    assert out["side"] == "short"
    assert out["mark_price"] == pytest.approx(105)
    assert out["margin_health"] == "warning"


def test_margin_api_does_not_silently_replace_invalid_explicit_values(monkeypatch):
    monkeypatch.setattr(app, "conf", lambda: {
        "futures_margin": {"margin_mode": "isolated"},
        "backtest": {"futures_leverage": 5},
    })
    with pytest.raises(app.HTTPException) as err:
        app.api_margin(entry_price=100, quantity=1, leverage=-2)
    assert err.value.status_code == 400
    assert "leverage" in err.value.detail

    with pytest.raises(app.HTTPException) as err:
        app.api_margin(entry_price=100, quantity=1, mark_price=-1)
    assert err.value.status_code == 400
    assert "mark_price" in err.value.detail


def test_margin_api_rejects_cross_margin_configuration(monkeypatch):
    monkeypatch.setattr(app, "conf", lambda: {
        "futures_margin": {"margin_mode": "cross"},
        "backtest": {},
    })
    with pytest.raises(app.HTTPException) as err:
        app.api_margin(entry_price=100, quantity=1)
    assert err.value.status_code == 400
    assert "isolated" in err.value.detail


def test_margin_route_and_dashboard_calculator_are_exposed():
    assert ("GET", "/api/margin") in app.app.routes
    assert "Futures Isolated-Margin Calculator" in app.PAGE
    assert "onclick=marginCalc()" in app.PAGE
    assert "api('/api/margin'" in app.PAGE
