# ruff: noqa: I001  # Flat-layout first-party import classification differs across Ruff versions.
import datetime
import sqlite3
import numpy as np
import pandas as pd
import pytest

import app


UTC = datetime.UTC
BASE_NOW = datetime.datetime(2031, 1, 1, 12, 2, tzinfo=UTC)


def make_frame(last_open, bars=120):
    times = np.asarray([
        (last_open - datetime.timedelta(hours=bars - 1 - index)).timestamp()
        for index in range(bars)
    ], dtype=float)
    close = np.linspace(100.0, 110.0, bars)
    return pd.DataFrame({
        "time": times,
        "open": close * 0.999,
        "high": close * 1.002,
        "low": close * 0.998,
        "close": close,
        "volume": np.full(bars, 100.0),
    })


def setup_app(monkeypatch, tmp_path, clock):
    monkeypatch.setattr(app, "PROSPECTIVE_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(app, "_prospective_now", lambda: clock["now"])
    monkeypatch.setattr(app, "conf", lambda: {"backtest": {"market_type": "spot"}})
    monkeypatch.setattr(app, "_audit_research_frame", lambda *args, **kwargs: {"valid": True, "state": "OK"})
    monkeypatch.setattr(app.PMI, "analyze", lambda *args, **kwargs: {
        "version": "5.0.0",
        "regime": {"label": "ranging"},
        "scenario_forecasts": {
            "4": {"return_quantiles": {"p10": -0.01, "p50": 0.001, "p90": 0.02}}
        },
    })


def registration_payload():
    start = BASE_NOW + datetime.timedelta(minutes=1)
    end = start + datetime.timedelta(days=10)
    return {
        "cohort_id": "BTC-SPOT-1H-4B-PROSPECTIVE",
        "source": "ramzinex",
        "symbol": "BTCUSDT",
        "tf": "1h",
        "horizon_bars": 4,
        "paths": 500,
        "n": 120,
        "collection_start": start.isoformat(),
        "collection_end": end.isoformat(),
        "min_forecasts": 30,
        "min_segment_count": 10,
    }


def test_register_prospective_cohort_freezes_server_computed_identity(monkeypatch, tmp_path):
    clock = {"now": BASE_NOW}
    setup_app(monkeypatch, tmp_path, clock)
    out = app.api_prospective_holdout_register(registration_payload())
    assert out["cohort"]["status"] == "SCHEDULED"
    assert len(out["config_fingerprint"]) == 64
    assert out["model_identity"]["model_id"] == "chartanalyzer-pmi-bootstrap"
    assert out["model_identity"]["market_type"] == "spot"


def test_tick_records_forecast_from_last_closed_candle_without_exposing_scores(monkeypatch, tmp_path):
    clock = {"now": BASE_NOW}
    setup_app(monkeypatch, tmp_path, clock)
    payload = registration_payload()
    app.api_prospective_holdout_register(payload)
    clock["now"] = BASE_NOW + datetime.timedelta(minutes=2)
    last_open = BASE_NOW.replace(minute=0, second=0, microsecond=0) - datetime.timedelta(hours=1)
    frame = make_frame(last_open)
    monkeypatch.setattr(app, "load_cached", lambda *args, **kwargs: frame)
    out = app.api_prospective_holdout_tick(payload)
    assert out["issued_forecast"] is not None
    assert out["cohort"]["forecast_count"] == 1
    assert out["report_available"] is False
    assert "overall" not in out
    assert out["issued_forecast"]["data_cutoff_at"] == BASE_NOW.replace(minute=0, second=0, microsecond=0).isoformat()


def test_tick_resolves_only_exact_future_target_candle_then_issues_next_nonoverlap(monkeypatch, tmp_path):
    clock = {"now": BASE_NOW}
    setup_app(monkeypatch, tmp_path, clock)
    payload = registration_payload()
    app.api_prospective_holdout_register(payload)
    clock["now"] = BASE_NOW + datetime.timedelta(minutes=2)
    first_last_open = BASE_NOW.replace(minute=0, second=0, microsecond=0) - datetime.timedelta(hours=1)
    frame = make_frame(first_last_open)
    monkeypatch.setattr(app, "load_cached", lambda *args, **kwargs: frame)
    first = app.api_prospective_holdout_tick(payload)
    target = datetime.datetime.fromisoformat(first["issued_forecast"]["target_at"])
    clock["now"] = target + datetime.timedelta(minutes=2)
    matured_last_open = target - datetime.timedelta(hours=1)
    matured = make_frame(matured_last_open)
    monkeypatch.setattr(app, "load_cached", lambda *args, **kwargs: matured)
    second = app.api_prospective_holdout_tick(payload)
    assert second["resolved_this_tick"] == 1, second["unresolved_due"]
    assert second["cohort"]["resolved_count"] == 1
    assert second["issued_forecast"] is not None
    assert datetime.datetime.fromisoformat(second["issued_forecast"]["issued_at"]) >= target


def test_status_and_evaluation_remain_separate_from_collection(monkeypatch, tmp_path):
    clock = {"now": BASE_NOW}
    setup_app(monkeypatch, tmp_path, clock)
    payload = registration_payload()
    app.api_prospective_holdout_register(payload)
    status = app.api_prospective_holdout_status(payload["cohort_id"])
    assert status["cohort"]["report_available"] is False
    assert status["integrity"]["status"] == "PASS"
    with pytest.raises(app.HTTPException):
        app.api_prospective_holdout_evaluate({"cohort_id": payload["cohort_id"]})


def test_config_fingerprint_binds_research_engine_revision(monkeypatch, tmp_path):
    import research_provenance

    clock = {"now": BASE_NOW}
    setup_app(monkeypatch, tmp_path, clock)
    payload = registration_payload()
    first, identity = app._prospective_identity(payload)
    assert identity["research_engine_revision"] == research_provenance.RESEARCH_ENGINE_REVISION
    monkeypatch.setattr(research_provenance, "RESEARCH_ENGINE_REVISION", "test-revision-change")
    second, _ = app._prospective_identity(payload)
    assert first != second


def test_register_rejects_collection_window_too_short_for_sample_floor(monkeypatch, tmp_path):
    clock = {"now": BASE_NOW}
    setup_app(monkeypatch, tmp_path, clock)
    payload = registration_payload()
    payload["min_forecasts"] = 200
    payload["collection_end"] = (BASE_NOW + datetime.timedelta(days=1)).isoformat()
    with pytest.raises(app.HTTPException):
        app.api_prospective_holdout_register(payload)


def test_prospective_cohort_rejects_unsupported_csv_source(monkeypatch, tmp_path):
    clock = {"now": BASE_NOW}
    setup_app(monkeypatch, tmp_path, clock)
    payload = registration_payload()
    payload["source"] = "csv"
    with pytest.raises(app.HTTPException):
        app.api_prospective_holdout_register(payload)


def test_tick_fails_closed_when_forecast_input_has_integrity_gaps(monkeypatch, tmp_path):
    clock = {"now": BASE_NOW}
    setup_app(monkeypatch, tmp_path, clock)
    payload = registration_payload()
    app.api_prospective_holdout_register(payload)
    clock["now"] = BASE_NOW + datetime.timedelta(minutes=2)
    last_open = BASE_NOW.replace(minute=0, second=0, microsecond=0) - datetime.timedelta(hours=1)
    frame = make_frame(last_open)
    monkeypatch.setattr(app, "load_cached", lambda *args, **kwargs: frame)
    monkeypatch.setattr(app, "_audit_research_frame", lambda *args, **kwargs: {
        "valid": True, "gap_count": 2, "state": "WARN"})
    with pytest.raises(app.HTTPException, match="missing candle intervals"):
        app.api_prospective_holdout_tick(payload)
    status = app.api_prospective_holdout_status(payload["cohort_id"])
    assert status["cohort"]["forecast_count"] == 0


def test_tick_fails_closed_on_off_grid_candle_intervals(monkeypatch, tmp_path):
    clock = {"now": BASE_NOW}
    setup_app(monkeypatch, tmp_path, clock)
    payload = registration_payload()
    app.api_prospective_holdout_register(payload)
    clock["now"] = BASE_NOW + datetime.timedelta(minutes=2)
    last_open = BASE_NOW.replace(minute=0, second=0, microsecond=0) - datetime.timedelta(hours=1)
    frame = make_frame(last_open)
    monkeypatch.setattr(app, "load_cached", lambda *args, **kwargs: frame)
    monkeypatch.setattr(app, "_audit_research_frame", lambda *args, **kwargs: {
        "valid": True, "gap_count": 0, "off_grid_interval_count": 1, "state": "WARN"})
    with pytest.raises(app.HTTPException, match="off the declared timeframe"):
        app.api_prospective_holdout_tick(payload)
    status = app.api_prospective_holdout_status(payload["cohort_id"])
    assert status["cohort"]["forecast_count"] == 0


def test_tick_does_not_resolve_outcome_from_invalid_ohlcv(monkeypatch, tmp_path):
    clock = {"now": BASE_NOW}
    setup_app(monkeypatch, tmp_path, clock)
    payload = registration_payload()
    app.api_prospective_holdout_register(payload)
    clock["now"] = BASE_NOW + datetime.timedelta(minutes=2)
    first_last_open = BASE_NOW.replace(minute=0, second=0, microsecond=0) - datetime.timedelta(hours=1)
    frame = make_frame(first_last_open)
    monkeypatch.setattr(app, "load_cached", lambda *args, **kwargs: frame)
    first = app.api_prospective_holdout_tick(payload)
    target = datetime.datetime.fromisoformat(first["issued_forecast"]["target_at"])
    clock["now"] = target + datetime.timedelta(minutes=2)
    matured = make_frame(target - datetime.timedelta(hours=1))
    monkeypatch.setattr(app, "load_cached", lambda *args, **kwargs: matured)
    monkeypatch.setattr(app, "_audit_research_frame", lambda *args, **kwargs: {
        "valid": False, "gap_count": 0, "state": "FAIL"})
    with pytest.raises(app.HTTPException, match="integrity audit did not pass"):
        app.api_prospective_holdout_tick(payload)
    status = app.api_prospective_holdout_status(payload["cohort_id"])
    assert status["cohort"]["resolved_count"] == 0

# ruff: noqa: I001  # Flat-layout first-party import classification differs across Ruff versions.



def test_microstructure_evidence_requires_exact_binance_spot_identity(monkeypatch, tmp_path):
    clock = {"now": BASE_NOW}
    setup_app(monkeypatch, tmp_path, clock)
    payload = registration_payload()
    payload["use_binance_microstructure"] = True
    with pytest.raises(app.HTTPException, match="requires CCXT Binance Spot BTCUSDT"):
        app.api_prospective_holdout_register(payload)


def test_tick_archives_and_binds_microstructure_evidence_before_issuance(monkeypatch, tmp_path):
    import json

    clock = {"now": BASE_NOW}
    setup_app(monkeypatch, tmp_path, clock)
    payload = registration_payload()
    payload.update({
        "source": "ccxt",
        "exchange": "binance",
        "symbol": "BTCUSDT",
        "use_binance_microstructure": True,
    })
    app.api_prospective_holdout_register(payload)
    clock["now"] = BASE_NOW + datetime.timedelta(minutes=2)
    last_open = BASE_NOW.replace(minute=0, second=0, microsecond=0) - datetime.timedelta(hours=1)
    frame = make_frame(last_open)
    monkeypatch.setattr(app, "load_cached", lambda *args, **kwargs: frame)

    def fake_provenance(identity, cutoff):
        dataset_hash = "c" * 64
        return {
            "schema": "chartanalyzer_pit_microstructure_features_v1",
            "snapshot_mode": "verified_active_partition_copy",
            "market": "spot",
            "symbol": "BTCUSDT",
            "session_id": "active-binance-session",
            "manifest_hash": "a" * 64,
            "feature_hash": "b" * 64,
            "dataset_hash": dataset_hash,
            "artifact_path": f"vault/features/{dataset_hash}.json",
            "artifact_sha256": "d" * 64,
            "decision_time": cutoff.isoformat(),
            "available_at": cutoff.isoformat(),
            "source_event_watermark": cutoff.isoformat(),
            "snapshot_created_at": (cutoff + datetime.timedelta(seconds=1)).isoformat(),
            "point_in_time_valid": True,
            "book_valid": True,
            "session_complete": False,
            "session_closed": False,
            "book_sequence_gaps": 0,
            "book_buffer_overflows": 0,
        }

    monkeypatch.setattr(app, "_prospective_binance_microstructure_provenance", fake_provenance)
    out = app.api_prospective_holdout_tick(payload)
    assert out["issued_forecast"]["microstructure_evidence"]["dataset_hash"] == "c" * 64
    ledger = app._prospective_ledger(payload["cohort_id"])
    with sqlite3.connect(ledger.path) as connection:
        row = connection.execute("SELECT payload FROM forecasts").fetchone()
    stored = json.loads(row[0])["microstructure_provenance"]
    assert stored["feature_hash"] == "b" * 64
    assert stored["artifact_path"] == f"vault/features/{'c' * 64}.json"


def test_microstructure_snapshot_failure_blocks_forecast_recording(monkeypatch, tmp_path):
    clock = {"now": BASE_NOW}
    setup_app(monkeypatch, tmp_path, clock)
    payload = registration_payload()
    payload.update({
        "source": "ccxt",
        "exchange": "binance",
        "symbol": "BTCUSDT",
        "use_binance_microstructure": True,
    })
    app.api_prospective_holdout_register(payload)
    clock["now"] = BASE_NOW + datetime.timedelta(minutes=2)
    last_open = BASE_NOW.replace(minute=0, second=0, microsecond=0) - datetime.timedelta(hours=1)
    monkeypatch.setattr(app, "load_cached", lambda *args, **kwargs: make_frame(last_open))

    def unavailable(*args, **kwargs):
        raise RuntimeError("no active Binance PIT session")

    monkeypatch.setattr(app, "_prospective_binance_microstructure_provenance", unavailable)
    with pytest.raises(app.HTTPException, match="no active Binance PIT session"):
        app.api_prospective_holdout_tick(payload)
    status = app.api_prospective_holdout_status(payload["cohort_id"])
    assert status["cohort"]["forecast_count"] == 0
