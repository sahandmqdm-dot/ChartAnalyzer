import numpy as np
import pytest

from prospective_baselines import build_baseline_quantiles, paired_block_bootstrap_mean_ci


def test_baselines_are_point_in_time_finite_and_ordered():
    rng = np.random.default_rng(19)
    close = 100.0 * np.exp(np.cumsum(rng.normal(0.0002, 0.01, 300)))
    baselines = build_baseline_quantiles(close, 12)
    assert set(baselines) == {
        "zero_return_point", "historical_unconditional", "ewma_gaussian"
    }
    for values in baselines.values():
        assert values["q10"] <= values["q50"] <= values["q90"]
        assert all(np.isfinite(value) for value in values.values())


def test_baseline_builder_rejects_insufficient_horizon_history():
    with pytest.raises(ValueError, match="30 historical horizon-matched returns"):
        build_baseline_quantiles(np.linspace(100, 110, 40), 11)


def test_paired_block_bootstrap_detects_consistent_negative_loss_delta():
    result = paired_block_bootstrap_mean_ci(np.full(60, -0.1), seed=19)
    assert result["status"] == "ESTIMATED"
    assert result["mean_difference"] == pytest.approx(-0.1)
    assert result["ci_upper"] < 0


def test_paired_block_bootstrap_marks_small_sample_insufficient():
    result = paired_block_bootstrap_mean_ci(np.full(29, -0.1))
    assert result["status"] == "INSUFFICIENT_SAMPLE"
    assert result["ci_lower"] is None
    assert result["n"] == 29


def test_paired_block_bootstrap_is_reproducible():
    values = np.random.default_rng(7).normal(0, 1, 100)
    first = paired_block_bootstrap_mean_ci(values, seed=77)
    second = paired_block_bootstrap_mean_ci(values, seed=77)
    assert first == second
