"""Canonical, immutable evidence contracts for Cognitive Market Intelligence.

This module is intentionally dependency-free and does not perform ingestion,
LLM extraction, persistence, trading, or network access. It provides strict
point-in-time records that downstream CMI components can validate and reuse.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from collections.abc import Iterable
from datetime import UTC, datetime
from enum import Enum
from typing import Any


class EvidenceContractError(ValueError):
    """An evidence/source record violates the canonical contract."""


class SourceType(str, Enum):
    EXCHANGE = "exchange"
    REGULATOR = "regulator"
    ISSUER = "issuer"
    NEWS = "news"
    RESEARCH = "research"
    SOCIAL = "social"
    ON_CHAIN = "on_chain"
    MACRO_SERIES = "macro_series"
    OTHER = "other"


class EvidenceType(str, Enum):
    OBSERVATION = "observation"
    FILING_FACT = "filing_fact"
    REPORTED_FACT = "reported_fact"
    ANALYST_OPINION = "analyst_opinion"
    EXTRACTED_CLAIM = "extracted_claim"
    MODEL_INFERENCE = "model_inference"
    HYPOTHESIS = "hypothesis"


class TrustTier(str, Enum):
    PRIMARY = "primary"
    ESTABLISHED = "established"
    SECONDARY = "secondary"
    COMMUNITY = "community"
    UNASSESSED = "unassessed"


def _required_text(value: Any, name: str, *, max_length: int = 2048) -> str:
    if not isinstance(value, str):
        raise EvidenceContractError(f"{name} must be a string")
    normalized = value.strip()
    if not normalized:
        raise EvidenceContractError(f"{name} is required")
    if len(normalized) > max_length:
        raise EvidenceContractError(f"{name} exceeds {max_length} characters")
    return normalized


def _optional_text(value: Any, name: str, *, max_length: int = 2048) -> str | None:
    if value is None:
        return None
    return _required_text(value, name, max_length=max_length)


def _utc(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise EvidenceContractError(f"{name} must be a timezone-aware datetime")
    return value.astimezone(UTC)


def _finite_number(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise EvidenceContractError(f"{name} must be a finite number")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise EvidenceContractError(f"{name} must be a finite number") from exc
    if not math.isfinite(result):
        raise EvidenceContractError(f"{name} must be a finite number")
    return result


def _canonical_value(value: Any) -> Any:
    """Convert supported values to stable JSON-compatible primitives."""
    if isinstance(value, datetime):
        return _utc(value, "timestamp").isoformat().replace("+00:00", "Z")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(k): _canonical_value(v) for k, v in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise EvidenceContractError("canonical payload cannot contain non-finite numbers")
        return value
    raise EvidenceContractError(f"unsupported canonical value type: {type(value).__name__}")


def canonical_sha256(payload: Any) -> str:
    encoded = json.dumps(
        _canonical_value(payload), sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class SourceReference:
    """Attribution and retrieval lineage for one immutable source version."""

    source_id: str
    provider: str
    source_type: SourceType
    source_uri: str
    first_seen_at: datetime
    retrieved_at: datetime
    published_at: datetime | None = None
    publisher: str | None = None
    author: str | None = None
    content_hash: str | None = None
    license_policy: str = "unspecified"
    trust_tier: TrustTier = TrustTier.UNASSESSED

    def __post_init__(self) -> None:
        for field in ("source_id", "provider", "source_uri", "license_policy"):
            object.__setattr__(self, field, _required_text(getattr(self, field), field))
        try:
            object.__setattr__(self, "source_type", SourceType(self.source_type))
            object.__setattr__(self, "trust_tier", TrustTier(self.trust_tier))
        except ValueError as exc:
            raise EvidenceContractError("unsupported source_type or trust_tier") from exc
        first_seen = _utc(self.first_seen_at, "first_seen_at")
        retrieved = _utc(self.retrieved_at, "retrieved_at")
        if retrieved < first_seen:
            raise EvidenceContractError("retrieved_at cannot precede first_seen_at")
        object.__setattr__(self, "first_seen_at", first_seen)
        object.__setattr__(self, "retrieved_at", retrieved)
        if self.published_at is not None:
            object.__setattr__(self, "published_at", _utc(self.published_at, "published_at"))
        object.__setattr__(self, "publisher", _optional_text(self.publisher, "publisher"))
        object.__setattr__(self, "author", _optional_text(self.author, "author"))
        if self.content_hash is not None:
            digest = _required_text(self.content_hash, "content_hash", max_length=64).lower()
            if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
                raise EvidenceContractError("content_hash must be a 64-character SHA-256 hex digest")
            object.__setattr__(self, "content_hash", digest)

    @property
    def fingerprint(self) -> str:
        return canonical_sha256(asdict(self))

    def to_dict(self) -> dict[str, Any]:
        return _canonical_value(asdict(self))


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    """One attributed observation, claim, inference, or explicit hypothesis."""

    evidence_type: EvidenceType
    claim: str
    source_reference_id: str
    first_seen_at: datetime
    available_at: datetime
    published_at: datetime | None = None
    valid_time_start: datetime | None = None
    valid_time_end: datetime | None = None
    entity_ids: tuple[str, ...] = ()
    instrument_id: str | None = None
    event_id: str | None = None
    value: float | None = None
    unit: str | None = None
    currency: str | None = None
    extraction_method: str | None = None
    extractor_version: str | None = None
    confidence: float | None = None
    supersedes_evidence_id: str | None = None
    evidence_id: str = ""

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "evidence_type", EvidenceType(self.evidence_type))
        except ValueError as exc:
            raise EvidenceContractError("unsupported evidence_type") from exc
        object.__setattr__(self, "claim", _required_text(self.claim, "claim", max_length=12000))
        object.__setattr__(self, "source_reference_id", _required_text(self.source_reference_id, "source_reference_id"))
        first_seen = _utc(self.first_seen_at, "first_seen_at")
        available = _utc(self.available_at, "available_at")
        if available < first_seen:
            raise EvidenceContractError("available_at cannot precede first_seen_at")
        object.__setattr__(self, "first_seen_at", first_seen)
        object.__setattr__(self, "available_at", available)
        for field in ("published_at", "valid_time_start", "valid_time_end"):
            value = getattr(self, field)
            if value is not None:
                object.__setattr__(self, field, _utc(value, field))
        if self.valid_time_start and self.valid_time_end and self.valid_time_end < self.valid_time_start:
            raise EvidenceContractError("valid_time_end cannot precede valid_time_start")
        ids = tuple(_required_text(item, "entity_id", max_length=256) for item in self.entity_ids)
        if len(ids) != len(set(ids)):
            raise EvidenceContractError("entity_ids cannot contain duplicates")
        object.__setattr__(self, "entity_ids", ids)
        for field in ("instrument_id", "event_id", "unit", "currency",
                      "extraction_method", "extractor_version", "supersedes_evidence_id"):
            object.__setattr__(self, field, _optional_text(getattr(self, field), field, max_length=512))
        if self.currency is not None:
            object.__setattr__(self, "currency", self.currency.upper())
        if self.value is not None:
            object.__setattr__(self, "value", _finite_number(self.value, "value"))
        if self.confidence is not None:
            confidence = _finite_number(self.confidence, "confidence")
            if not 0.0 <= confidence <= 1.0:
                raise EvidenceContractError("confidence must be in [0, 1]")
            object.__setattr__(self, "confidence", confidence)
        if self.evidence_id:
            supplied = _required_text(self.evidence_id, "evidence_id", max_length=64).lower()
            if len(supplied) != 64 or any(ch not in "0123456789abcdef" for ch in supplied):
                raise EvidenceContractError("evidence_id must be a 64-character SHA-256 hex digest")
            if supplied != self.fingerprint:
                raise EvidenceContractError("evidence_id does not match canonical evidence payload")
        else:
            object.__setattr__(self, "evidence_id", self.fingerprint)

    @property
    def fingerprint(self) -> str:
        payload = asdict(self)
        payload.pop("evidence_id", None)
        return canonical_sha256(payload)

    def is_available_at(self, cutoff: datetime) -> bool:
        """Return whether this evidence was available by a point-in-time cutoff."""
        return self.available_at <= _utc(cutoff, "cutoff")

    def to_dict(self) -> dict[str, Any]:
        return _canonical_value(asdict(self))


def evidence_available_at(
    records: Iterable[EvidenceRecord], cutoff: datetime
) -> tuple[EvidenceRecord, ...]:
    """Select only records known to be available at the requested UTC cutoff."""
    point_in_time = _utc(cutoff, "cutoff")
    return tuple(sorted(
        (record for record in records if record.is_available_at(point_in_time)),
        key=lambda record: (record.available_at, record.evidence_id),
    ))


def validate_source_links(
    records: Iterable[EvidenceRecord], sources: Iterable[SourceReference]
) -> tuple[EvidenceRecord, ...]:
    """Fail closed when evidence references an absent or temporally impossible source."""
    source_rows = tuple(sources)
    source_map = {source.source_id: source for source in source_rows}
    if len(source_map) != len(source_rows):
        raise EvidenceContractError("duplicate source_id values")
    validated = tuple(records)
    for record in validated:
        source = source_map.get(record.source_reference_id)
        if source is None:
            raise EvidenceContractError(f"missing source reference: {record.source_reference_id}")
        if record.first_seen_at < source.first_seen_at:
            raise EvidenceContractError("evidence first_seen_at precedes its source first_seen_at")
        if record.available_at < source.first_seen_at:
            raise EvidenceContractError("evidence available_at precedes source availability")
        if (record.published_at is not None and source.published_at is not None
                and record.published_at != source.published_at):
            raise EvidenceContractError("evidence publication timestamp conflicts with source reference")
    return validated



class SourceRegistry:
    """Small in-memory registry for immutable source-reference versions.

    Persistence is deliberately left to a later stage. Re-registering the
    identical source version is idempotent; reusing an ID for changed metadata
    is rejected instead of silently overwriting provenance.
    """

    def __init__(self, sources: Iterable[SourceReference] = ()) -> None:
        self._sources: dict[str, SourceReference] = {}
        self.register_many(sources)

    def register(self, source: SourceReference) -> SourceReference:
        return self.register_many((source,))[0]

    def register_many(
        self, sources: Iterable[SourceReference]
    ) -> tuple[SourceReference, ...]:
        rows = tuple(sources)
        staged = dict(self._sources)
        registered: list[SourceReference] = []
        for source in rows:
            if not isinstance(source, SourceReference):
                raise EvidenceContractError("registry accepts SourceReference instances only")
            existing = staged.get(source.source_id)
            if existing is not None and existing.fingerprint != source.fingerprint:
                raise EvidenceContractError(
                    f"source_id collision with different immutable metadata: {source.source_id}"
                )
            staged[source.source_id] = source
            registered.append(existing or source)
        self._sources = staged
        return tuple(registered)

    def get(self, source_id: str) -> SourceReference | None:
        return self._sources.get(_required_text(source_id, "source_id"))

    def all(self) -> tuple[SourceReference, ...]:
        return tuple(self._sources[key] for key in sorted(self._sources))

    def validate_evidence(
        self, records: Iterable[EvidenceRecord]
    ) -> tuple[EvidenceRecord, ...]:
        return validate_source_links(records, self._sources.values())
