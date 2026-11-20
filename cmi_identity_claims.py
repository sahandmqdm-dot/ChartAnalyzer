"""Point-in-time entity resolution and evidence-grounded claim contracts.

This module deliberately reuses the existing multi_asset Instrument and
MarketRegistry for venue-specific instruments. It does not create or merge
instrument identities, ingest sources, invoke an LLM, or authorize trading.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from collections.abc import Iterable
from enum import Enum
from threading import RLock

from evidence_contracts import EvidenceRecord, canonical_sha256
from multi_asset import MarketRegistry


class IdentityContractError(ValueError):
    """An entity, alias, or claim violates its canonical contract."""


def _text(value: str, field: str, limit: int = 256) -> str:
    if not isinstance(value, str):
        raise IdentityContractError(f"{field} must be a string")
    value = value.strip()
    if not value or len(value) > limit:
        raise IdentityContractError(f"{field} must contain 1..{limit} characters")
    return value


def _utc(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise IdentityContractError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


def _confidence(value: float) -> float:
    if isinstance(value, bool):
        raise IdentityContractError("confidence must be finite and in [0, 1]")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise IdentityContractError("confidence must be finite and in [0, 1]") from exc
    if not math.isfinite(result) or not 0 <= result <= 1:
        raise IdentityContractError("confidence must be finite and in [0, 1]")
    return result


class EntityType(str, Enum):
    ASSET = "asset"
    ISSUER = "issuer"
    PROTOCOL = "protocol"
    MACRO_INDICATOR = "macro_indicator"
    PERSON = "person"
    ORGANIZATION = "organization"
    EVENT = "event"


class ResolutionStatus(str, Enum):
    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"
    AMBIGUOUS = "ambiguous"


class ResolutionMethod(str, Enum):
    CANONICAL_ID = "canonical_id"
    NAMESPACE_ALIAS = "namespace_alias"
    VENUE_INSTRUMENT = "venue_instrument"


@dataclass(frozen=True, slots=True)
class EntityRecord:
    entity_id: str
    entity_type: EntityType
    display_name: str
    recorded_at: datetime
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    instrument_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "entity_id", _text(self.entity_id, "entity_id"))
        object.__setattr__(self, "display_name", _text(self.display_name, "display_name"))
        try:
            object.__setattr__(self, "entity_type", EntityType(self.entity_type))
        except ValueError as exc:
            raise IdentityContractError("unsupported entity_type") from exc
        object.__setattr__(self, "recorded_at", _utc(self.recorded_at, "recorded_at"))
        for field in ("valid_from", "valid_to"):
            value = getattr(self, field)
            if value is not None:
                object.__setattr__(self, field, _utc(value, field))
        if self.valid_from and self.valid_to and self.valid_to <= self.valid_from:
            raise IdentityContractError("valid_to must be later than valid_from")
        if self.instrument_id is not None:
            object.__setattr__(self, "instrument_id", _text(self.instrument_id, "instrument_id"))

    def is_valid_at(self, instant: datetime) -> bool:
        instant = _utc(instant, "instant")
        return ((self.valid_from is None or self.valid_from <= instant)
                and (self.valid_to is None or instant < self.valid_to))


@dataclass(frozen=True, slots=True)
class EntityAlias:
    entity_id: str
    namespace: str
    value: str
    recorded_at: datetime
    provider: str | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None

    def __post_init__(self) -> None:
        for field in ("entity_id", "namespace", "value"):
            object.__setattr__(self, field, _text(getattr(self, field), field))
        if self.provider is not None:
            object.__setattr__(self, "provider", _text(self.provider, "provider"))
        object.__setattr__(self, "recorded_at", _utc(self.recorded_at, "recorded_at"))
        for field in ("valid_from", "valid_to"):
            value = getattr(self, field)
            if value is not None:
                object.__setattr__(self, field, _utc(value, field))
        if self.valid_from and self.valid_to and self.valid_to <= self.valid_from:
            raise IdentityContractError("alias valid_to must be later than valid_from")

    @property
    def key(self) -> tuple[str, str, str | None, str]:
        return (self.namespace.casefold(), self.value.casefold(),
                self.provider.casefold() if self.provider else None, self.entity_id)

    def is_valid_at(self, instant: datetime) -> bool:
        instant = _utc(instant, "instant")
        return ((self.valid_from is None or self.valid_from <= instant)
                and (self.valid_to is None or instant < self.valid_to))


@dataclass(frozen=True, slots=True)
class EntityResolutionResult:
    status: ResolutionStatus
    query: str
    candidate_entity_ids: tuple[str, ...]
    method: ResolutionMethod | None = None
    confidence: float = 0.0
    reason: str = ""


class EntityResolver:
    """Conservative resolver: exact aliases only; ambiguity never auto-merges."""

    def __init__(
        self,
        entities: Iterable[EntityRecord] = (),
        aliases: Iterable[EntityAlias] = (),
        market_registry: MarketRegistry | None = None,
    ) -> None:
        self._lock = RLock()
        self._entities: dict[str, EntityRecord] = {}
        self._aliases: list[EntityAlias] = []
        self._market_registry = market_registry
        self.register_many(entities, aliases)

    def register(self, entity: EntityRecord, aliases: Iterable[EntityAlias] = ()) -> EntityRecord:
        self.register_many((entity,), aliases)
        return entity

    def register_many(
        self, entities: Iterable[EntityRecord], aliases: Iterable[EntityAlias] = ()
    ) -> tuple[EntityRecord, ...]:
        entity_rows, alias_rows = tuple(entities), tuple(aliases)
        with self._lock:
            staged_entities = dict(self._entities)
            staged_aliases = list(self._aliases)
            for entity in entity_rows:
                if not isinstance(entity, EntityRecord):
                    raise IdentityContractError("entities must be EntityRecord instances")
                old = staged_entities.get(entity.entity_id)
                if old is not None and old != entity:
                    raise IdentityContractError(f"entity_id collision: {entity.entity_id}")
                if entity.instrument_id and self._market_registry is not None:
                    try:
                        self._market_registry.get(entity.instrument_id)
                    except KeyError as exc:
                        raise IdentityContractError(
                            f"unknown canonical instrument: {entity.instrument_id}"
                        ) from exc
                staged_entities[entity.entity_id] = entity
            for alias in alias_rows:
                if not isinstance(alias, EntityAlias):
                    raise IdentityContractError("aliases must be EntityAlias instances")
                if alias.entity_id not in staged_entities:
                    raise IdentityContractError(f"alias references unknown entity: {alias.entity_id}")
                collision = next((item for item in staged_aliases if item.key == alias.key), None)
                if collision is not None and collision != alias:
                    raise IdentityContractError("alias key collision with different validity metadata")
                if collision is None:
                    staged_aliases.append(alias)
            self._entities, self._aliases = staged_entities, staged_aliases
            return tuple(staged_entities[e.entity_id] for e in entity_rows)

    def resolve(
        self, value: str, *, namespace: str, as_of: datetime,
        provider: str | None = None,
    ) -> EntityResolutionResult:
        value, namespace = _text(value, "value"), _text(namespace, "namespace")
        as_of = _utc(as_of, "as_of")
        provider_key = provider.casefold() if provider else None
        with self._lock:
            if (value in self._entities
                    and self._entities[value].recorded_at <= as_of
                    and self._entities[value].is_valid_at(as_of)):
                return EntityResolutionResult(
                    ResolutionStatus.RESOLVED, value, (value,),
                    ResolutionMethod.CANONICAL_ID, 1.0, "exact canonical identifier",
                )
            matches = {
                alias.entity_id for alias in self._aliases
                if alias.namespace.casefold() == namespace.casefold()
                and alias.value.casefold() == value.casefold()
                and (alias.provider is None or alias.provider.casefold() == provider_key)
                and alias.recorded_at <= as_of
                and alias.is_valid_at(as_of)
                and self._entities[alias.entity_id].recorded_at <= as_of
                and self._entities[alias.entity_id].is_valid_at(as_of)
            }
        candidates = tuple(sorted(matches))
        if len(candidates) == 1:
            return EntityResolutionResult(
                ResolutionStatus.RESOLVED, value, candidates,
                ResolutionMethod.NAMESPACE_ALIAS, 1.0, "unique exact namespace alias",
            )
        if len(candidates) > 1:
            return EntityResolutionResult(
                ResolutionStatus.AMBIGUOUS, value, candidates, None, 0.0,
                "multiple exact matches; explicit disambiguation required",
            )
        return EntityResolutionResult(
            ResolutionStatus.UNRESOLVED, value, (), None, 0.0,
            "no valid exact alias at the requested point in time",
        )

    def get(self, entity_id: str) -> EntityRecord:
        with self._lock:
            try:
                return self._entities[entity_id]
            except KeyError as exc:
                raise KeyError(f"unknown entity: {entity_id}") from exc

    def resolve_instrument(
        self, *, venue: str, symbol: str, market_type: str
    ) -> EntityResolutionResult:
        """Resolve through the existing venue-scoped MarketRegistry.

        The returned candidate is the existing canonical instrument ID, not
        a newly created entity or a fuzzy ticker match.
        """
        query = f"{venue}:{symbol}:{market_type}"
        if self._market_registry is None:
            return EntityResolutionResult(
                ResolutionStatus.UNRESOLVED, query, (), None, 0.0,
                "MarketRegistry was not supplied",
            )
        try:
            instrument = self._market_registry.get_by_venue(venue, symbol, market_type)
        except KeyError:
            return EntityResolutionResult(
                ResolutionStatus.UNRESOLVED, query, (), None, 0.0,
                "unknown venue-scoped instrument",
            )
        return EntityResolutionResult(
            ResolutionStatus.RESOLVED, query, (instrument.canonical_id,),
            ResolutionMethod.VENUE_INSTRUMENT, 1.0,
            "resolved by existing canonical MarketRegistry",
        )


class ClaimModality(str, Enum):
    FACT = "fact"
    OPINION = "opinion"
    HYPOTHESIS = "hypothesis"
    FORECAST = "forecast"


class ClaimStance(str, Enum):
    SUPPORTS = "supports"
    OPPOSES = "opposes"
    NEUTRAL = "neutral"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True, slots=True)
class ClaimRecord:
    subject_entity_id: str
    predicate: str
    object_text: str
    modality: ClaimModality
    stance: ClaimStance
    available_at: datetime
    evidence_ids: tuple[str, ...]
    confidence: float | None = None
    valid_time_start: datetime | None = None
    valid_time_end: datetime | None = None
    extraction_method: str | None = None
    extractor_version: str | None = None
    claim_id: str = ""

    def __post_init__(self) -> None:
        for field in ("subject_entity_id", "predicate", "object_text"):
            object.__setattr__(self, field, _text(getattr(self, field), field, 4000))
        try:
            object.__setattr__(self, "modality", ClaimModality(self.modality))
            object.__setattr__(self, "stance", ClaimStance(self.stance))
        except ValueError as exc:
            raise IdentityContractError("unsupported claim modality or stance") from exc
        object.__setattr__(self, "available_at", _utc(self.available_at, "available_at"))
        ids = tuple(_text(item, "evidence_id", 64) for item in self.evidence_ids)
        if not ids or len(ids) != len(set(ids)):
            raise IdentityContractError("claim requires one or more unique evidence_ids")
        object.__setattr__(self, "evidence_ids", ids)
        if self.confidence is not None:
            object.__setattr__(self, "confidence", _confidence(self.confidence))
        for field in ("valid_time_start", "valid_time_end"):
            value = getattr(self, field)
            if value is not None:
                object.__setattr__(self, field, _utc(value, field))
        if self.valid_time_start and self.valid_time_end and self.valid_time_end <= self.valid_time_start:
            raise IdentityContractError("claim valid_time_end must be later than valid_time_start")
        for field in ("extraction_method", "extractor_version"):
            value = getattr(self, field)
            if value is not None:
                object.__setattr__(self, field, _text(value, field))
        expected = self.fingerprint
        if self.claim_id and self.claim_id != expected:
            raise IdentityContractError("claim_id does not match canonical claim payload")
        object.__setattr__(self, "claim_id", expected)

    @property
    def fingerprint(self) -> str:
        payload = asdict(self)
        payload.pop("claim_id", None)
        return canonical_sha256(payload)


class ClaimRegistry:
    """Atomic in-memory claim registry with entity/evidence and PIT validation."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._claims: dict[str, ClaimRecord] = {}

    def register_many(
        self, claims: Iterable[ClaimRecord], *,
        entities: Iterable[EntityRecord], evidence: Iterable[EvidenceRecord],
    ) -> tuple[ClaimRecord, ...]:
        claim_rows, entity_rows, evidence_rows = tuple(claims), tuple(entities), tuple(evidence)
        entities_by_id = {item.entity_id: item for item in entity_rows}
        if len(entities_by_id) != len(entity_rows):
            raise IdentityContractError("duplicate entity_id values")
        evidence_by_id = {item.evidence_id: item for item in evidence_rows}
        if len(evidence_by_id) != len(evidence_rows):
            raise IdentityContractError("duplicate evidence_id values")
        with self._lock:
            staged = dict(self._claims)
            for claim in claim_rows:
                if not isinstance(claim, ClaimRecord):
                    raise IdentityContractError("claims must be ClaimRecord instances")
                subject = entities_by_id.get(claim.subject_entity_id)
                if subject is None:
                    raise IdentityContractError(f"unknown claim subject: {claim.subject_entity_id}")
                if subject.recorded_at > claim.available_at or not subject.is_valid_at(claim.available_at):
                    raise IdentityContractError("claim subject is not point-in-time valid")
                for evidence_id in claim.evidence_ids:
                    record = evidence_by_id.get(evidence_id)
                    if record is None:
                        raise IdentityContractError(f"missing claim evidence: {evidence_id}")
                    if record.available_at > claim.available_at:
                        raise IdentityContractError("claim available_at precedes cited evidence availability")
                    if record.entity_ids and claim.subject_entity_id not in record.entity_ids:
                        raise IdentityContractError("claim subject is not linked by cited evidence")
                old = staged.get(claim.claim_id)
                if old is not None and old != claim:
                    raise IdentityContractError(f"claim_id collision: {claim.claim_id}")
                staged[claim.claim_id] = claim
            self._claims = staged
            return tuple(staged[item.claim_id] for item in claim_rows)

    def get(self, claim_id: str) -> ClaimRecord | None:
        with self._lock:
            return self._claims.get(claim_id)

    def all(self) -> tuple[ClaimRecord, ...]:
        with self._lock:
            return tuple(self._claims[key] for key in sorted(self._claims))
