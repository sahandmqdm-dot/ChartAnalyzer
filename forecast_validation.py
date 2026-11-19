"""Point-in-time forecast records, resolution and out-of-sample evaluation.

Forecast probabilities are not assumed calibrated. This module measures their
historical reliability and distributional accuracy after the target horizon
has elapsed. It never generates trading instructions.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


class ForecastValidationError(ValueError):
    """Forecast record or evaluation data violates the research contract."""


def _aware(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ForecastValidationError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _finite(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise ForecastValidationError(f"{name} must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ForecastValidationError(f"{name} must be numeric") from exc
    if not math.isfinite(number):
        raise ForecastValidationError(f"{name} must be finite")
    return number


def _canonical_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ForecastRecord:
    forecast_id: str
    instrument_id: str
    market_type: str
    timeframe: str
    horizon_bars: int
    issued_at: datetime
    target_at: datetime
    data_cutoff_at: datetime
    model_id: str
    model_version: str
    dataset_fingerprint: str
    regime: str
    q10: float
    q50: float
    q90: float
    probability_up: float | None = None
    target_kind: str = "log_return"

    def __post_init__(self) -> None:
        required = ("forecast_id", "instrument_id", "market_type", "timeframe",
                    "model_id", "model_version", "dataset_fingerprint", "regime")
        if any(not str(getattr(self, name)).strip() for name in required):
            raise ForecastValidationError("forecast identity fields must be non-empty")
        if isinstance(self.horizon_bars, bool) or not isinstance(self.horizon_bars, int) or self.horizon_bars < 1:
            raise ForecastValidationError("horizon_bars must be a positive integer")
        issued = _aware(self.issued_at, "issued_at")
        target = _aware(self.target_at, "target_at")
        cutoff = _aware(self.data_cutoff_at, "data_cutoff_at")
        if cutoff > issued:
            raise ForecastValidationError("data_cutoff_at cannot be after issued_at (look-ahead leakage)")
        if target <= issued:
            raise ForecastValidationError("target_at must be after issued_at")
        if self.target_kind != "log_return":
            raise ForecastValidationError("only log_return targets are currently supported")
        q10, q50, q90 = (_finite(self.q10, "q10"), _finite(self.q50, "q50"), _finite(self.q90, "q90"))
        if not q10 <= q50 <= q90:
            raise ForecastValidationError("forecast quantiles must satisfy q10 <= q50 <= q90")
        probability = None if self.probability_up is None else _finite(self.probability_up, "probability_up")
        if probability is not None and not 0.0 <= probability <= 1.0:
            raise ForecastValidationError("probability_up must be in [0, 1]")
        object.__setattr__(self, "issued_at", issued)
        object.__setattr__(self, "target_at", target)
        object.__setattr__(self, "data_cutoff_at", cutoff)
        object.__setattr__(self, "q10", q10)
        object.__setattr__(self, "q50", q50)
        object.__setattr__(self, "q90", q90)
        object.__setattr__(self, "probability_up", probability)


@dataclass(frozen=True, slots=True)
class ResolvedForecast:
    forecast: ForecastRecord
    actual_log_return: float
    resolved_at: datetime

    def __post_init__(self) -> None:
        actual = _finite(self.actual_log_return, "actual_log_return")
        resolved = _aware(self.resolved_at, "resolved_at")
        if resolved < self.forecast.target_at:
            raise ForecastValidationError("forecast cannot be resolved before its target horizon")
        object.__setattr__(self, "actual_log_return", actual)
        object.__setattr__(self, "resolved_at", resolved)


def create_forecast_record(
    *,
    instrument_id: str,
    market_type: str,
    timeframe: str,
    horizon_bars: int,
    issued_at: datetime,
    target_at: datetime,
    data_cutoff_at: datetime,
    model_id: str,
    model_version: str,
    dataset_fingerprint: str,
    regime: str,
    q10: float,
    q50: float,
    q90: float,
    probability_up: float | None = None,
) -> ForecastRecord:
    """Create a stable forecast identity from its complete point-in-time payload."""
    if isinstance(horizon_bars, bool) or not isinstance(horizon_bars, int) or horizon_bars < 1:
        raise ForecastValidationError("horizon_bars must be a positive integer")
    issued, target, cutoff = (_aware(issued_at, "issued_at"),
                              _aware(target_at, "target_at"),
                              _aware(data_cutoff_at, "data_cutoff_at"))
    payload = {
        "instrument_id": str(instrument_id).strip(),
        "market_type": str(market_type).strip(),
        "timeframe": str(timeframe).strip(),
        "horizon_bars": int(horizon_bars),
        "issued_at": issued.isoformat(),
        "target_at": target.isoformat(),
        "data_cutoff_at": cutoff.isoformat(),
        "model_id": str(model_id).strip(),
        "model_version": str(model_version).strip(),
        "dataset_fingerprint": str(dataset_fingerprint).strip(),
        "regime": str(regime).strip(),
        "q10": _finite(q10, "q10"),
        "q50": _finite(q50, "q50"),
        "q90": _finite(q90, "q90"),
        "probability_up": None if probability_up is None else _finite(probability_up, "probability_up"),
        "target_kind": "log_return",
    }
    return ForecastRecord(forecast_id=_canonical_hash(payload), **payload)


def resolve_forecast(forecast: ForecastRecord, actual_log_return: float,
                     resolved_at: datetime) -> ResolvedForecast:
    """Resolve only after target_at; actuals are never used to rewrite forecasts."""
    if not isinstance(forecast, ForecastRecord):
        raise TypeError("forecast must be a ForecastRecord")
    return ResolvedForecast(forecast, actual_log_return, resolved_at)


def _pinball(actual: float, predicted: float, quantile: float) -> float:
    error = actual - predicted
    return max(quantile * error, (quantile - 1.0) * error)


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _score_group(rows: list[ResolvedForecast]) -> dict[str, Any]:
    absolute_errors = [abs(row.actual_log_return - row.forecast.q50) for row in rows]
    squared_errors = [(row.actual_log_return - row.forecast.q50) ** 2 for row in rows]
    interval_hits = [float(row.forecast.q10 <= row.actual_log_return <= row.forecast.q90) for row in rows]
    interval_widths = [row.forecast.q90 - row.forecast.q10 for row in rows]
    pinball = {
        "p10": _mean([_pinball(r.actual_log_return, r.forecast.q10, 0.10) for r in rows]),
        "p50": _mean([_pinball(r.actual_log_return, r.forecast.q50, 0.50) for r in rows]),
        "p90": _mean([_pinball(r.actual_log_return, r.forecast.q90, 0.90) for r in rows]),
    }
    probabilistic = [r for r in rows if r.forecast.probability_up is not None]
    brier = _mean([
        (r.forecast.probability_up - float(r.actual_log_return > 0)) ** 2
        for r in probabilistic
    ])
    return {
        "n": len(rows),
        "median_forecast_mae": _mean(absolute_errors),
        "median_forecast_rmse": math.sqrt(_mean(squared_errors)) if rows else None,
        "central_80_interval_coverage": _mean(interval_hits),
        "central_80_interval_mean_width": _mean(interval_widths),
        "quantile_pinball_loss": pinball,
        "directional_brier_score": brier,
        "directional_probability_n": len(probabilistic),
        "probability_status": "historically_scored_not_guaranteed_calibrated" if probabilistic else "not_provided",
    }


def calibration_report(rows: Iterable[ResolvedForecast], *, bins: int = 10,
                       min_bin_count: int = 5) -> dict[str, Any]:
    """Reliability table for P(log_return > 0), with equal-width probability bins."""
    if isinstance(bins, bool) or not 2 <= int(bins) <= 20:
        raise ValueError("bins must be between 2 and 20")
    if isinstance(min_bin_count, bool) or int(min_bin_count) < 1:
        raise ValueError("min_bin_count must be positive")
    selected = [r for r in rows if r.forecast.probability_up is not None]
    groups: list[list[ResolvedForecast]] = [[] for _ in range(int(bins))]
    for row in selected:
        p = row.forecast.probability_up
        index = min(int(p * bins), bins - 1)
        groups[index].append(row)
    table = []
    weighted_error = 0.0
    for index, group in enumerate(groups):
        if not group:
            continue
        mean_probability = _mean([float(r.forecast.probability_up) for r in group])
        observed_frequency = _mean([float(r.actual_log_return > 0) for r in group])
        gap = abs(mean_probability - observed_frequency)
        weighted_error += len(group) / max(len(selected), 1) * gap
        table.append({
            "bin": index,
            "lower_bound": index / bins,
            "upper_bound": (index + 1) / bins,
            "n": len(group),
            "mean_predicted_probability": mean_probability,
            "observed_frequency": observed_frequency,
            "absolute_calibration_error": gap,
            "sample_sufficient": len(group) >= min_bin_count,
        })
    brier = _mean([
        (float(r.forecast.probability_up) - float(r.actual_log_return > 0)) ** 2
        for r in selected
    ])
    return {
        "n": len(selected),
        "bins": int(bins),
        "minimum_bin_count": int(min_bin_count),
        "brier_score": brier,
        "expected_calibration_error": weighted_error if selected else None,
        "reliability_table": table,
        "status": "CALIBRATION_EVIDENCE_AVAILABLE" if selected else "NO_PROBABILISTIC_FORECASTS",
        "warning": "Small samples and empty bins do not establish calibration; report reliability with sample counts.",
    }


def evaluate_forecasts(rows: Iterable[ResolvedForecast], *, min_segment_count: int = 5) -> dict[str, Any]:
    """Evaluate resolved OOS forecasts overall and by model, horizon and regime."""
    records = list(rows)
    if not records:
        return {"status": "NO_RESOLVED_FORECASTS", "overall": _score_group([]),
                "calibration": calibration_report([]), "segments": {}}
    if any(not isinstance(row, ResolvedForecast) for row in records):
        raise TypeError("rows must contain ResolvedForecast values")
    ids = [row.forecast.forecast_id for row in records]
    if len(ids) != len(set(ids)):
        raise ForecastValidationError("duplicate forecast_id in evaluation cohort")
    segments: dict[str, dict[str, Any]] = {}
    dimensions = {
        "model": lambda r: r.forecast.model_id,
        "horizon_bars": lambda r: str(r.forecast.horizon_bars),
        "regime": lambda r: r.forecast.regime,
        "instrument": lambda r: r.forecast.instrument_id,
    }
    for dimension, key_fn in dimensions.items():
        buckets: dict[str, list[ResolvedForecast]] = defaultdict(list)
        for row in records:
            buckets[key_fn(row)].append(row)
        segments[dimension] = {
            key: {**_score_group(group), "sample_sufficient": len(group) >= min_segment_count}
            for key, group in sorted(buckets.items())
        }
    return {
        "schema": "forecast_evaluation_v1",
        "status": "EVALUATED_OUT_OF_SAMPLE",
        "n_resolved": len(records),
        "overall": _score_group(records),
        "calibration": calibration_report(records),
        "segments": segments,
        "research_guardrails": [
            "Only forecasts resolved at or after target_at are scored.",
            "Forecast identity includes point-in-time data cutoff and dataset fingerprint.",
            "Probability metrics are historical diagnostics, not guarantees of future calibration.",
            "Do not pool instruments, market types or horizons without an explicit comparability policy.",
            "No trade signal, position sizing or order instruction is produced.",
        ],
        "actionable": False,
    }


__all__ = [
    "ForecastRecord", "ForecastValidationError", "ResolvedForecast",
    "calibration_report", "create_forecast_record", "evaluate_forecasts",
    "resolve_forecast",
]
