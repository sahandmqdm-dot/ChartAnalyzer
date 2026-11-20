# ruff: noqa: I001
import datetime

import pytest

from evidence_contracts import (
    EvidenceContractError,
    EvidenceRecord,
    EvidenceType,
    SourceReference,
    SourceRegistry,
    SourceType,
    TrustTier,
    canonical_sha256,
    evidence_available_at,
    validate_source_links,
)


T0 = datetime.datetime(2026, 1, 1, 12, 0, tzinfo=datetime.UTC)


def source(**overrides):
    values = {
        "source_id": "src:exchange:binance:btc",
        "provider": "binance",
        "source_type": SourceType.EXCHANGE,
        "source_uri": "https://example.invalid/market/BTCUSDT",
        "first_seen_at": T0,
        "retrieved_at": T0 + datetime.timedelta(seconds=2),
        "published_at": T0,
        "publisher": "Binance",
        "content_hash": "a" * 64,
        "license_policy": "public_market_data",
        "trust_tier": TrustTier.PRIMARY,
    }
    values.update(overrides)
    return SourceReference(**values)


def evidence(**overrides):
    values = {
        "evidence_type": EvidenceType.OBSERVATION,
        "claim": "BTC reference price observed",
        "source_reference_id": "src:exchange:binance:btc",
        "first_seen_at": T0 + datetime.timedelta(seconds=1),
        "available_at": T0 + datetime.timedelta(seconds=2),
        "published_at": T0,
        "entity_ids": ("crypto:BTC",),
        "instrument_id": "binance:spot:BTCUSDT",
        "value": 65000.0,
        "unit": "USD/BTC",
        "currency": "USD",
    }
    values.update(overrides)
    return EvidenceRecord(**values)


def test_source_normalizes_timezone_to_utc():
    local = datetime.datetime(2026, 1, 1, 15, 30, tzinfo=datetime.timezone(datetime.timedelta(hours=3, minutes=30)))
    item = source(first_seen_at=local, retrieved_at=local + datetime.timedelta(seconds=1))
    assert item.first_seen_at == datetime.datetime(2026, 1, 1, 12, 0, tzinfo=datetime.UTC)


@pytest.mark.parametrize("field", ["first_seen_at", "retrieved_at"])
def test_source_rejects_naive_timestamps(field):
    values = {"first_seen_at": T0, "retrieved_at": T0 + datetime.timedelta(seconds=1)}
    values[field] = datetime.datetime(2026, 1, 1)
    with pytest.raises(EvidenceContractError, match="timezone-aware"):
        source(**values)


def test_source_rejects_retrieval_before_first_seen():
    with pytest.raises(EvidenceContractError, match="cannot precede"):
        source(retrieved_at=T0 - datetime.timedelta(seconds=1))


def test_source_rejects_invalid_content_hash():
    with pytest.raises(EvidenceContractError, match="SHA-256"):
        source(content_hash="not-a-hash")


def test_evidence_id_is_deterministic_and_payload_sensitive():
    first = evidence()
    second = evidence()
    changed = evidence(value=65001.0)
    assert first.evidence_id == second.evidence_id
    assert first.evidence_id != changed.evidence_id
    assert len(first.evidence_id) == 64


def test_evidence_rejects_naive_and_future_availability():
    with pytest.raises(EvidenceContractError, match="timezone-aware"):
        evidence(available_at=datetime.datetime(2026, 1, 1, 12, 0))
    with pytest.raises(EvidenceContractError, match="cannot precede"):
        evidence(available_at=T0 - datetime.timedelta(microseconds=1))


def test_evidence_rejects_non_finite_values_and_confidence():
    with pytest.raises(EvidenceContractError, match="finite"):
        evidence(value=float("nan"))
    with pytest.raises(EvidenceContractError, match=r"\[0, 1\]"):
        evidence(confidence=1.1)


def test_evidence_rejects_duplicate_entity_ids():
    with pytest.raises(EvidenceContractError, match="duplicates"):
        evidence(entity_ids=("asset:BTC", "asset:BTC"))


def test_valid_time_must_be_ordered():
    with pytest.raises(EvidenceContractError, match="valid_time_end"):
        evidence(
            valid_time_start=T0 + datetime.timedelta(days=2),
            valid_time_end=T0 + datetime.timedelta(days=1),
        )


def test_point_in_time_filter_excludes_not_yet_available_evidence():
    early = evidence(available_at=T0 + datetime.timedelta(seconds=2))
    late = evidence(
        claim="Later correction",
        available_at=T0 + datetime.timedelta(days=1),
        first_seen_at=T0 + datetime.timedelta(days=1),
    )
    selected = evidence_available_at([late, early], T0 + datetime.timedelta(minutes=1))
    assert selected == (early,)


def test_point_in_time_filter_rejects_naive_cutoff():
    with pytest.raises(EvidenceContractError, match="timezone-aware"):
        evidence_available_at([evidence()], datetime.datetime(2026, 1, 1))


def test_source_link_validation_and_temporal_consistency():
    src = source()
    record = evidence()
    assert validate_source_links([record], [src]) == (record,)
    with pytest.raises(EvidenceContractError, match="missing source"):
        validate_source_links([record], [])
    with pytest.raises(EvidenceContractError, match="precedes its source"):
        validate_source_links(
            [evidence(first_seen_at=T0 - datetime.timedelta(seconds=1))], [src]
        )


def test_publication_timestamp_must_match_source_reference():
    with pytest.raises(EvidenceContractError, match="publication timestamp"):
        validate_source_links(
            [evidence(published_at=T0 + datetime.timedelta(seconds=5))], [source()]
        )


def test_serialization_is_json_safe_and_stable():
    item = evidence()
    encoded = item.to_dict()
    assert encoded["available_at"].endswith("Z")
    assert encoded["evidence_type"] == "observation"
    assert canonical_sha256({"b": 2, "a": 1}) == canonical_sha256({"a": 1, "b": 2})


def test_evidence_rejects_forged_supplied_id():
    with pytest.raises(EvidenceContractError, match="does not match"):
        evidence(evidence_id="f" * 64)



def test_source_registry_is_idempotent_for_identical_versions():
    item = source()
    registry = SourceRegistry()
    assert registry.register(item) == item
    assert registry.register(item) == item
    assert registry.get(item.source_id) == item
    assert registry.all() == (item,)


def test_source_registry_rejects_source_id_collision():
    registry = SourceRegistry([source()])
    changed = source(content_hash="b" * 64)
    with pytest.raises(EvidenceContractError, match="collision"):
        registry.register(changed)
    assert registry.all() == (source(),)


def test_source_registry_batch_registration_is_atomic():
    first = source()
    conflicting = source(content_hash="c" * 64)
    registry = SourceRegistry()
    with pytest.raises(EvidenceContractError, match="collision"):
        registry.register_many([first, conflicting])
    assert registry.all() == ()


def test_source_registry_validates_evidence_links():
    item = source()
    registry = SourceRegistry([item])
    record = evidence()
    assert registry.validate_evidence([record]) == (record,)


def test_evidence_cannot_be_available_before_source_retrieval():
    src = source()
    record = evidence(
        first_seen_at=T0 + datetime.timedelta(seconds=1),
        available_at=T0 + datetime.timedelta(seconds=1),
    )
    with pytest.raises(EvidenceContractError, match="precedes source retrieval"):
        validate_source_links([record], [src])


def test_source_publication_cannot_follow_its_retrieval():
    with pytest.raises(EvidenceContractError, match="published_at cannot be later"):
        source(published_at=T0 + datetime.timedelta(seconds=3))
