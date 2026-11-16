import numpy as np
import pytest

from app import scenario_ranking


def test_scenario_ranking_orders_probabilities_and_sums_to_100():
    # Last-column returns: 40% bullish, 30% sideways, 30% bearish.
    terminal = np.array([1.02] * 4 + [1.001] * 3 + [0.98] * 3)
    paths = terminal[:, None]
    out = scenario_ranking(paths, last_price=1.0, round_trip_fee=0.005)

    assert out["top_scenario"] == "Bullish"
    assert [row["scenario"] for row in out["ranking"]] == ["Bullish", "Sideways", "Bearish"]
    assert sum(row["probability_pct"] for row in out["ranking"]) == 100.0
    assert [row["probability_pct"] for row in out["ranking"]] == [40.0, 30.0, 30.0]
    assert out["ranking"][0]["is_most_likely"] is True
    assert out["probability_type"] == "uncalibrated_monte_carlo_path_frequency"


def test_scenario_ranking_uses_round_trip_fee_as_neutral_band():
    paths = np.array([[1.003], [1.004], [0.996], [0.997], [1.02], [0.98]])
    out = scenario_ranking(paths, last_price=1.0, round_trip_fee=0.004)
    by_name = {row["scenario"]: row for row in out["ranking"]}
    assert by_name["Sideways"]["count"] == 4
    assert by_name["Bullish"]["count"] == 1
    assert by_name["Bearish"]["count"] == 1
    assert out["neutral_band_pct"] == 0.4


def test_scenario_ranking_rejects_invalid_inputs():
    with pytest.raises(ValueError):
        scenario_ranking(np.array([]), 100, 0.004)
    with pytest.raises(ValueError):
        scenario_ranking(np.array([[100.0]]), 0, 0.004)
    with pytest.raises(ValueError):
        scenario_ranking(np.array([[100.0]]), 100, -0.01)
