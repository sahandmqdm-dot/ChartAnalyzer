"""Prospective, append-only holdout ledger for genuinely future forecasts.

This is deliberately separate from historical backtest holdouts. A cohort is
registered before its collection window starts; forecasts are persisted before
their target horizon; outcomes can be appended only after the target resolves.
The final report is revealed once, after the window closes and every forecast
has a recorded outcome. This is an operational tamper-evident ledger, not a
cryptographic boundary against a filesystem administrator.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from forecast_validation import (
    ForecastRecord,
    ForecastValidationError,
    ResolvedForecast,
    evaluate_forecasts,
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS cohort (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    payload TEXT NOT NULL,
    event_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS forecasts (
    forecast_id TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    event_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS outcomes (
    forecast_id TEXT PRIMARY KEY REFERENCES forecasts(forecast_id),
    payload TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    event_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS evaluations (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    payload TEXT NOT NULL,
    evaluated_at TEXT NOT NULL,
    event_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ledger_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    event_id TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    previous_hash TEXT NOT NULL,
    event_hash TEXT NOT NULL UNIQUE
);
CREATE TRIGGER IF NOT EXISTS cohort_no_update BEFORE UPDATE ON cohort
BEGIN SELECT RAISE(ABORT, 'cohort is immutable'); END;
CREATE TRIGGER IF NOT EXISTS cohort_no_delete BEFORE DELETE ON cohort
BEGIN SELECT RAISE(ABORT, 'cohort is immutable'); END;
CREATE TRIGGER IF NOT EXISTS forecasts_no_update BEFORE UPDATE ON forecasts
BEGIN SELECT RAISE(ABORT, 'forecasts are append-only'); END;
CREATE TRIGGER IF NOT EXISTS forecasts_no_delete BEFORE DELETE ON forecasts
BEGIN SELECT RAISE(ABORT, 'forecasts are append-only'); END;
CREATE TRIGGER IF NOT EXISTS outcomes_no_update BEFORE UPDATE ON outcomes
BEGIN SELECT RAISE(ABORT, 'outcomes are append-only'); END;
CREATE TRIGGER IF NOT EXISTS outcomes_no_delete BEFORE DELETE ON outcomes
BEGIN SELECT RAISE(ABORT, 'outcomes are append-only'); END;
CREATE TRIGGER IF NOT EXISTS evaluations_no_update BEFORE UPDATE ON evaluations
BEGIN SELECT RAISE(ABORT, 'evaluation is immutable'); END;
CREATE TRIGGER IF NOT EXISTS evaluations_no_delete BEFORE DELETE ON evaluations
BEGIN SELECT RAISE(ABORT, 'evaluation is immutable'); END;
CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON ledger_events
BEGIN SELECT RAISE(ABORT, 'ledger events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON ledger_events
BEGIN SELECT RAISE(ABORT, 'ledger events are append-only'); END;
"""


