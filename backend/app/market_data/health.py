from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


LIVE_MARKET_STALE_AFTER_SECONDS = 15.0
OPEN_INTEREST_STALE_MULTIPLIER = 3.0
OPEN_INTEREST_MIN_STALE_AFTER_SECONDS = 180.0
FUTURE_TIMESTAMP_TOLERANCE_SECONDS = 30.0


def _parse_timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _feed_health(
    kind: str,
    snapshot: dict,
    now: datetime,
    stale_after_seconds: float,
) -> dict:
    state = str(snapshot.get("state", "unknown"))
    observed_at = _parse_timestamp(snapshot.get("observed_at"))
    age_seconds = None
    timestamp_delta = None
    if observed_at is not None:
        timestamp_delta = (now - observed_at).total_seconds()
        age_seconds = max(0.0, timestamp_delta)

    if state == "stopped":
        status = "stopped"
    elif state == "reconnecting":
        status = "reconnecting"
    elif observed_at is None:
        status = "waiting_for_data"
    elif timestamp_delta is not None and timestamp_delta < -FUTURE_TIMESTAMP_TOLERANCE_SECONDS:
        status = "clock_skew"
    elif age_seconds is not None and age_seconds > stale_after_seconds:
        status = "stale"
    else:
        status = "healthy"

    return {
        "kind": kind,
        "provider": snapshot.get("provider"),
        "symbol": snapshot.get("symbol"),
        "state": state,
        "status": status,
        "observed_at": observed_at.isoformat() if observed_at else None,
        "age_seconds": round(age_seconds, 3) if age_seconds is not None else None,
        "stale_after_seconds": stale_after_seconds,
        "is_stale": status == "stale",
        "last_error": snapshot.get("last_error"),
        "persistence_state": snapshot.get("persistence_state", "disabled"),
        "persistence_error": snapshot.get("persistence_error"),
    }


def build_market_data_health(
    provider: str,
    streams: dict,
    collectors: dict,
    *,
    now: datetime | None = None,
) -> dict:
    """Build a provider-scoped health report without starting or mutating collectors."""
    checked_at = now or datetime.now(UTC)
    if checked_at.tzinfo is None:
        raise ValueError("health check time must be timezone-aware")
    checked_at = checked_at.astimezone(UTC)

    feeds: list[dict] = []
    for stream in streams.values():
        if stream.provider != provider:
            continue
        feeds.append(
            _feed_health(
                "live_market",
                stream.snapshot(),
                checked_at,
                LIVE_MARKET_STALE_AFTER_SECONDS,
            )
        )

    for collector in collectors.values():
        if collector.provider != provider:
            continue
        interval = float(getattr(collector, "interval_seconds", 60.0))
        stale_after = max(
            OPEN_INTEREST_MIN_STALE_AFTER_SECONDS,
            interval * OPEN_INTEREST_STALE_MULTIPLIER,
        )
        feeds.append(
            _feed_health(
                "open_interest",
                collector.snapshot(),
                checked_at,
                stale_after,
            )
        )

    active_feeds = [feed for feed in feeds if feed["state"] != "stopped"]
    degraded = [
        feed
        for feed in active_feeds
        if feed["status"] in {"reconnecting", "stale", "clock_skew"}
        or feed["persistence_state"] == "unavailable"
    ]
    starting = [
        feed
        for feed in active_feeds
        if feed["status"] in {"waiting_for_data"}
        or feed["state"] == "connecting"
    ]
    if not active_feeds:
        status = "idle"
    elif degraded:
        status = "degraded"
    elif starting:
        status = "starting"
    else:
        status = "healthy"

    reasons = [
        f'{feed["kind"]}:{feed["symbol"]}:{feed["status"]}'
        for feed in degraded
    ]
    reasons.extend(
        f'{feed["kind"]}:{feed["symbol"]}:persistence_unavailable'
        for feed in active_feeds
        if feed["persistence_state"] == "unavailable"
    )
    return {
        "provider": provider,
        "status": status,
        "checked_at": checked_at.isoformat(),
        "active_feeds": len(active_feeds),
        "feeds": feeds,
        "degraded_reasons": sorted(set(reasons)),
    }
