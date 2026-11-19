from datetime import UTC, datetime, timedelta

import pytest

from regime_transition_forecasting import (
    RegimeForecastError,
    create_regime_forecast_record,
    evaluate_regime_forecasts,
    fit_regime_transition_model,
    forecast_regime_transitions,
    resolve_regime_forecast,
)


AS_OF = datetime(2026, 11, 19, 12, tzinfo=UTC)


def fit_model(regimes=None, **kwargs):
    return fit_regime_transition_model(
        regimes or ["trending", "trending", "ranging", "ranging", "trending",
                    "trending", "volatile", "volatile", "trending"] * 8,
        states=["trending", "ranging", "volatile"],
        training_end_at=AS_OF - timedelta(hours=1),
        as_of=AS_OF,
        dataset_fingerprint="d" * 64,
        min_row_count=2,
        **kwargs,
    )


def test_transition_matrix_is_stochastic_and_deterministic():
    model = fit_model()
    assert all(sum(row) == pytest.approx(1.0) for row in model.transition_probabilities)
    assert model.model_id == fit_model().model_id


def test_unknown_training_state_and_future_training_cutoff_are_rejected():
    with pytest.raises(RegimeForecastError, match="unknown state"):
        fit_model(["trending", "unknown"])
    with pytest.raises(RegimeForecastError, match="after as_of"):
        fit_regime_transition_model(
            ["trending", "ranging"], states=["trending", "ranging"],
            training_end_at=AS_OF + timedelta(minutes=1), as_of=AS_OF,
            dataset_fingerprint="x",
        )


def test_forecast_probabilities_sum_to_one_and_remain_non_actionable():
    model = fit_model()
    result = forecast_regime_transitions(
        model, "trending", horizons=[1, 3, 6], issued_at=AS_OF,
    )
    assert result["actionable"] is False
    for item in result["forecasts"].values():
        if item["probabilities"] is not None:
            assert sum(item["probabilities"].values()) == pytest.approx(1.0)


def test_forecast_before_training_cutoff_is_rejected():
    model = fit_model()
    with pytest.raises(RegimeForecastError, match="precedes"):
        forecast_regime_transitions(
            model, "trending", horizons=[1],
            issued_at=model.training_end_at - timedelta(seconds=1),
        )


def test_insufficient_source_history_withholds_probability_vector():
    model = fit_regime_transition_model(
        ["trending", "ranging", "trending"],
        states=["trending", "ranging", "volatile"],
        training_end_at=AS_OF - timedelta(minutes=1),
        as_of=AS_OF, dataset_fingerprint="sparse", min_row_count=10,
    )
    result = forecast_regime_transitions(model, "volatile", horizons=[1], issued_at=AS_OF)
    assert result["forecasts"]["1"]["probabilities"] is None
    assert result["forecasts"]["1"]["status"] == "INSUFFICIENT_SOURCE_HISTORY"


def make_forecast(instrument="BTCUSDT", horizon=4, probability=None):
    probabilities = probability or {"trending": 0.8, "ranging": 0.1, "volatile": 0.1}
    return create_regime_forecast_record(
        instrument_id=instrument, market_type="linear_perpetual", timeframe="1h",
        horizon_bars=horizon, model_id="markov-v1", dataset_fingerprint="e" * 64,
        current_regime="trending", probabilities=probabilities,
        issued_at=AS_OF, target_at=AS_OF + timedelta(hours=horizon),
        data_cutoff_at=AS_OF - timedelta(minutes=1),
    )


def test_forecast_record_rejects_bad_probabilities_and_lookahead():
    with pytest.raises(RegimeForecastError, match="sum to one"):
        make_forecast(probability={"trending": 0.8, "ranging": 0.1, "volatile": 0.2})
    with pytest.raises(RegimeForecastError, match="cannot be after"):
        create_regime_forecast_record(
            instrument_id="BTCUSDT", market_type="linear_perpetual", timeframe="1h",
            horizon_bars=1, model_id="m", dataset_fingerprint="x",
            current_regime="trending", probabilities={"trending": 0.5, "ranging": 0.5},
            issued_at=AS_OF, target_at=AS_OF + timedelta(hours=1),
            data_cutoff_at=AS_OF + timedelta(seconds=1),
        )


def test_resolution_waits_until_target_and_scoring_is_correct():
    forecast = make_forecast()
    target = forecast.target_at
    with pytest.raises(RegimeForecastError, match="before target_at"):
        resolve_regime_forecast(forecast, "trending", target - timedelta(seconds=1))
    resolved = resolve_regime_forecast(forecast, "trending", target)
    report = evaluate_regime_forecasts([resolved])
    assert report["overall"]["n"] == 1
    assert report["overall"]["top1_accuracy"] == 1.0
    assert report["overall"]["multiclass_log_loss"] == pytest.approx(-__import__("math").log(0.8))
    assert report["actionable"] is False


def test_mixed_instruments_are_scored_as_separate_cohorts():
    btc = resolve_regime_forecast(make_forecast(), "trending", AS_OF + timedelta(hours=4))
    eth = resolve_regime_forecast(make_forecast(instrument="ETHUSDT"), "ranging", AS_OF + timedelta(hours=4))
    report = evaluate_regime_forecasts([btc, eth])
    assert report["overall"]["status"] == "NOT_AGGREGATED_MIXED_COHORTS"
    assert len(report["cohorts"]) == 2
