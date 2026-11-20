# ruff: noqa: I001  # Flat-layout first-party import classification differs across Ruff versions.
import datetime
import pytest
import sqlite3
from forecast_validation import create_forecast_record
from prospective_holdout import ProspectiveHoldoutLedger


UTC = datetime.UTC
NOW = datetime.datetime(2030, 1, 1, tzinfo=UTC)
START = NOW + datetime.timedelta(days=1)
END = START + datetime.timedelta(days=7)
CONFIG = "c" * 64
INPUT_DATA = "a" * 64
OUTCOME_DATA = "b" * 64


def make_ledger(tmp_path, *, min_forecasts=30):
    ledger = ProspectiveHoldoutLedger(tmp_path / "prospective.sqlite3")
    ledger.register_cohort(
        cohort_id="BTC-SPOT-1H-4B-2020",
        instrument_id="BTCUSDT",
        market_type="spot",
        timeframe="1h",
        horizon_bars=4,
        model_id="cmi-scenario",
        model_version="1.0.0",
        config_fingerprint=CONFIG,
        collection_start=START,
        collection_end=END,
        min_forecasts=min_forecasts,
        now=NOW,
    )
    return ledger


def make_forecast(index=0, **overrides):
    cutoff = START + datetime.timedelta(hours=4 * index)
    issued = cutoff + datetime.timedelta(minutes=1)
    values = {
        "instrument_id": "BTCUSDT",
        "market_type": "spot",
        "timeframe": "1h",
        "horizon_bars": 4,
        "issued_at": issued,
        "target_at": cutoff + datetime.timedelta(hours=4),
        "data_cutoff_at": cutoff,
        "model_id": "cmi-scenario",
        "model_version": "1.0.0",
        "dataset_fingerprint": INPUT_DATA,
        "regime": "trending",
        "q10": -0.02,
        "q50": 0.001,
        "q90": 0.025,
        "probability_up": 0.6,
    }
    values.update(overrides)
    return create_forecast_record(**values)


def append_forecast(ledger, index=0, **kwargs):
    forecast = make_forecast(index, **kwargs)
    ledger.record_forecast(
        forecast,
        config_fingerprint=CONFIG,
        input_source="ramzinex-public",
        reference_price=100.0,
        recorded_at=forecast.issued_at + datetime.timedelta(seconds=1),
    )
    return forecast


def resolve(ledger, forecast, actual=0.01):
    resolved = forecast.target_at + datetime.timedelta(minutes=1)
    return ledger.resolve_forecast(
        forecast.forecast_id,
        actual_log_return=actual,
        outcome_source="independent-outcome-feed",
        outcome_dataset_fingerprint=OUTCOME_DATA,
        outcome_available_at=resolved,
        resolved_at=resolved,
    )


def test_cohort_must_be_registered_before_future_collection(tmp_path):
    ledger = ProspectiveHoldoutLedger(tmp_path / "ledger.db")
    with pytest.raises(ValueError, match="in the future"):
        ledger.register_cohort(
            cohort_id="C1", instrument_id="BTCUSDT", market_type="spot",
            timeframe="1h", horizon_bars=4, model_id="m", model_version="1",
            config_fingerprint=CONFIG, collection_start=NOW,
            collection_end=END, now=NOW,
        )


def test_only_one_frozen_cohort_can_be_registered(tmp_path):
    ledger = make_ledger(tmp_path)
    with pytest.raises(ValueError, match="already has a cohort"):
        ledger.register_cohort(
            cohort_id="C2", instrument_id="BTCUSDT", market_type="spot",
            timeframe="1h", horizon_bars=4, model_id="m", model_version="1",
            config_fingerprint=CONFIG, collection_start=START,
            collection_end=END, now=NOW,
        )


