"""Pre-registered forecast baselines and paired block-bootstrap comparisons."""
from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import replace
from typing import Any

import numpy as np

from forecast_validation import ResolvedForecast, evaluate_forecasts

_Z_10 = 1.2815515655446004
_BASELINE_NAMES = ("zero_return_point", "historical_unconditional", "ewma_gaussian")


def build_baseline_quantiles(
    close: Iterable[float], horizon_bars: int, *, ewma_lambda: float = 0.94,
    history_window: int = 500,
) -> dict[str, dict[str, float]]:
    """Build simple baselines using only closes available at forecast cutoff."""
    prices = np.asarray(list(close), dtype=float)
    if prices.ndim != 1 or not np.isfinite(prices).all() or np.any(prices <= 0):
        raise ValueError("close must be a finite positive one-dimensional price series")
    if isinstance(horizon_bars, bool) or not isinstance(horizon_bars, int) or horizon_bars < 1:
        raise ValueError("horizon_bars must be a positive integer")
    if len(prices) - horizon_bars < 30:
        raise ValueError("at least 30 historical horizon-matched returns are required for baselines")
    if not 0.0 < float(ewma_lambda) < 1.0:
        raise ValueError("ewma_lambda must be between zero and one")
    if isinstance(history_window, bool) or int(history_window) < 30:
        raise ValueError("history_window must be at least 30")
    log_prices = np.log(prices)
    one_bar = np.diff(log_prices)
    horizon_returns = log_prices[horizon_bars:] - log_prices[:-horizon_bars]
    historical = np.quantile(horizon_returns[-min(len(horizon_returns), int(history_window)):],
                             [0.10, 0.50, 0.90])
    recent = one_bar[-min(len(one_bar), int(history_window)):]
    weights = (1.0 - float(ewma_lambda)) * float(ewma_lambda) ** np.arange(len(recent) - 1, -1, -1)
    weights /= weights.sum()
    ewma_sigma = math.sqrt(float(np.sum(weights * np.square(recent))) * horizon_bars)
    gaussian = np.asarray([-_Z_10 * ewma_sigma, 0.0, _Z_10 * ewma_sigma], dtype=float)
    zero = np.zeros(3, dtype=float)

    def pack(values: np.ndarray) -> dict[str, float]:
        q10, q50, q90 = (float(x) for x in values)
        if not all(math.isfinite(x) for x in (q10, q50, q90)) or not q10 <= q50 <= q90:
            raise ValueError("baseline quantiles must be finite and ordered")
        return {"q10": q10, "q50": q50, "q90": q90}

    return {
        "zero_return_point": pack(zero),
        "historical_unconditional": pack(historical),
        "ewma_gaussian": pack(gaussian),
    }


def paired_block_bootstrap_mean_ci(
    differences: Iterable[float], *, block_length: int = 5,
    repetitions: int = 2000, seed: int = 19,
) -> dict[str, Any]:
    """Moving-block bootstrap CI for paired candidate-minus-baseline differences."""
    values = np.asarray(list(differences), dtype=float)
    if values.ndim != 1 or values.size == 0 or not np.isfinite(values).all():
        raise ValueError("differences must be a non-empty finite one-dimensional array")
    if isinstance(block_length, bool) or int(block_length) < 1:
        raise ValueError("block_length must be positive")
    if isinstance(repetitions, bool) or int(repetitions) < 100:
        raise ValueError("repetitions must be at least 100")
    n = int(values.size)
    observed = float(np.mean(values))
    if n < 30:
        return {
            "status": "INSUFFICIENT_SAMPLE", "n": n, "mean_difference": observed,
            "ci_lower": None, "ci_upper": None, "confidence": 0.95,
            "block_length": min(int(block_length), n), "repetitions": int(repetitions),
        }
    block = min(int(block_length), n)
    blocks_needed = math.ceil(n / block)
    rng = np.random.default_rng(int(seed))
    starts = rng.integers(0, n - block + 1, size=(int(repetitions), blocks_needed))
    offsets = np.arange(block)
    indices = (starts[:, :, None] + offsets[None, None, :]).reshape(int(repetitions), -1)[:, :n]
    means = np.mean(values[indices], axis=1)
    lower, upper = np.quantile(means, [0.025, 0.975])
    return {
        "status": "ESTIMATED", "n": n, "mean_difference": observed,
        "ci_lower": float(lower), "ci_upper": float(upper), "confidence": 0.95,
        "block_length": block, "repetitions": int(repetitions),
    }


