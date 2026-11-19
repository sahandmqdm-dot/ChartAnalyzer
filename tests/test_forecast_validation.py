from datetime import UTC, datetime, timedelta

import pytest

from forecast_validation import (
    ForecastValidationError,
    calibration_report,
    create_forecast_record,
    evaluate_forecasts,
    resolve_forecast,
)


ISSUED = datetime(2026, 11, 1, tzinfo=UTC)
TARGET = ISSUED + timedelta(hours=4)


def make_forecast(**overrides):
    values = {
        "instrument_id": "BTCUSDT",
        "market_type": "linear_perpetual",
        "timeframe": "1h",
        "horizon_bars": 4,
        "issued_at": ISSUED,
        "target_at": TARGET,
        "data_cutoff_at": ISSUED - timedelta(hours=1),
        "model_id": "scenario-bootstrap",
        "model_version": "1.0.0",
        "dataset_fingerprint": "a" * 64,
        "regime": "trending",
        "q10": -0.02,
        "q50": 0.001,
        "q90": 0.025,
        "probability_up": 0.6,
    }
    values.update(overrides)
    return create_forecast_record(**values)


def test_forecast_identity_is_deterministic_and_payload_sensitive():
    a = make_forecast()
    b = make_forecast()
    c = make_forecast(q50=0.002)
    assert a.forecast_id == b.forecast_id
    assert a.forecast_id != c.forecast_id


def test_lookahead_data_cutoff_is_rejected():
    with pytest.raises(ForecastValidationError, match="look-ahead"):
        make_forecast(data_cutoff_at=ISSUED + timedelta(seconds=1))


def test_forecast_target_must_be_in_future():
    with pytest.raises(ForecastValidationError, match="after issued_at"):
        make_forecast(target_at=ISSUED)


@pytest.mark.parametrize("quantiles", [(0.1, 0.0, 0.2), (0.0, 0.2, 0.1)])
def test_quantiles_must_be_ordered(quantiles):
    with pytest.raises(ForecastValidationError, match="q10"):
        make_forecast(q10=quantiles[0], q50=quantiles[1], q90=quantiles[2])


@pytest.mark.parametrize("probability", [-0.01, 1.01, float("nan")])
def test_probability_must_be_valid(probability):
    with pytest.raises(ForecastValidationError):
        make_forecast(probability_up=probability)


def test_naive_timestamps_are_rejected():
    with pytest.raises(ForecastValidationError, match="timezone-aware"):
        make_forecast(issued_at=datetime(2026, 11, 1))


def test_forecast_cannot_be_resolved_before_target():
    forecast = make_forecast()
    with pytest.raises(ForecastValidationError, match="before its target"):
        resolve_forecast(forecast, 0.01, TARGET - timedelta(seconds=1))


def test_evaluation_metrics_and_non_actionable_output():
    rows = []
    for index in range(10):
        forecast = make_forecast(
            issued_at=ISSUED + timedelta(days=index),
            data_cutoff_at=ISSUED + timedelta(days=index) - timedelta(hours=1),
            target_at=TARGET + timedelta(days=index),
            q10=-0.02, q50=0.0, q90=0.02,
            probability_up=0.8,
        )
        actual = 0.01 if index < 8 else -0.01
        rows.append(resolve_forecast(forecast, actual, TARGET + timedelta(days=index)))
    report = evaluate_forecasts(rows, min_segment_count=5)
    assert report["status"] == "EVALUATED_OUT_OF_SAMPLE"
    assert report["overall"]["n"] == 10
    assert report["overall"]["directional_brier_score"] == pytest.approx(0.04)
    assert report["overall"]["central_80_interval_coverage"] == 1.0
    assert report["actionable"] is False
    assert report["segments"]["regime"]["trending"]["sample_sufficient"] is True


def test_calibration_report_counts_each_bin_and_reports_sample_size():
    rows = []
    for index in range(6):
        forecast = make_forecast(
            issued_at=ISSUED + timedelta(days=index),
            data_cutoff_at=ISSUED + timedelta(days=index) - timedelta(minutes=1),
            target_at=TARGET + timedelta(days=index),
            probability_up=0.9,
        )
        rows.append(resolve_forecast(forecast, 0.01 if index < 5 else -0.01,
                                     TARGET + timedelta(days=index)))
    result = calibration_report(rows, bins=10, min_bin_count=5)
    assert result["n"] == 6
    assert result["reliability_table"][0]["n"] == 6
    assert result["reliability_table"][0]["sample_sufficient"] is True
    assert result["expected_calibration_error"] == pytest.approx(1 / 15)


def test_duplicate_forecast_ids_are_rejected():
    forecast = make_forecast()
    resolved = resolve_forecast(forecast, 0.01, TARGET)
    with pytest.raises(ForecastValidationError, match="duplicate"):
        evaluate_forecasts([resolved, resolved])


def test_empty_evaluation_is_explicit():
    result = evaluate_forecasts([])
    assert result["status"] == "NO_RESOLVED_FORECASTS"
    assert result["overall"]["n"] == 0


def test_insufficient_segment_sample_is_flagged_not_hidden():
    rows = [
        resolve_forecast(make_forecast(issued_at=ISSUED + timedelta(days=i),
            data_cutoff_at=ISSUED + timedelta(days=i) - timedelta(minutes=1),
            target_at=TARGET + timedelta(days=i)), 0.0, TARGET + timedelta(days=i))
        for i in range(3)
    ]
    result = evaluate_forecasts(rows, min_segment_count=5)
    assert result["segments"]["model"]["scenario-bootstrap"]["sample_sufficient"] is False