def test_forecast_must_match_frozen_config_and_model(tmp_path):
    ledger = make_ledger(tmp_path)
    forecast = make_forecast()
    with pytest.raises(ValueError, match="config fingerprint"):
        ledger.record_forecast(
            forecast, config_fingerprint="d" * 64, input_source="feed", reference_price=100.0,
            recorded_at=forecast.issued_at + datetime.timedelta(seconds=1),
        )
    with pytest.raises(ValueError, match="model_version"):
        append_forecast(ledger, model_version="2.0.0")


def test_forecast_must_be_persisted_before_target(tmp_path):
    ledger = make_ledger(tmp_path)
    forecast = make_forecast()
    with pytest.raises(ValueError, match="strictly before its target"):
        ledger.record_forecast(
            forecast, config_fingerprint=CONFIG, input_source="feed", reference_price=100.0,
            recorded_at=forecast.target_at,
        )


def test_delayed_forecast_persistence_is_rejected(tmp_path):
    ledger = make_ledger(tmp_path)
    forecast = make_forecast()
    with pytest.raises(ValueError, match="within 60 seconds"):
        ledger.record_forecast(
            forecast, config_fingerprint=CONFIG, input_source="feed", reference_price=100.0,
            recorded_at=forecast.issued_at + datetime.timedelta(minutes=2),
        )


def test_forecast_target_must_match_declared_horizon(tmp_path):
    ledger = make_ledger(tmp_path)
    forecast = make_forecast(target_at=START + datetime.timedelta(hours=5))
    with pytest.raises(ValueError, match="horizon_bars"):
        ledger.record_forecast(
            forecast, config_fingerprint=CONFIG, input_source="feed", reference_price=100.0,
            recorded_at=forecast.issued_at + datetime.timedelta(seconds=1),
        )


def test_overlapping_forecast_horizons_are_rejected(tmp_path):
    ledger = make_ledger(tmp_path)
    first = append_forecast(ledger, 0)
    overlapping = make_forecast(
        1, issued_at=first.issued_at + datetime.timedelta(hours=1),
        target_at=first.target_at + datetime.timedelta(minutes=59),
        data_cutoff_at=first.data_cutoff_at + datetime.timedelta(minutes=59),
    )
    with pytest.raises(ValueError, match="overlapping"):
        ledger.record_forecast(
            overlapping, config_fingerprint=CONFIG, input_source="feed", reference_price=100.0,
            recorded_at=overlapping.issued_at + datetime.timedelta(seconds=1),
        )


def test_outcome_cannot_be_resolved_before_horizon_or_data_availability(tmp_path):
    ledger = make_ledger(tmp_path)
    forecast = append_forecast(ledger)
    early = forecast.target_at - datetime.timedelta(seconds=1)
    with pytest.raises(ValueError, match="after target_at"):
        ledger.resolve_forecast(
            forecast.forecast_id, actual_log_return=0.01,
            outcome_source="outcome-feed", outcome_dataset_fingerprint=OUTCOME_DATA,
            outcome_available_at=early, resolved_at=early,
        )


def test_outcome_is_append_once_and_requires_known_forecast(tmp_path):
    ledger = make_ledger(tmp_path)
    with pytest.raises(ValueError, match="not present"):
        ledger.resolve_forecast(
            "missing", actual_log_return=0.01, outcome_source="feed",
            outcome_dataset_fingerprint=OUTCOME_DATA,
            outcome_available_at=END, resolved_at=END,
        )
    forecast = append_forecast(ledger)
    resolve(ledger, forecast)
    with pytest.raises(sqlite3.IntegrityError):
        resolve(ledger, forecast)


def test_report_remains_locked_until_collection_closes_and_all_resolve(tmp_path):
    ledger = make_ledger(tmp_path)
    forecast = append_forecast(ledger)
    with pytest.raises(ValueError, match="collection window has not closed"):
        ledger.evaluate_once(now=END)
    with pytest.raises(ValueError, match="every forecast must resolve"):
        ledger.evaluate_once(now=END + datetime.timedelta(days=1))
    resolve(ledger, forecast)
    with pytest.raises(ValueError, match="pre-registered minimum"):
        ledger.evaluate_once(now=END + datetime.timedelta(days=1))


