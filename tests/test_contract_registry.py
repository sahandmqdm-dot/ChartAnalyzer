# ruff: noqa: I001  # Match the repository's flat-layout import convention.
from datetime import datetime, timedelta, UTC

import pytest

from contract_registry import ContractRegistry


NOW = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)


def metadata(**changes):
    value = {
        "exchange": "binance", "symbol": "BTCUSDT", "market_type": "linear_perpetual",
        "contract_type": "linear", "status": "TRADING", "quote_asset": "USDT",
        "margin_asset": "USDT", "settlement_asset": "USDT", "contract_multiplier": "1",
        "tick_size": "0.1", "step_size": "0.001", "min_qty": "0.001",
        "max_qty": "1000", "min_notional": "5", "max_leverage": "125",
        "maintenance_margin_tiers": [
            {"notional_cap": "50000", "maintenance_margin_rate": "0.004",
             "maintenance_amount": "0", "max_leverage": "125"},
            {"notional_cap": "250000", "maintenance_margin_rate": "0.01",
             "maintenance_amount": "300", "max_leverage": "50"},
        ],
        "source": "binance-exchange-info+risk-brackets", "version": "fixture-v1",
        "observed_at": NOW - timedelta(minutes=1),
        "effective_from": NOW - timedelta(days=1), "effective_to": None,
    }
    value.update(changes)
    return value


def test_register_and_resolve_point_in_time_snapshot():
    registry = ContractRegistry()
    snapshot = registry.register(metadata())
    result = registry.resolve(
        venue="BINANCE", instrument_id="btcusdt", market_type="linear_perpetual",
        as_of=NOW, max_observation_age_seconds=300,
    )
    assert result["status"] == "CONTRACT_RESOLVED"
    assert result["fingerprint"] == snapshot.fingerprint
    assert result["contract"]["registry_fingerprint"] == snapshot.fingerprint


def test_snapshot_payload_is_immutable_to_caller_mutation():
    registry = ContractRegistry()
    snapshot = registry.register(metadata())
    payload = snapshot.payload()
    payload["tick_size"] = "999"
    assert snapshot.payload()["tick_size"] == "0.1"


def test_rejects_overlapping_different_contract_versions():
    registry = ContractRegistry()
    registry.register(metadata())
    changed = metadata(version="fixture-v2", tick_size="0.5")
    with pytest.raises(ValueError, match="overlaps"):
        registry.register(changed)


def test_accepts_non_overlapping_temporal_versions():
    registry = ContractRegistry()
    first = metadata(effective_to=NOW)
    second = metadata(version="fixture-v2", tick_size="0.5",
                      observed_at=NOW + timedelta(seconds=1), effective_from=NOW)
    registry.register(first)
    registry.register(second)
    old = registry.resolve(venue="binance", instrument_id="BTCUSDT",
                           market_type="linear_perpetual", as_of=NOW - timedelta(seconds=1))
    new = registry.resolve(venue="binance", instrument_id="BTCUSDT",
                           market_type="linear_perpetual", as_of=NOW + timedelta(seconds=2))
    assert old["contract"]["version"] == "fixture-v1"
    assert new["contract"]["version"] == "fixture-v2"


def test_stale_and_future_observations_fail_closed():
    registry = ContractRegistry()
    registry.register(metadata(observed_at=NOW - timedelta(days=2)))
    stale = registry.resolve(venue="binance", instrument_id="BTCUSDT",
                             market_type="linear_perpetual", as_of=NOW,
                             max_observation_age_seconds=60)
    future_registry = ContractRegistry()
    future_registry.register(metadata(observed_at=NOW + timedelta(seconds=5)))
    future = future_registry.resolve(venue="binance", instrument_id="BTCUSDT",
                                     market_type="linear_perpetual", as_of=NOW)
    assert stale["status"] == "CONTRACT_STALE"
    assert future["status"] == "CONTRACT_OBSERVATION_FROM_FUTURE"


def test_unknown_identity_and_bad_contract_metadata_fail_closed():
    registry = ContractRegistry()
    assert registry.resolve(venue="binance", instrument_id="ETHUSDT",
                            market_type="linear_perpetual", as_of=NOW)["status"] == "CONTRACT_NOT_FOUND"
    bad = metadata(contract_type="inverse")
    with pytest.raises(ValueError, match="only linear"):
        registry.register(bad)


def test_rejects_missing_margin_deduction_even_if_exchange_filter_tier_is_present():
    registry = ContractRegistry()
    bad = metadata()
    bad["maintenance_margin_tiers"][0].pop("maintenance_amount")
    with pytest.raises(ValueError, match="missing"):
        registry.register(bad)


def test_halted_contract_is_preserved_but_not_resolved_as_tradable():
    registry = ContractRegistry()
    registry.register(metadata(status="HALT"))
    result = registry.resolve(venue="binance", instrument_id="BTCUSDT",
                              market_type="linear_perpetual", as_of=NOW)
    assert result["status"] == "CONTRACT_NOT_TRADING"
    assert result["contract"]["status"] == "HALT"
