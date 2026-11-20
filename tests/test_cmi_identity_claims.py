import datetime

import pytest

from cmi_identity_claims import (
    ClaimModality,
    ClaimRecord,
    ClaimRegistry,
    ClaimStance,
    EntityAlias,
    EntityRecord,
    EntityResolver,
    EntityType,
    IdentityContractError,
    ResolutionMethod,
    ResolutionStatus,
)
from evidence_contracts import EvidenceRecord, EvidenceType
from multi_asset import AssetClass, Instrument, MarketRegistry, MarketType


T0 = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)


def entity(entity_id="crypto:BTC", **kw):
    return EntityRecord(entity_id, EntityType.ASSET, kw.pop("display_name", "Bitcoin"),
                        kw.pop("recorded_at", T0), **kw)


def alias(entity_id, value, namespace="ticker", provider=None, **kw):
    recorded_at = kw.pop("recorded_at", T0)
    return EntityAlias(entity_id, namespace, value, recorded_at, provider=provider, **kw)


def evidence(available_at=T0):
    return EvidenceRecord(
        evidence_type=EvidenceType.OBSERVATION, claim="BTC price observed",
        source_reference_id="src:test", first_seen_at=T0, available_at=available_at,
        entity_ids=("crypto:BTC",),
    )


def claim(record, **kw):
    values = {"subject_entity_id": "crypto:BTC", "predicate": "has_observation",
              "object_text": "price observed", "modality": ClaimModality.FACT,
              "stance": ClaimStance.NEUTRAL, "available_at": record.available_at,
              "evidence_ids": (record.evidence_id,), "confidence": 0.9}
    values.update(kw)
    return ClaimRecord(**values)


def test_exact_alias_resolves_and_is_case_insensitive():
    resolver = EntityResolver([entity()], [alias("crypto:BTC", "btc")])
    result = resolver.resolve("BTC", namespace="TICKER", as_of=T0)
    assert result.status is ResolutionStatus.RESOLVED
    assert result.candidate_entity_ids == ("crypto:BTC",)


def test_ticker_collision_is_ambiguous_not_auto_merged():
    rows = [entity(), entity("equity:NYSE:BTC", display_name="BTC Corp")]
    resolver = EntityResolver(rows, [alias("crypto:BTC", "BTC"), alias("equity:NYSE:BTC", "BTC")])
    result = resolver.resolve("BTC", namespace="ticker", as_of=T0)
    assert result.status is ResolutionStatus.AMBIGUOUS
    assert len(result.candidate_entity_ids) == 2


def test_provider_scoped_alias_disambiguates():
    rows = [entity(), entity("equity:NASDAQ:ABC", display_name="ABC Inc")]
    resolver = EntityResolver(rows, [
        alias("crypto:BTC", "ABC", provider="provider-a"),
        alias("equity:NASDAQ:ABC", "ABC", provider="provider-b"),
    ])
    result = resolver.resolve("ABC", namespace="ticker", provider="provider-b", as_of=T0)
    assert result.candidate_entity_ids == ("equity:NASDAQ:ABC",)


def test_expired_alias_is_not_resolved():
    resolver = EntityResolver([entity()], [alias("crypto:BTC", "BTC", valid_to=T0 + datetime.timedelta(days=1))])
    assert resolver.resolve("BTC", namespace="ticker", as_of=T0 + datetime.timedelta(days=1)).status is ResolutionStatus.UNRESOLVED


def test_unknown_alias_is_unresolved():
    resolver = EntityResolver([entity()])
    assert resolver.resolve("UNKNOWN", namespace="ticker", as_of=T0).status is ResolutionStatus.UNRESOLVED


def test_alias_must_reference_known_entity_and_batch_is_atomic():
    resolver = EntityResolver()
    with pytest.raises(IdentityContractError, match="unknown entity"):
        resolver.register_many([entity()], [alias("missing", "BTC")])
    with pytest.raises(KeyError):
        resolver.get("crypto:BTC")


def test_entity_validity_window():
    item = entity(valid_from=T0 + datetime.timedelta(days=1))
    assert not item.is_valid_at(T0)
    assert item.is_valid_at(T0 + datetime.timedelta(days=1))


def test_naive_timestamp_rejected():
    with pytest.raises(IdentityContractError, match="timezone-aware"):
        entity(recorded_at=datetime.datetime(2026, 1, 1))


def test_claim_requires_evidence_and_stable_id():
    record = evidence()
    item = claim(record)
    assert item.claim_id == claim(record).claim_id
    with pytest.raises(IdentityContractError, match="one or more"):
        ClaimRecord("crypto:BTC", "p", "o", "fact", "neutral", T0, ())


