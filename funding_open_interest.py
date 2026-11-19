"""Provider-neutral funding-rate and open-interest intelligence.

This module validates timestamped derivatives observations and produces
read-only context. It does not infer trader positioning from OI alone and
never creates orders or actionable trade instructions.
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any


def _utc(name: str, value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be a timezone-aware datetime")
    return value.astimezone(UTC)


def _finite(name: str, value: Any, *, minimum: float | None = None) -> float:
    if isinstance(value, bool):
        raise TypeError(f"{name} must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not math.isfinite(number) or (minimum is not None and number < minimum):
        raise ValueError(f"{name} must be finite and >= {minimum}")
    return number


@dataclass(frozen=True, slots=True)
class FundingObservation:
    instrument_id: str
    provider: str
    observed_at: datetime
    funding_rate: float
    funding_interval_hours: float
    next_funding_at: datetime | None = None
    mark_price: float | None = None
    index_price: float | None = None

    def __post_init__(self) -> None:
        if not self.instrument_id.strip() or not self.provider.strip():
            raise ValueError("instrument_id and provider are required")
        observed = _utc("observed_at", self.observed_at)
        rate = _finite("funding_rate", self.funding_rate)
        interval = _finite("funding_interval_hours", self.funding_interval_hours, minimum=0.000001)
        next_at = None if self.next_funding_at is None else _utc("next_funding_at", self.next_funding_at)
        mark = None if self.mark_price is None else _finite("mark_price", self.mark_price, minimum=0.000001)
        index = None if self.index_price is None else _finite("index_price", self.index_price, minimum=0.000001)
        object.__setattr__(self, "instrument_id", self.instrument_id.strip())
        object.__setattr__(self, "provider", self.provider.strip())
        object.__setattr__(self, "observed_at", observed)
        object.__setattr__(self, "funding_rate", rate)
        object.__setattr__(self, "funding_interval_hours", interval)
        object.__setattr__(self, "next_funding_at", next_at)
        object.__setattr__(self, "mark_price", mark)
        object.__setattr__(self, "index_price", index)


@dataclass(frozen=True, slots=True)
class OpenInterestObservation:
    instrument_id: str
    provider: str
    observed_at: datetime
    open_interest: float
    unit: str
    quote_notional: float | None = None

    def __post_init__(self) -> None:
        if not self.instrument_id.strip() or not self.provider.strip() or not self.unit.strip():
            raise ValueError("instrument_id, provider and unit are required")
        observed = _utc("observed_at", self.observed_at)
        oi = _finite("open_interest", self.open_interest, minimum=0.0)
        notional = None if self.quote_notional is None else _finite(
            "quote_notional", self.quote_notional, minimum=0.0
        )
        object.__setattr__(self, "instrument_id", self.instrument_id.strip())
        object.__setattr__(self, "provider", self.provider.strip())
        object.__setattr__(self, "unit", self.unit.strip())
        object.__setattr__(self, "observed_at", observed)
        object.__setattr__(self, "open_interest", oi)
        object.__setattr__(self, "quote_notional", notional)


@dataclass(frozen=True, slots=True)
class DerivativesSnapshot:
    instrument_id: str
    funding: FundingObservation
    open_interest: OpenInterestObservation

    def __post_init__(self) -> None:
        if not self.instrument_id.strip():
            raise ValueError("instrument_id is required")
        if self.funding.instrument_id != self.instrument_id:
            raise ValueError("funding instrument does not match snapshot instrument")
        if self.open_interest.instrument_id != self.instrument_id:
            raise ValueError("open-interest instrument does not match snapshot instrument")
        if self.funding.provider != self.open_interest.provider:
            raise ValueError("funding and open-interest providers must match")
        object.__setattr__(self, "instrument_id", self.instrument_id.strip())


def normalize_funding(row: Mapping[str, Any]) -> FundingObservation:
    """Normalize a provider mapping; rates are decimal fractions, not percentages."""
    if not isinstance(row, Mapping):
        raise TypeError("funding row must be a mapping")
    return FundingObservation(
        instrument_id=str(row.get("instrument_id", "")),
        provider=str(row.get("provider", "")),
        observed_at=row["observed_at"],
        funding_rate=row["funding_rate"],
        funding_interval_hours=row["funding_interval_hours"],
        next_funding_at=row.get("next_funding_at"),
        mark_price=row.get("mark_price"),
        index_price=row.get("index_price"),
    )


def normalize_open_interest(row: Mapping[str, Any]) -> OpenInterestObservation:
    if not isinstance(row, Mapping):
        raise TypeError("open-interest row must be a mapping")
    return OpenInterestObservation(
        instrument_id=str(row.get("instrument_id", "")),
        provider=str(row.get("provider", "")),
        observed_at=row["observed_at"],
        open_interest=row["open_interest"],
        unit=str(row.get("unit", "")),
        quote_notional=row.get("quote_notional"),
    )


def _freshness(observed_at: datetime, now: datetime, max_age: timedelta) -> tuple[bool, float]:
    age = (now - observed_at).total_seconds()
    if age < 0:
        raise ValueError("observation timestamp is in the future")
    if max_age.total_seconds() <= 0:
        raise ValueError("max_age must be positive")
    return age <= max_age.total_seconds(), age


def analyze_derivatives_context(
    *,
    snapshot: DerivativesSnapshot,
    now: datetime,
    max_funding_age: timedelta = timedelta(minutes=10),
    max_open_interest_age: timedelta = timedelta(minutes=10),
    history: Iterable[OpenInterestObservation] = (),
    change_window: timedelta = timedelta(hours=1),
) -> dict[str, Any]:
    """Return a fail-closed, non-actionable derivatives context.

    OI percentage change is emitted only when a same-instrument, same-provider,
    same-unit historical point is found at or before the requested window.
    No directional inference is made from OI change in isolation.
    """
    current = _utc("now", now)
    if change_window.total_seconds() <= 0:
        raise ValueError("change_window must be positive")
    funding_fresh, funding_age = _freshness(
        snapshot.funding.observed_at, current, max_funding_age
    )
    oi_fresh, oi_age = _freshness(
        snapshot.open_interest.observed_at, current, max_open_interest_age
    )
    oi = snapshot.open_interest
    cutoff = oi.observed_at - change_window
    candidates = []
    for point in history:
        if not isinstance(point, OpenInterestObservation):
            raise TypeError("history must contain OpenInterestObservation values")
        if (
            point.instrument_id == snapshot.instrument_id
            and point.provider == oi.provider
            and point.unit == oi.unit
            and point.observed_at <= cutoff
            and point.observed_at <= oi.observed_at
        ):
            candidates.append(point)
    baseline = max(candidates, key=lambda item: item.observed_at) if candidates else None
    oi_change_pct = None
    if baseline is not None and baseline.open_interest > 0:
        oi_change_pct = (oi.open_interest / baseline.open_interest - 1.0) * 100.0

    valid = funding_fresh and oi_fresh
    warnings = []
    if not funding_fresh:
        warnings.append("Funding observation is stale.")
    if not oi_fresh:
        warnings.append("Open-interest observation is stale.")
    if baseline is None:
        warnings.append("Insufficient aligned open-interest history for change calculation.")
    if oi_change_pct is not None:
        warnings.append("Open-interest change alone does not identify long or short positioning.")
    return {
        "schema": "derivatives_context_v1",
        "instrument_id": snapshot.instrument_id,
        "provider": snapshot.funding.provider,
        "status": "VALID_READ_ONLY_CONTEXT" if valid else "NO_TRADE_STALE_DERIVATIVES_DATA",
        "valid": valid,
        "actionable": False,
        "funding": {
            "rate": snapshot.funding.funding_rate,
            "rate_unit": "decimal_per_funding_interval",
            "interval_hours": snapshot.funding.funding_interval_hours,
            "observed_at": snapshot.funding.observed_at.isoformat(),
            "age_seconds": funding_age,
            "fresh": funding_fresh,
            "next_funding_at": (
                snapshot.funding.next_funding_at.isoformat()
                if snapshot.funding.next_funding_at else None
            ),
            "mark_index_basis_pct": (
                (snapshot.funding.mark_price / snapshot.funding.index_price - 1.0) * 100.0
                if snapshot.funding.mark_price is not None and snapshot.funding.index_price is not None
                else None
            ),
        },
        "open_interest": {
            "value": oi.open_interest,
            "unit": oi.unit,
            "quote_notional": oi.quote_notional,
            "observed_at": oi.observed_at.isoformat(),
            "age_seconds": oi_age,
            "fresh": oi_fresh,
            "change_window_seconds": change_window.total_seconds(),
            "baseline_observed_at": baseline.observed_at.isoformat() if baseline else None,
            "change_pct": oi_change_pct,
        },
        "warnings": warnings,
        "limitations": [
            "Funding and open-interest values are provider-specific observations, not exchange-certified truth.",
            "Open-interest units differ across providers and contract types; never compare unlike units.",
            "Open-interest expansion or contraction alone does not reveal trader direction.",
            "No trading order is created, modified or cancelled.",
        ],
    }


__all__ = [
    "DerivativesSnapshot",
    "FundingObservation",
    "OpenInterestObservation",
    "analyze_derivatives_context",
    "normalize_funding",
    "normalize_open_interest",
]
