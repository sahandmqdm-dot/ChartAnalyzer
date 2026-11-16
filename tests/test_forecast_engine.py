import sys
from pathlib import Path
import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from forecast_engine import multi_horizon_forecast, scenario_narratives


def test_multi_horizon_basic():
    rng = np.random.default_rng(1)
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.008, 250)))
    f = multi_horizon_forecast(c, horizons=(1, 6, 12), n_paths=400, seed=99)
    assert f["last_price"] == pytest.approx(c[-1])
    assert "6" in f["horizons"]
    h6 = f["horizons"]["6"]
    assert h6["price_quantiles"]["p10"] < h6["price_quantiles"]["p50"] < h6["price_quantiles"]["p90"]
    assert f["directional_probabilities"]["6"]["status"] == "EXPERIMENTAL_UNCALIBRATED"


def test_scenarios_present():
    rng = np.random.default_rng(2)
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, 200)))
    f = multi_horizon_forecast(c, horizons=(3, 12), n_paths=300)
    sc = scenario_narratives(f)
    assert "bullish" in sc and "bearish" in sc and "sideways" in sc
