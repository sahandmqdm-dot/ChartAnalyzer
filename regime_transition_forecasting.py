"""Causal Markov regime-transition research with delayed OOS scoring.

This module estimates state-transition distributions from historical regime
labels. It does not claim calibrated probabilities and never emits trade
instructions.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import pairwise
from typing import Any

_EPSILON = 1e-12


class RegimeForecastError(ValueError):
    """Invalid regime training, forecast or resolution data."""


def _aware(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise RegimeForecastError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _digest(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class RegimeTransitionModel:
    model_id: str
    states: tuple[str, ...]
    transition_probabilities: tuple[tuple[float, ...], ...]
    transition_counts: tuple[tuple[int, ...], ...]
    row_counts: tuple[int, ...]
    training_end_at: datetime
    dataset_fingerprint: str
    smoothing_alpha: float
    min_row_count: int

    def __post_init__(self) -> None:
        if not self.model_id or not self.dataset_fingerprint:
            raise RegimeForecastError("model_id and dataset_fingerprint are required")
        if len(self.states) < 2 or len(set(self.states)) != len(self.states):
            raise RegimeForecastError("states must contain at least two unique labels")
        size = len(self.states)
        if len(self.transition_probabilities) != size or len(self.transition_counts) != size:
            raise RegimeForecastError("transition matrix dimensions do not match states")
        if len(self.row_counts) != size:
            raise RegimeForecastError("row_counts dimensions do not match states")
        for row in self.transition_probabilities:
            if len(row) != size or any(not math.isfinite(x) or x < 0 for x in row):
                raise RegimeForecastError("invalid transition probability matrix")
            if not math.isclose(sum(row), 1.0, rel_tol=1e-9, abs_tol=1e-9):
                raise RegimeForecastError("each transition probability row must sum to one")
        object.__setattr__(self, "training_end_at", _aware(self.training_end_at, "training_end_at"))


def fit_regime_transition_model(
    regimes: Sequence[str],
    *,
    states: Sequence[str],
    training_end_at: datetime,
    as_of: datetime,
    dataset_fingerprint: str,
    smoothing_alpha: float = 1.0,
    min_row_count: int = 20,
) -> RegimeTransitionModel:
    """Fit a smoothed transition matrix using chronological labels through as_of."""
    end, current = _aware(training_end_at, "training_end_at"), _aware(as_of, "as_of")
    if end > current:
        raise RegimeForecastError("training_end_at cannot be after as_of")
    labels = tuple(str(value).strip() for value in regimes)
    state_names = tuple(str(value).strip() for value in states)
    if len(labels) < 2:
        raise RegimeForecastError("at least two historical regime labels are required")
    if len(state_names) < 2 or any(not value for value in state_names) or len(set(state_names)) != len(state_names):
        raise RegimeForecastError("states must contain at least two unique non-empty labels")
    if any(label not in state_names for label in labels):
        raise RegimeForecastError("training regimes contain an unknown state")
    alpha = float(smoothing_alpha)
    if not math.isfinite(alpha) or alpha <= 0:
        raise RegimeForecastError("smoothing_alpha must be finite and positive")
    if isinstance(min_row_count, bool) or not isinstance(min_row_count, int) or min_row_count < 1:
        raise RegimeForecastError("min_row_count must be a positive integer")
    if not str(dataset_fingerprint).strip():
        raise RegimeForecastError("dataset_fingerprint is required")

    index = {state: i for i, state in enumerate(state_names)}
    size = len(state_names)
    counts = [[0 for _ in range(size)] for _ in range(size)]
    for previous, following in pairwise(labels):
        counts[index[previous]][index[following]] += 1
    row_counts = [sum(row) for row in counts]
    matrix = [
        tuple((count + alpha) / (row_counts[i] + alpha * size) for count in counts[i])
        for i in range(size)
    ]
    payload = {
        "states": state_names,
        "counts": counts,
        "training_end_at": end.isoformat(),
        "dataset_fingerprint": str(dataset_fingerprint).strip(),
        "smoothing_alpha": alpha,
        "min_row_count": min_row_count,
    }
    return RegimeTransitionModel(
        model_id=_digest(payload), states=state_names,
        transition_probabilities=tuple(matrix),
        transition_counts=tuple(tuple(row) for row in counts),
        row_counts=tuple(row_counts), training_end_at=end,
        dataset_fingerprint=str(dataset_fingerprint).strip(),
        smoothing_alpha=alpha, min_row_count=min_row_count,
    )


def forecast_regime_transitions(
    model: RegimeTransitionModel,
    current_regime: str,
    *,
    horizons: Iterable[int] = (1, 3, 6, 12),
    issued_at: datetime,
) -> dict[str, Any]:
    """Project regime-state distributions using the fitted transition matrix."""
    if not isinstance(model, RegimeTransitionModel):
        raise TypeError("model must be a RegimeTransitionModel")
    issued = _aware(issued_at, "issued_at")
    if issued < model.training_end_at:
        raise RegimeForecastError("forecast issue time precedes model training cutoff")
    if current_regime not in model.states:
        raise RegimeForecastError("current_regime is not present in the model")
    requested = list(horizons)
    if not requested or any(isinstance(h, bool) or not isinstance(h, int) or not 1 <= h <= 500 for h in requested):
        raise RegimeForecastError("horizons must contain positive integers <= 500")
    if len(set(requested)) != len(requested):
        raise RegimeForecastError("horizons must be unique")
    matrix = model.transition_probabilities
    state_index = {state: i for i, state in enumerate(model.states)}
    vector = [0.0] * len(model.states)
    vector[state_index[current_regime]] = 1.0
    forecasts: dict[str, Any] = {}
    max_horizon = max(requested)
    for step in range(1, max_horizon + 1):
        vector = [
            sum(vector[i] * matrix[i][j] for i in range(len(model.states)))
            for j in range(len(model.states))
        ]
        if step not in requested:
            continue
        reachable_low_support = [
            model.states[i] for i, probability in enumerate(vector)
            if probability > _EPSILON and model.row_counts[i] < model.min_row_count
        ]
        source_supported = model.row_counts[state_index[current_regime]] >= model.min_row_count
        probabilities = dict(zip(model.states, vector))
        entropy = -sum(p * math.log(p) for p in vector if p > _EPSILON)
        forecasts[str(step)] = {
            "horizon_bars": step,
            "probabilities": probabilities if source_supported else None,
            "transition_entropy_nats": entropy,
            "source_transition_count": model.row_counts[state_index[current_regime]],
            "low_support_reachable_states": reachable_low_support,
            "status": (
                "INSUFFICIENT_SOURCE_HISTORY" if not source_supported
                else "LOW_SUPPORT_EXPLORATORY" if reachable_low_support
                else "EXPLORATORY_UNCALIBRATED"
            ),
            "probability_semantics": "smoothed Markov transition estimate; not calibrated until chronologically tested",
        }
    return {
        "schema": "regime_transition_forecast_v1",
        "model_id": model.model_id,
        "dataset_fingerprint": model.dataset_fingerprint,
        "training_end_at": model.training_end_at.isoformat(),
        "issued_at": issued.isoformat(),
        "current_regime": current_regime,
        "states": list(model.states),
        "forecasts": forecasts,
        "actionable": False,
        "limitations": [
            "First-order Markov assumption may not capture regime duration or hidden market drivers.",
            "Transition probabilities depend on the training window and smoothing prior.",
            "Low transition counts are flagged and must not be treated as reliable probabilities.",
            "Forecasts require chronological out-of-sample evaluation before production use.",
        ],
    }


@dataclass(frozen=True, slots=True)
class RegimeForecastRecord:
    forecast_id: str
    instrument_id: str
    market_type: str
    timeframe: str
    horizon_bars: int
    model_id: str
    dataset_fingerprint: str
    current_regime: str
    probabilities: tuple[tuple[str, float], ...]
    issued_at: datetime
    target_at: datetime
    data_cutoff_at: datetime

    def __post_init__(self) -> None:
        if any(not str(getattr(self, name)).strip() for name in (
            "forecast_id", "instrument_id", "market_type", "timeframe",
            "model_id", "dataset_fingerprint", "current_regime",
        )):
            raise RegimeForecastError("forecast identity fields are required")
        if isinstance(self.horizon_bars, bool) or not isinstance(self.horizon_bars, int) or self.horizon_bars < 1:
            raise RegimeForecastError("horizon_bars must be a positive integer")
        issued, target, cutoff = (
            _aware(self.issued_at, "issued_at"),
            _aware(self.target_at, "target_at"),
            _aware(self.data_cutoff_at, "data_cutoff_at"),
        )
        if cutoff > issued:
            raise RegimeForecastError("data_cutoff_at cannot be after issued_at")
        if target <= issued:
            raise RegimeForecastError("target_at must be after issued_at")
        normalized_pairs = []
        for state, probability in self.probabilities:
            if isinstance(probability, bool):
                raise RegimeForecastError("state probabilities must be numeric")
            try:
                numeric_probability = float(probability)
            except (TypeError, ValueError, OverflowError) as exc:
                raise RegimeForecastError("state probabilities must be numeric") from exc
            normalized_pairs.append((str(state), numeric_probability))
        pairs = tuple(normalized_pairs)
        if len(pairs) < 2 or len({state for state, _ in pairs}) != len(pairs):
            raise RegimeForecastError("probabilities must cover at least two unique states")
        if any(not math.isfinite(p) or p < 0 or p > 1 for _, p in pairs):
            raise RegimeForecastError("state probabilities must be in [0, 1]")
        if not math.isclose(sum(p for _, p in pairs), 1.0, rel_tol=1e-7, abs_tol=1e-7):
            raise RegimeForecastError("state probabilities must sum to one")
        object.__setattr__(self, "issued_at", issued)
        object.__setattr__(self, "target_at", target)
        object.__setattr__(self, "data_cutoff_at", cutoff)
        object.__setattr__(self, "probabilities", tuple(sorted(pairs)))


@dataclass(frozen=True, slots=True)
class ResolvedRegimeForecast:
    forecast: RegimeForecastRecord
    actual_regime: str
    resolved_at: datetime

    def __post_init__(self) -> None:
        if self.actual_regime not in {state for state, _ in self.forecast.probabilities}:
            raise RegimeForecastError("actual_regime is not covered by forecast states")
        resolved = _aware(self.resolved_at, "resolved_at")
        if resolved < self.forecast.target_at:
            raise RegimeForecastError("regime forecast cannot resolve before target_at")
        object.__setattr__(self, "resolved_at", resolved)


def create_regime_forecast_record(
    *,
    instrument_id: str,
    market_type: str,
    timeframe: str,
    horizon_bars: int,
    model_id: str,
    dataset_fingerprint: str,
    current_regime: str,
    probabilities: Mapping[str, float],
    issued_at: datetime,
    target_at: datetime,
    data_cutoff_at: datetime,
) -> RegimeForecastRecord:
    issued, target, cutoff = (
        _aware(issued_at, "issued_at"),
        _aware(target_at, "target_at"),
        _aware(data_cutoff_at, "data_cutoff_at"),
    )
    normalized_pairs = []
    for state, probability in probabilities.items():
        if isinstance(probability, bool):
            raise RegimeForecastError("state probabilities must be numeric")
        try:
            numeric_probability = float(probability)
        except (TypeError, ValueError, OverflowError) as exc:
            raise RegimeForecastError("state probabilities must be numeric") from exc
        normalized_pairs.append((str(state), numeric_probability))
    pairs = tuple(sorted(normalized_pairs))
    payload = {
        "instrument_id": instrument_id, "market_type": market_type,
        "timeframe": timeframe, "horizon_bars": horizon_bars,
        "model_id": model_id, "dataset_fingerprint": dataset_fingerprint,
        "current_regime": current_regime, "probabilities": pairs,
        "issued_at": issued.isoformat(), "target_at": target.isoformat(),
        "data_cutoff_at": cutoff.isoformat(),
    }
    return RegimeForecastRecord(
        forecast_id=_digest(payload), instrument_id=instrument_id,
        market_type=market_type, timeframe=timeframe, horizon_bars=horizon_bars,
        model_id=model_id, dataset_fingerprint=dataset_fingerprint,
        current_regime=current_regime, probabilities=pairs,
        issued_at=issued, target_at=target, data_cutoff_at=cutoff,
    )


def resolve_regime_forecast(
    forecast: RegimeForecastRecord, actual_regime: str, resolved_at: datetime
) -> ResolvedRegimeForecast:
    if not isinstance(forecast, RegimeForecastRecord):
        raise TypeError("forecast must be a RegimeForecastRecord")
    return ResolvedRegimeForecast(forecast, actual_regime, resolved_at)


def _score_regime_group(rows: list[ResolvedRegimeForecast]) -> dict[str, Any]:
    states = tuple(state for state, _ in rows[0].forecast.probabilities)
    log_losses, brier_scores, correct = [], [], []
    for row in rows:
        probabilities = dict(row.forecast.probabilities)
        actual_probability = probabilities[row.actual_regime]
        log_losses.append(-math.log(max(actual_probability, 1e-15)))
        brier_scores.append(sum(
            (probabilities[state] - float(state == row.actual_regime)) ** 2
            for state in states
        ))
        predicted = max(probabilities, key=probabilities.get)
        correct.append(float(predicted == row.actual_regime))
    return {
        "n": len(rows),
        "multiclass_log_loss": sum(log_losses) / len(log_losses),
        "multiclass_brier_score": sum(brier_scores) / len(brier_scores),
        "top1_accuracy": sum(correct) / len(correct),
    }


def evaluate_regime_forecasts(rows: Iterable[ResolvedRegimeForecast]) -> dict[str, Any]:
    """Score categorical forecasts without pooling unlike instruments or horizons."""
    records = list(rows)
    if not records:
        return {"status": "NO_RESOLVED_FORECASTS", "cohorts": {}, "actionable": False}
    if any(not isinstance(row, ResolvedRegimeForecast) for row in records):
        raise TypeError("rows must contain ResolvedRegimeForecast values")
    ids = [row.forecast.forecast_id for row in records]
    if len(ids) != len(set(ids)):
        raise RegimeForecastError("duplicate regime forecast_id in evaluation cohort")
    buckets: dict[tuple[str, str, str, int, str], list[ResolvedRegimeForecast]] = defaultdict(list)
    for row in records:
        f = row.forecast
        buckets[(f.instrument_id, f.market_type, f.timeframe, f.horizon_bars, f.model_id)].append(row)
    cohorts = {}
    for key, group in sorted(buckets.items()):
        state_sets = {tuple(state for state, _ in row.forecast.probabilities) for row in group}
        if len(state_sets) != 1:
            raise RegimeForecastError("all forecasts in a cohort must use the same regime-state vocabulary")
        cohorts["|".join(map(str, key))] = _score_regime_group(group)
    overall = next(iter(cohorts.values())) if len(cohorts) == 1 else {
        "status": "NOT_AGGREGATED_MIXED_COHORTS", "n": len(records)
    }
    return {
        "schema": "regime_forecast_evaluation_v1",
        "status": "EVALUATED_OUT_OF_SAMPLE",
        "overall": overall,
        "cohorts": cohorts,
        "actionable": False,
        "guardrail": "Categorical regime metrics are historical diagnostics; incomparable cohorts are scored separately.",
    }


__all__ = [
    "RegimeForecastError", "RegimeForecastRecord", "RegimeTransitionModel",
    "ResolvedRegimeForecast", "create_regime_forecast_record",
    "evaluate_regime_forecasts", "fit_regime_transition_model",
    "forecast_regime_transitions", "resolve_regime_forecast",
]