def test_complete_prospective_cohort_evaluates_once_and_is_non_actionable(tmp_path):
    ledger = make_ledger(tmp_path)
    forecasts = [append_forecast(ledger, i) for i in range(30)]
    for index, forecast in enumerate(forecasts):
        resolve(ledger, forecast, actual=0.01 if index % 3 else -0.01)
    report = ledger.evaluate_once(now=END + datetime.timedelta(days=1))
    assert report["status"] == "EVALUATED_OUT_OF_SAMPLE"
    assert report["n_resolved"] == 30
    assert report["actionable"] is False
    assert report["prospective_holdout"]["forecast_count"] == 30
    assert report["prospective_holdout"]["input_sources"] == ["ramzinex-public"]
    assert report["prospective_holdout"]["outcome_sources"] == ["independent-outcome-feed"]
    assert ledger.final_report() == report
    with pytest.raises(ValueError, match="already been evaluated"):
        ledger.evaluate_once(now=END + datetime.timedelta(days=2))


def test_final_report_is_not_available_before_single_use_evaluation(tmp_path):
    ledger = make_ledger(tmp_path)
    with pytest.raises(ValueError, match="locked"):
        ledger.final_report()


def test_append_only_triggers_prevent_forecast_mutation(tmp_path):
    ledger = make_ledger(tmp_path)
    forecast = append_forecast(ledger)
    connection = sqlite3.connect(tmp_path / "prospective.sqlite3")
    try:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(
                "UPDATE forecasts SET payload='{}' WHERE forecast_id=?",
                (forecast.forecast_id,),
            )
    finally:
        connection.close()


def test_hash_chain_and_record_payload_integrity_pass_for_complete_cohort(tmp_path):
    ledger = make_ledger(tmp_path)
    forecasts = [append_forecast(ledger, i) for i in range(30)]
    for forecast in forecasts:
        resolve(ledger, forecast)
    ledger.evaluate_once(now=END + datetime.timedelta(days=1))
    integrity = ledger.verify_integrity()
    assert integrity["status"] == "PASS"
    assert integrity["events"] == 62


def test_payload_tampering_is_detected(tmp_path):
    ledger = make_ledger(tmp_path)
    forecast = append_forecast(ledger)
    connection = sqlite3.connect(tmp_path / "prospective.sqlite3")
    try:
        connection.execute("DROP TRIGGER forecasts_no_update")
        connection.execute(
            "UPDATE forecasts SET payload='{}' WHERE forecast_id=?",
            (forecast.forecast_id,),
        )
        connection.commit()
    finally:
        connection.close()
    assert ledger.verify_integrity()["status"] == "BLOCKED"


def test_pending_forecast_queue_and_next_issue_time(tmp_path):
    ledger = make_ledger(tmp_path)
    assert ledger.next_issuance_due() == START
    forecast = append_forecast(ledger)
    assert ledger.next_issuance_due() == forecast.target_at
    pending = ledger.pending_forecasts()
    assert len(pending) == 1
    assert pending[0]["forecast_id"] == forecast.forecast_id
    assert pending[0]["reference_price"] == 100.0
    resolve(ledger, forecast)
    assert ledger.pending_forecasts() == []


def test_reference_price_must_be_positive_and_finite(tmp_path):
    ledger = make_ledger(tmp_path)
    forecast = make_forecast()
    with pytest.raises(ValueError, match="reference_price"):
        ledger.record_forecast(
            forecast, config_fingerprint=CONFIG, input_source="feed",
            reference_price=0.0,
            recorded_at=forecast.issued_at + datetime.timedelta(seconds=1),
        )


def test_ledger_status_does_not_disclose_scores_before_evaluation(tmp_path):
    ledger = make_ledger(tmp_path)
    forecast = append_forecast(ledger)
    resolve(ledger, forecast)
    status = ledger.status()
    assert status["report_available"] is False
    assert status["resolved_count"] == 1
    assert "overall" not in status
