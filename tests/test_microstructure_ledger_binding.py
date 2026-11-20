import datetime
import json
import sqlite3

import pytest

from forecast_validation import create_forecast_record
from prospective_holdout import ProspectiveHoldoutLedger


UTC = datetime.UTC
NOW = datetime.datetime(2030, 1, 1, tzinfo=UTC)
START = NOW + datetime.timedelta(days=1)
END = START + datetime.timedelta(days=7)
CONFIG = "c" * 64
BASELINES = {
    "zero_return_point": {"q10": 0.0, "q50": 0.0, "q90": 0.0},
    "historical_unconditional": {"q10": -0.02, "q50": 0.001, "q90": 0.025},
    "ewma_gaussian": {"q10": -0.03, "q50": 0.0, "q90": 0.03},
}


def _ledger(tmp_path):
    ledger = ProspectiveHoldoutLedger(tmp_path / "ledger.sqlite3")
    ledger.register_cohort(
        cohort_id="BTC-SPOT-PIT-2030",
        instrument_id="BTCUSDT",
        market_type="spot",
        timeframe="1h",
        horizon_bars=4,
        model_id="pmi",
        model_version="1.0",
        config_fingerprint=CONFIG,
        collection_start=START,
        collection_end=END,
        min_forecasts=30,
        now=NOW,
    )
    return ledger


def _forecast():
    cutoff = START
    return create_forecast_record(
        instrument_id="BTCUSDT",
        market_type="spot",
        timeframe="1h",
        horizon_bars=4,
        issued_at=cutoff + datetime.timedelta(minutes=1),
        target_at=cutoff + datetime.timedelta(hours=4),
        data_cutoff_at=cutoff,
        model_id="pmi",
        model_version="1.0",
        dataset_fingerprint="d" * 64,
        regime="unknown",
        q10=-0.02,
        q50=0.0,
        q90=0.02,
        probability_up=None,
    )


def _provenance(forecast, **overrides):
    value = {
        "schema": "chartanalyzer_pit_microstructure_features_v1",
        "snapshot_mode": "verified_active_partition_copy",
        "market": "spot",
        "symbol": "BTCUSDT",
        "session_id": "session-2030",
        "manifest_hash": "a" * 64,
        "feature_hash": "b" * 64,
        "dataset_hash": "c" * 64,
        "artifact_path": "vault/features/" + "c" * 64 + ".json",
        "artifact_sha256": "d" * 64,
        "decision_time": forecast.data_cutoff_at.isoformat(),
        "available_at": forecast.data_cutoff_at.isoformat(),
        "source_event_watermark": forecast.data_cutoff_at.isoformat(),
        "snapshot_created_at": forecast.issued_at.isoformat(),
        "point_in_time_valid": True,
        "book_valid": True,
        "session_complete": False,
        "session_closed": False,
        "book_sequence_gaps": 0,
        "book_buffer_overflows": 0,
    }
    value.update(overrides)
    return value


def _record(ledger, forecast, provenance):
    return ledger.record_forecast(
        forecast,
        config_fingerprint=CONFIG,
        input_source="ccxt:binance",
        reference_price=90000.0,
        baseline_quantiles=BASELINES,
        recorded_at=forecast.issued_at + datetime.timedelta(seconds=1),
        microstructure_provenance=provenance,
    )


def test_forecast_ledger_persists_microstructure_hash_lineage(tmp_path):
    ledger = _ledger(tmp_path)
    forecast = _forecast()
    _record(ledger, forecast, _provenance(forecast))

    with sqlite3.connect(ledger.path) as connection:
        row = connection.execute(
            "SELECT payload FROM forecasts WHERE forecast_id=?",
            (forecast.forecast_id,),
        ).fetchone()
    payload = json.loads(row[0])
    bound = payload["microstructure_provenance"]
    assert bound["manifest_hash"] == "a" * 64
    assert bound["feature_hash"] == "b" * 64
    assert bound["dataset_hash"] == "c" * 64
    assert bound["artifact_path"] == "vault/features/" + "c" * 64 + ".json"
    assert bound["artifact_sha256"] == "d" * 64
    assert bound["session_complete"] is False
    assert ledger.verify_integrity()["status"] == "PASS"


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"symbol": "ETHUSDT"}, "symbol does not match"),
        ({"point_in_time_valid": False}, "not point-in-time valid"),
        ({"decision_time": (START + datetime.timedelta(seconds=2)).isoformat()}, "decision_time must match"),
        ({"available_at": (START + datetime.timedelta(seconds=2)).isoformat()}, "not available by"),
        ({"source_event_watermark": (START + datetime.timedelta(seconds=2)).isoformat()}, "watermark exceeds"),
        ({"book_sequence_gaps": -1}, "non-negative integer"),
        ({"feature_hash": "not-a-hash"}, "SHA-256"),
    ],
)
def test_forecast_rejects_invalid_microstructure_provenance(tmp_path, overrides, message):
    ledger = _ledger(tmp_path)
    forecast = _forecast()
    with pytest.raises(ValueError, match=message):
        _record(ledger, forecast, _provenance(forecast, **overrides))