def _pinball(actual: float, predicted: float, quantile: float) -> float:
    error = actual - predicted
    return max(quantile * error, (quantile - 1.0) * error)


def compare_candidate_to_baselines(
    candidate_rows: list[ResolvedForecast],
    baseline_quantiles: list[dict[str, dict[str, float]]], *,
    min_segment_count: int = 30, block_length: int = 5,
    repetitions: int = 2000, seed: int = 19,
) -> dict[str, Any]:
    """Score frozen baselines on identical outcomes and compare paired losses."""
    if not candidate_rows or len(candidate_rows) != len(baseline_quantiles):
        raise ValueError("candidate rows and baseline forecasts must be non-empty and aligned")
    names = tuple(sorted(baseline_quantiles[0]))
    if not names:
        raise ValueError("at least one pre-registered baseline is required")
    if any(tuple(sorted(row)) != names for row in baseline_quantiles):
        raise ValueError("baseline set changed during the holdout cohort")
    if set(names) != set(_BASELINE_NAMES):
        raise ValueError("baseline set does not match the frozen supported baseline registry")

    candidate_pinball = []
    candidate_mae = []
    candidate_coverage = []
    candidate_width = []
    for row in candidate_rows:
        forecast, actual = row.forecast, float(row.actual_log_return)
        candidate_pinball.append(np.mean([
            _pinball(actual, forecast.q10, 0.10),
            _pinball(actual, forecast.q50, 0.50),
            _pinball(actual, forecast.q90, 0.90),
        ]))
        candidate_mae.append(abs(actual - forecast.q50))
        candidate_coverage.append(float(forecast.q10 <= actual <= forecast.q90))
        candidate_width.append(forecast.q90 - forecast.q10)

    baseline_reports = {}
    comparisons = {}
    for name in names:
        baseline_rows = []
        baseline_pinball = []
        baseline_mae = []
        baseline_coverage = []
        baseline_width = []
        for row, forecasts in zip(candidate_rows, baseline_quantiles, strict=True):
            q = forecasts[name]
            q10, q50, q90 = (float(q[key]) for key in ("q10", "q50", "q90"))
            if not all(math.isfinite(v) for v in (q10, q50, q90)) or not q10 <= q50 <= q90:
                raise ValueError(f"invalid baseline quantiles for {name}")
            base_forecast = replace(
                row.forecast, model_id="baseline:" + name, model_version="1.0.0",
                q10=q10, q50=q50, q90=q90, probability_up=None,
            )
            baseline_rows.append(ResolvedForecast(
                forecast=base_forecast, actual_log_return=row.actual_log_return,
                resolved_at=row.resolved_at,
            ))
            actual = float(row.actual_log_return)
            baseline_pinball.append(np.mean([
                _pinball(actual, q10, 0.10), _pinball(actual, q50, 0.50),
                _pinball(actual, q90, 0.90),
            ]))
            baseline_mae.append(abs(actual - q50))
            baseline_coverage.append(float(q10 <= actual <= q90))
            baseline_width.append(q90 - q10)
        baseline_reports[name] = evaluate_forecasts(
            baseline_rows, min_segment_count=min_segment_count)
        comparisons[name] = {
            "candidate_minus_baseline": {
                "mean_pinball_loss": paired_block_bootstrap_mean_ci(
                    np.asarray(candidate_pinball) - np.asarray(baseline_pinball),
                    block_length=block_length, repetitions=repetitions, seed=seed),
                "median_forecast_mae": paired_block_bootstrap_mean_ci(
                    np.asarray(candidate_mae) - np.asarray(baseline_mae),
                    block_length=block_length, repetitions=repetitions, seed=seed + 1),
                "central_80_coverage": paired_block_bootstrap_mean_ci(
                    np.asarray(candidate_coverage) - np.asarray(baseline_coverage),
                    block_length=block_length, repetitions=repetitions, seed=seed + 2),
                "central_80_interval_width": paired_block_bootstrap_mean_ci(
                    np.asarray(candidate_width) - np.asarray(baseline_width),
                    block_length=block_length, repetitions=repetitions, seed=seed + 3),
            },
            "interpretation": {
                "losses": "negative candidate-minus-baseline is better",
                "coverage": "positive candidate-minus-baseline is higher coverage",
                "width": "lower width is not better unless coverage is maintained",
            },
        }
    return {
        "schema": "prospective_baseline_comparison_v1",
        "n_paired": len(candidate_rows),
        "baseline_evaluations": baseline_reports,
        "paired_comparisons": comparisons,
        "inference": "paired moving-block bootstrap; confidence intervals are descriptive, not a guarantee of future edge",
    }