def test_claim_registry_validates_evidence_and_subject():
    record = evidence()
    item = claim(record)
    registry = ClaimRegistry()
    assert registry.register_many([item], entities=[entity()], evidence=[record]) == (item,)
    assert registry.get(item.claim_id) == item
    with pytest.raises(IdentityContractError, match="unknown claim subject"):
        ClaimRegistry().register_many([item], entities=[], evidence=[record])


def test_claim_rejects_future_evidence_leakage():
    record = evidence(T0 + datetime.timedelta(hours=1))
    item = claim(record, available_at=T0)
    with pytest.raises(IdentityContractError, match="precedes cited evidence"):
        ClaimRegistry().register_many([item], entities=[entity()], evidence=[record])


def test_claim_rejects_missing_evidence_and_invalid_confidence():
    record = evidence()
    item = claim(record)
    with pytest.raises(IdentityContractError, match="missing claim evidence"):
        ClaimRegistry().register_many([item], entities=[entity()], evidence=[])
    with pytest.raises(IdentityContractError, match="confidence"):
        claim(record, confidence=1.1)


def test_claim_registry_batch_is_atomic():
    record = evidence()
    good = claim(record)
    bad = claim(record, subject_entity_id="missing:entity")
    registry = ClaimRegistry()
    with pytest.raises(IdentityContractError):
        registry.register_many([good, bad], entities=[entity()], evidence=[record])
    assert registry.all() == ()


def test_venue_instrument_resolution_reuses_market_registry():
    instrument = Instrument(
        "crypto:BTC/USDT", "BTC/USDT", "BINANCE",
        AssetClass.CRYPTO_SPOT, MarketType.SPOT, "BTC", "USDT",
    )
    resolver = EntityResolver(market_registry=MarketRegistry([instrument]))
    result = resolver.resolve_instrument(
        venue="binance", symbol="BTC/USDT", market_type="spot"
    )
    assert result.status is ResolutionStatus.RESOLVED
    assert result.method is ResolutionMethod.VENUE_INSTRUMENT
    assert result.candidate_entity_ids == ("crypto:BTC/USDT",)


def test_venue_instrument_resolution_fails_closed_without_registry_match():
    resolver = EntityResolver(market_registry=MarketRegistry())
    result = resolver.resolve_instrument(
        venue="binance", symbol="BTCUSDT", market_type="spot"
    )
    assert result.status is ResolutionStatus.UNRESOLVED


def test_alias_key_collision_with_changed_validity_is_rejected_atomically():
    resolver = EntityResolver([entity()], [alias("crypto:BTC", "BTC")])
    changed = alias("crypto:BTC", "BTC", valid_to=T0 + datetime.timedelta(days=5))
    with pytest.raises(IdentityContractError, match="alias key collision"):
        resolver.register_many([], [changed])
    assert resolver.resolve("BTC", namespace="ticker", as_of=T0).status is ResolutionStatus.RESOLVED


def test_future_recorded_entity_and_alias_are_not_visible_in_past():
    future = entity(recorded_at=T0 + datetime.timedelta(days=1))
    future_alias = alias("crypto:BTC", "BTC", recorded_at=T0 + datetime.timedelta(days=1))
    resolver = EntityResolver([future], [future_alias])
    assert resolver.resolve("crypto:BTC", namespace="id", as_of=T0).status is ResolutionStatus.UNRESOLVED
    assert resolver.resolve("BTC", namespace="ticker", as_of=T0).status is ResolutionStatus.UNRESOLVED


def test_claim_subject_must_be_point_in_time_valid():
    record = evidence()
    item = claim(record)
    future_subject = entity(recorded_at=T0 + datetime.timedelta(seconds=1))
    with pytest.raises(IdentityContractError, match="point-in-time valid"):
        ClaimRegistry().register_many([item], entities=[future_subject], evidence=[record])


def test_claim_rejects_evidence_for_different_subject():
    unrelated = EvidenceRecord(
        evidence_type=EvidenceType.OBSERVATION, claim="AAPL revenue",
        source_reference_id="src:test", first_seen_at=T0, available_at=T0,
        entity_ids=("equity:NASDAQ:AAPL",),
    )
    item = claim(unrelated)
    with pytest.raises(IdentityContractError, match="not linked by cited evidence"):
        ClaimRegistry().register_many([item], entities=[entity()], evidence=[unrelated])


def test_claim_valid_time_interval_must_have_positive_duration():
    record = evidence()
    with pytest.raises(IdentityContractError, match="must be later"):
        claim(record, valid_time_start=T0, valid_time_end=T0)
