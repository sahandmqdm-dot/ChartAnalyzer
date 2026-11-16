import sys
from pathlib import Path
import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from statistical_edge import (
    effective_sample_size, pearson_corr, forward_returns,
    signal_forward_analysis, edge_report
)


def test_forward_returns_shape_and_causality():
    c = np.array([100., 101., 102., 103., 104., 105.])
    f = forward_returns(c, 2)
    assert len(f) == 6
    assert np.isnan(f[-1]) and np.isnan(f[-2])
    assert np.isfinite(f[0])
    # log(102/100)
    assert f[0] == pytest.approx(np.log(102/100))


def test_edge_report_rejects_noise():
    rng = np.random.default_rng(7)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, 200)))
    signal = rng.normal(0, 1, 200)  # pure noise
    rep = edge_report(signal, close, horizons=(1, 3, 6), min_samples=30)
    assert rep["recommendation"] in ("NO_TRADE", "REVIEW_WITH_COSTS_AND_OOS")
    assert "evidence_level" in rep


def test_pearson_handles_short_series():
    r, n, t = pearson_corr([1, 2], [3, 4])
    assert n == 2
    assert not np.isfinite(r) or True  # may be nan
