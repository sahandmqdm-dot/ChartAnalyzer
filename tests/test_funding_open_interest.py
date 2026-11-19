from datetime import datetime, timedelta, timezone

import pytest

from funding_open_interest import (
    DerivativesSnapshot,
    FundingObservation,
    OpenInterestObservation,
    analyze_derivatives_context,
    normalize_funding,
    normalize_open_interest,
)


NOW = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)


def make_funding(**kwargs):
    values = {
        "instrument_id": "BTC-USDT-PERP",
        "provider": "test-exchange",
        "observed_at": NOW - timedelta(seconds=30),
        "funding_rate": 0.0001,
        "funding_interval_hours": 8,
        "next_funding_at": NOW + timedelta(hours=2),
        "mark_price": 90010,
        "index_price": 90000,
    }
    values.update(kwargs)
    return FundingObservation(**values)


def make_oi(**kwargs):
    values = {
        "instrument_id": "BTC-USDT-PERP",
        "provider": "test-exchange",
        "observed_at": NOW - timedelta(seconds=20),
        "open_interest": 1200,
        "unit": "contracts",
        "quote_notional": 108000000,
    }
    values.update(kwargs)
    return OpenInterestObservation(**values)


def make_snapshot(funding=None, oi=None):
    return DerivativesSnapshot(
        instrument_id="BTC-USDT-PERP",
        funding=funding or make_funding(),
        open_interest=oi or make_oi(),
    )


def test_fresh_snapshot_is_valid_and_never_actionable():
    result = analyze_derivatives_context(snapshot=make_snapshot(), now=NOW)
    assert result["valid"] is True
    assert result["status"] == "VALID_READ_ONLY_CONTEXT"
    assert result["actionable"] is False
    assert result["funding"]["mark_index_basis_pct"] == pytest.approx(100 / 90000)
    assert result["open_interest"]["change_pct"] is None


def test_stale_funding_fails_closed():
    snapshot = make_snapshot(funding=make_funding(observed_at=NOW - timedelta(minutes=30)))
    result = analyze_derivatives_context(snapshot=snapshot, now=NOW)
    assert result["valid"] is False
    assert result["status"] == "NO_TRADE_STALE_DERIVATIVES_DATA"
    assert result["funding"]["fresh"] is False


def test_stale_open_interest_fails_closed():
    snapshot = make_snapshot(oi=make_oi(observed_at=NOW - timedelta(minutes=30)))
    result = analyze_derivatives_context(snapshot=snapshot, now=NOW)
    assert result["valid"] is False
    assert result["open_interest"]["fresh"] is False


def test_open_interest_change_uses_same_provider_unit_and_instrument():
    baseline = make_oi(
        observed_at=NOW - timedelta(hours=2),
        open_interest=1000,
    )
    result = analyze_derivatives_context(
        snapshot=make_snapshot(),
        now=NOW,
        history=[baseline],
        change_window=timedelta(hours=1),
    )
    assert result["open_interest"]["change_pct"] == pytest.approx(20.0)
    assert result["actionable"] is False


def test_unlike_history_is_ignored():
    wrong_provider = make_oi(provider="other", observed_at=NOW - timedelta(hours=2))
    wrong_unit = make_oi(unit="BTC", observed_at=NOW - timedelta(hours=3))
    result = analyze_derivatives_context(
        snapshot=make_snapshot(),
        now=NOW,
        history=[wrong_provider, wrong_unit],
    )
    assert result["open_interest"]["change_pct"] is None


def test_zero_baseline_does_not_divide_by_zero():
    baseline = make_oi(observed_at=NOW - timedelta(hours=2), open_interest=0)
    result = analyze_derivatives_context(snapshot=make_snapshot(), now=NOW, history=[baseline])
    assert result["open_interest"]["change_pct"] is None


def test_future_observation_is_rejected():
    snapshot = make_snapshot(funding=make_funding(observed_at=NOW + timedelta(seconds=1)))
    with pytest.raises(ValueError, match="future"):
        analyze_derivatives_context(snapshot=snapshot, now=NOW)


def test_naive_timestamps_are_rejected():
    with pytest.raises(ValueError, match="timezone-aware"):
        make_funding(observed_at=datetime(2026, 11, 19, 12, 0))


def test_invalid_rate_and_negative_open_interest_are_rejected():
    with pytest.raises(ValueError):
        make_funding(funding_rate=float("nan"))
    with pytest.raises(ValueError):
        make_oi(open_interest=-1)


def test_snapshot_requires_matching_instrument_and_provider():
    with pytest.raises(ValueError, match="does not match"):
        DerivativesSnapshot(
            instrument_id="ETH-USDT-PERP",
            funding=make_funding(),
            open_interest=make_oi(instrument_id="ETH-USDT-PERP"),
        )
    with pytest.raises(ValueError, match="providers must match"):
        DerivativesSnapshot(
            instrument_id="BTC-USDT-PERP",
            funding=make_funding(),
            open_interest=make_oi(provider="another"),
        )


def test_provider_normalizers():
    funding = normalize_funding({
        "instrument_id": "BTC-USDT-PERP",
        "provider": "test-exchange",
        "observed_at": NOW,
        "funding_rate": "0.0002",
        "funding_interval_hours": "8",
    })
    oi = normalize_open_interest({
        "instrument_id": "BTC-USDT-PERP",
        "provider": "test-exchange",
        "observed_at": NOW,
        "open_interest": "250",
        "unit": "contracts",
    })
    assert funding.funding_rate == pytest.approx(0.0002)
    assert oi.open_interest == pytest.approx(250)


def test_invalid_age_window_is_rejected():
    with pytest.raises(ValueError, match="max_age"):
        analyze_derivatives_context(
            snapshot=make_snapshot(),
            now=NOW,
            max_funding_age=timedelta(0),
        )