def _utc(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _iso(value: datetime) -> str:
    return _utc(value, "timestamp").isoformat()


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(UTC)


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha(value: str, name: str) -> str:
    text = str(value or "").lower()
    if not _SHA256.fullmatch(text):
        raise ValueError(f"{name} must be a 64-character SHA-256 hex digest")
    return text


def _forecast_payload(forecast: ForecastRecord) -> dict[str, Any]:
    payload = asdict(forecast)
    for key in ("issued_at", "target_at", "data_cutoff_at"):
        payload[key] = _iso(payload[key])
    return payload


def _forecast_from_payload(payload: dict[str, Any]) -> ForecastRecord:
    data = dict(payload)
    for key in ("issued_at", "target_at", "data_cutoff_at"):
        data[key] = _parse(data[key])
    return ForecastRecord(**data)


class ProspectiveHoldoutLedger:
    """Single-cohort future holdout with immutable forecasts and one final report."""

    def __init__(self, path: str | Path = "prospective_holdout.sqlite3"):
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.path), timeout=20, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=20000")
        connection.executescript(_SCHEMA)
        return connection

    @staticmethod
    def _begin(connection: sqlite3.Connection) -> None:
        connection.execute("BEGIN IMMEDIATE")

    @staticmethod
    def _finish(connection: sqlite3.Connection, error: bool = False) -> None:
        try:
            connection.rollback() if error else connection.commit()
        finally:
            connection.close()

    @staticmethod
    def _event(connection: sqlite3.Connection, event_type: str, event_id: str,
               payload: dict[str, Any]) -> str:
        payload_hash = _hash(_json(payload))
        row = connection.execute(
            "SELECT event_hash FROM ledger_events ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        previous = row["event_hash"] if row else "0" * 64
        event_hash = _hash(f"{previous}|{event_type}|{event_id}|{payload_hash}")
        connection.execute(
            "INSERT INTO ledger_events(event_type,event_id,payload_hash,previous_hash,event_hash) "
            "VALUES(?,?,?,?,?)",
            (event_type, event_id, payload_hash, previous, event_hash),
        )
        return event_hash

    def register_cohort(
        self, *, cohort_id: str, instrument_id: str, market_type: str,
        timeframe: str, horizon_bars: int, model_id: str, model_version: str,
        config_fingerprint: str, collection_start: datetime, collection_end: datetime,
        min_forecasts: int = 200, now: datetime | None = None,
    ) -> dict[str, Any]:
        """Freeze the exact model/config before a future collection window begins."""
        current = _utc(now or datetime.now(UTC), "now")
        start, end = _utc(collection_start, "collection_start"), _utc(collection_end, "collection_end")
        if start <= current:
            raise ValueError("prospective collection_start must be in the future")
        if end <= start:
            raise ValueError("collection_end must be after collection_start")
        if isinstance(horizon_bars, bool) or not isinstance(horizon_bars, int) or horizon_bars < 1:
            raise ValueError("horizon_bars must be a positive integer")
        if isinstance(min_forecasts, bool) or not isinstance(min_forecasts, int) or min_forecasts < 30:
            raise ValueError("min_forecasts must be an integer >= 30")
        fields = {
            "cohort_id": cohort_id, "instrument_id": instrument_id,
            "market_type": market_type, "timeframe": timeframe,
            "model_id": model_id, "model_version": model_version,
        }
        for name, value in fields.items():
            if not isinstance(value, str) or not value.strip() or len(value) > 128:
                raise ValueError(f"{name} must be a non-empty string of at most 128 characters")
        payload = {
            **fields, "horizon_bars": horizon_bars,
            "config_fingerprint": _sha(config_fingerprint, "config_fingerprint"),
            "collection_start": _iso(start), "collection_end": _iso(end),
            "min_forecasts": min_forecasts, "registered_at": _iso(current),
            "policy": "prospective_only_non_overlapping_single_cohort",
        }
        connection = self._connect()
        try:
            self._begin(connection)
            if connection.execute("SELECT 1 FROM cohort").fetchone():
                raise ValueError("this ledger already has a cohort; use a fresh ledger for a new holdout")
            event_hash = self._event(connection, "COHORT_REGISTERED", cohort_id, payload)
            connection.execute(
                "INSERT INTO cohort(singleton,payload,event_hash) VALUES(1,?,?)",
                (_json(payload), event_hash),
            )
            self._finish(connection)
        except Exception:
            self._finish(connection, error=True)
            raise
        return self.status()

    def record_forecast(
        self, forecast: ForecastRecord, *, config_fingerprint: str,
        input_source: str, recorded_at: datetime | None = None,
    ) -> dict[str, Any]:
        """Append a forecast before its target time; never overwrite a prior forecast."""
        if not isinstance(forecast, ForecastRecord):
            raise TypeError("forecast must be a ForecastRecord")
        current = _utc(recorded_at or datetime.now(UTC), "recorded_at")
        config_hash = _sha(config_fingerprint, "config_fingerprint")
        if not isinstance(input_source, str) or not input_source.strip() or len(input_source) > 128:
            raise ValueError("input_source must be a non-empty string")
        if not _SHA256.fullmatch(forecast.dataset_fingerprint.lower()):
            raise ValueError("forecast dataset_fingerprint must be a SHA-256 digest")
        if current < forecast.issued_at or current >= forecast.target_at:
            raise ValueError("forecast must be recorded at/after issuance and strictly before its target")
        payload = {
            "forecast": _forecast_payload(forecast),
            "config_fingerprint": config_hash,
            "input_source": input_source.strip(),
            "recorded_at": _iso(current),
        }
        connection = self._connect()
        try:
            self._begin(connection)
            row = connection.execute("SELECT payload FROM cohort WHERE singleton=1").fetchone()
            if row is None:
                raise ValueError("prospective holdout cohort has not been registered")
            cohort = json.loads(row["payload"])
            if config_hash != cohort["config_fingerprint"]:
                raise ValueError("forecast config fingerprint differs from frozen cohort")
            checks = {
                "instrument": forecast.instrument_id == cohort["instrument_id"],
                "market_type": forecast.market_type == cohort["market_type"],
                "timeframe": forecast.timeframe == cohort["timeframe"],
                "horizon": forecast.horizon_bars == cohort["horizon_bars"],
                "model_id": forecast.model_id == cohort["model_id"],
                "model_version": forecast.model_version == cohort["model_version"],
                "issued_in_collection_window": (
                    _parse(cohort["collection_start"]) <= forecast.issued_at <= _parse(cohort["collection_end"])
                ),
            }
            failed = [key for key, passed in checks.items() if not passed]
            if failed:
                raise ValueError("forecast does not match frozen cohort: " + ", ".join(failed))
            prior = connection.execute(
                "SELECT payload FROM forecasts ORDER BY recorded_at DESC LIMIT 1"
            ).fetchone()
            if prior:
                previous = _forecast_from_payload(json.loads(prior["payload"])["forecast"])
                if forecast.issued_at < previous.target_at:
                    raise ValueError("overlapping forecast horizons are not allowed in this independent cohort")
            event_hash = self._event(connection, "FORECAST_RECORDED", forecast.forecast_id, payload)
            connection.execute(
                "INSERT INTO forecasts(forecast_id,payload,recorded_at,event_hash) VALUES(?,?,?,?)",
                (forecast.forecast_id, _json(payload), _iso(current), event_hash),
            )
            self._finish(connection)
        except Exception:
            self._finish(connection, error=True)
            raise
        return self.status()

    def resolve_forecast(
        self, forecast_id: str, *, actual_log_return: float, outcome_source: str,
        outcome_dataset_fingerprint: str, outcome_available_at: datetime,
        resolved_at: datetime | None = None,
    ) -> dict[str, Any]:
        """Append realized outcome only after the forecast horizon and data availability."""
        current = _utc(resolved_at or datetime.now(UTC), "resolved_at")
        available = _utc(outcome_available_at, "outcome_available_at")
        actual = float(actual_log_return)
        if not math.isfinite(actual):
            raise ValueError("actual_log_return must be finite")
        if not isinstance(outcome_source, str) or not outcome_source.strip() or len(outcome_source) > 128:
            raise ValueError("outcome_source must be a non-empty string")
        fingerprint = _sha(outcome_dataset_fingerprint, "outcome_dataset_fingerprint")
        connection = self._connect()
        try:
            self._begin(connection)
            row = connection.execute(
                "SELECT payload FROM forecasts WHERE forecast_id=?", (str(forecast_id),)
            ).fetchone()
            if row is None:
                raise ValueError("forecast_id is not present in this holdout ledger")
            forecast = _forecast_from_payload(json.loads(row["payload"])["forecast"])
            if current < forecast.target_at or available < forecast.target_at or available > current:
                raise ValueError("outcome can only be recorded after target_at and after outcome data is available")
            payload = {
                "forecast_id": str(forecast_id), "actual_log_return": actual,
                "outcome_source": outcome_source.strip(),
                "outcome_dataset_fingerprint": fingerprint,
                "outcome_available_at": _iso(available), "resolved_at": _iso(current),
            }
            event_hash = self._event(connection, "OUTCOME_RESOLVED", str(forecast_id), payload)
            connection.execute(
                "INSERT INTO outcomes(forecast_id,payload,recorded_at,event_hash) VALUES(?,?,?,?)",
                (str(forecast_id), _json(payload), _iso(current), event_hash),
            )
            self._finish(connection)
        except Exception:
            self._finish(connection, error=True)
            raise
        return self.status()

    def status(self) -> dict[str, Any]:
        connection = self._connect()
        try:
            cohort_row = connection.execute("SELECT payload FROM cohort WHERE singleton=1").fetchone()
            if cohort_row is None:
                return {"status": "UNCONFIGURED", "locked": True, "forecast_count": 0, "resolved_count": 0}
            cohort = json.loads(cohort_row["payload"])
            forecast_count = int(connection.execute("SELECT COUNT(*) FROM forecasts").fetchone()[0])
            resolved_count = int(connection.execute("SELECT COUNT(*) FROM outcomes").fetchone()[0])
            evaluated = connection.execute("SELECT 1 FROM evaluations WHERE singleton=1").fetchone() is not None
            now = datetime.now(UTC)
            if evaluated:
                state = "EVALUATED_ONCE"
            elif now < _parse(cohort["collection_start"]):
                state = "SCHEDULED"
            elif now <= _parse(cohort["collection_end"]):
                state = "COLLECTING"
            else:
                state = "AWAITING_RESOLUTION"
            return {
                "status": state, "locked": not evaluated,
                "cohort_id": cohort["cohort_id"], "instrument_id": cohort["instrument_id"],
                "timeframe": cohort["timeframe"], "horizon_bars": cohort["horizon_bars"],
                "collection_start": cohort["collection_start"], "collection_end": cohort["collection_end"],
                "min_forecasts": cohort["min_forecasts"], "forecast_count": forecast_count,
                "resolved_count": resolved_count, "unresolved_count": forecast_count - resolved_count,
                "report_available": evaluated,
            }
        finally:
            connection.close()

    def evaluate_once(self, *, now: datetime | None = None, min_segment_count: int = 30) -> dict[str, Any]:
        """Reveal and permanently store one evaluation after the cohort fully resolves."""
        current = _utc(now or datetime.now(UTC), "now")
        connection = self._connect()
        try:
            self._begin(connection)
            cohort_row = connection.execute("SELECT payload FROM cohort WHERE singleton=1").fetchone()
            if cohort_row is None:
                raise ValueError("prospective holdout cohort has not been registered")
            cohort = json.loads(cohort_row["payload"])
            if current <= _parse(cohort["collection_end"]):
                raise ValueError("collection window has not closed")
            existing = connection.execute("SELECT payload FROM evaluations WHERE singleton=1").fetchone()
            if existing:
                raise ValueError("final prospective holdout has already been evaluated")
            forecast_rows = connection.execute("SELECT payload FROM forecasts ORDER BY recorded_at").fetchall()
            outcome_rows = connection.execute("SELECT forecast_id,payload FROM outcomes").fetchall()
            outcomes = {row["forecast_id"]: json.loads(row["payload"]) for row in outcome_rows}
            if len(forecast_rows) < int(cohort["min_forecasts"]):
                raise ValueError("insufficient prospective forecasts for the pre-registered minimum")
            if len(outcomes) != len(forecast_rows):
                raise ValueError("every forecast must resolve before the final report is opened")
            resolved = []
            for row in forecast_rows:
                payload = json.loads(row["payload"])
                forecast = _forecast_from_payload(payload["forecast"])
                outcome = outcomes[forecast.forecast_id]
                resolved.append(ResolvedForecast(
                    forecast=forecast,
                    actual_log_return=float(outcome["actual_log_return"]),
                    resolved_at=_parse(outcome["resolved_at"]),
                ))
            report = evaluate_forecasts(resolved, min_segment_count=min_segment_count)
            report = {
                **report,
                "prospective_holdout": {
                    "cohort_id": cohort["cohort_id"],
                    "config_fingerprint": cohort["config_fingerprint"],
                    "collection_start": cohort["collection_start"],
                    "collection_end": cohort["collection_end"],
                    "forecast_count": len(forecast_rows),
                    "resolved_count": len(outcomes),
                    "input_sources": sorted({json.loads(r["payload"])["input_source"] for r in forecast_rows}),
                    "outcome_sources": sorted({o["outcome_source"] for o in outcomes.values()}),
                    "ledger_integrity": "verify_with_verify_integrity",
                    "policy": "one_time_final_report_no_retraining_or_tuning_on_this_sample",
                },
            }
            payload = {"report": report, "evaluated_at": _iso(current)}
            event_hash = self._event(connection, "HOLDOUT_EVALUATED", cohort["cohort_id"], payload)
            connection.execute(
                "INSERT INTO evaluations(singleton,payload,evaluated_at,event_hash) VALUES(1,?,?,?)",
                (_json(payload), _iso(current), event_hash),
            )
            self._finish(connection)
            return report
        except Exception:
            self._finish(connection, error=True)
            raise

    def final_report(self) -> dict[str, Any]:
        connection = self._connect()
        try:
            row = connection.execute("SELECT payload FROM evaluations WHERE singleton=1").fetchone()
            if row is None:
                raise ValueError("final report is locked until the one-time evaluation is completed")
            return json.loads(row["payload"])["report"]
        finally:
            connection.close()

    def verify_integrity(self) -> dict[str, Any]:
        """Verify the append-only event hash chain and event-to-record references."""
        connection = self._connect()
        try:
            events = connection.execute("SELECT * FROM ledger_events ORDER BY seq").fetchall()
            previous = "0" * 64
            valid = True
            for event in events:
                expected = _hash(
                    f"{previous}|{event['event_type']}|{event['event_id']}|{event['payload_hash']}"
                )
                if event["previous_hash"] != previous or event["event_hash"] != expected:
                    valid = False
                    break
                previous = event["event_hash"]
            refs = []
            for table in ("cohort", "forecasts", "outcomes", "evaluations"):
                refs.extend(row[0] for row in connection.execute(f"SELECT event_hash FROM {table}"))
            event_hashes = {row["event_hash"] for row in events}
            valid = valid and len(refs) == len(event_hashes) and set(refs) == event_hashes
            return {
                "status": "PASS" if valid else "BLOCKED",
                "events": len(events), "last_event_hash": previous if events else None,
                "policy": "Hash-chain tampering is detectable; this is not protection against an administrator who can rewrite the database and recompute the chain.",
            }
        finally:
            connection.close()
