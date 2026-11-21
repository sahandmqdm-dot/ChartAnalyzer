"""Append-only, point-in-time registry for normalized linear Futures contracts.

This registry stores versioned exchange metadata. It is not an exchange API
adapter and does not certify that a source is truthful; callers must preserve
the source response and provenance separately.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from collections.abc import Mapping
from datetime import datetime, UTC
from typing import Any

from futures_exchange_rules import validate_futures_rules
from tiered_margin_engine import validate_margin_tiers


def _utc(value: Any, name: str) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"{name} must be ISO-8601") from exc
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _canonical(value: Any) -> str:
    def encode(item: Any) -> Any:
        if isinstance(item, datetime):
            return _utc(item, "timestamp").isoformat()
        if isinstance(item, Mapping):
            return {str(k): encode(v) for k, v in item.items()}
        if isinstance(item, (list, tuple)):
            return [encode(v) for v in item]
        if isinstance(item, (str, int, float, bool)) or item is None:
            return item
        raise TypeError(f"unsupported contract metadata value: {type(item).__name__}")
    return json.dumps(encode(value), sort_keys=True, separators=(",", ":"), allow_nan=False)


@dataclass(frozen=True, slots=True)
class ContractSnapshot:
    venue: str
    instrument_id: str
    market_type: str
    effective_from: datetime
    effective_to: datetime | None
    observed_at: datetime
    source: str
    version: str
    fingerprint: str
    payload_json: str

    def payload(self) -> dict[str, Any]:
        return json.loads(self.payload_json)


class ContractRegistry:
    """In-memory append-only registry with overlap and freshness protection.

    Persistence is deliberately left to a caller-owned durable store. A process
    restart must reload verified snapshots; this class never silently invents
    or fetches exchange rules.
    """

    def __init__(self) -> None:
        self._snapshots: list[ContractSnapshot] = []

    @property
    def snapshots(self) -> tuple[ContractSnapshot, ...]:
        return tuple(self._snapshots)

    def register(self, metadata: Mapping[str, Any]) -> ContractSnapshot:
        if not isinstance(metadata, Mapping):
            raise TypeError("metadata must be a mapping")
        raw = dict(metadata)
        required = (
            "exchange", "symbol", "market_type", "contract_type", "status",
            "quote_asset", "margin_asset", "settlement_asset", "contract_multiplier",
            "tick_size", "step_size", "min_qty", "max_qty", "min_notional",
            "max_leverage", "maintenance_margin_tiers", "source", "version",
            "observed_at", "effective_from",
        )
        missing = [key for key in required if raw.get(key) is None]
        if missing:
            raise ValueError("contract metadata missing: " + ", ".join(missing))

        venue = str(raw["exchange"]).strip().lower()
        symbol = str(raw["symbol"]).strip().upper()
        market_type = str(raw["market_type"]).strip().lower()
        if not venue or not symbol or market_type not in {"futures", "linear_perpetual"}:
            raise ValueError("venue, symbol and supported Futures market_type are required")
        if str(raw["contract_type"]).lower() != "linear":
            raise ValueError("only linear Futures contracts are supported")
        if not str(raw["source"]).strip() or not str(raw["version"]).strip():
            raise ValueError("source and version must be non-empty")
        if str(raw["status"]).upper() not in {"TRADING", "HALT", "REDUCE_ONLY", "DELISTED"}:
            raise ValueError("unsupported contract status")

        observed = _utc(raw["observed_at"], "observed_at")
        start = _utc(raw["effective_from"], "effective_from")
        end = _utc(raw["effective_to"], "effective_to") if raw.get("effective_to") is not None else None
        if end is not None and end <= start:
            raise ValueError("effective_to must be later than effective_from")
        # A contract rule may be published before its effective timestamp.
        # Such a snapshot is stored now but resolve() will not expose it early.

        rules = {
            **raw,
            "symbol": symbol,
            "status": str(raw["status"]).upper(),
            "contract_type": "linear",
        }
        # Reuse the existing exchange-filter validator, rather than maintaining
        # a second independent implementation of tick/lot/tier constraints.
        # Validate the mechanical filters independently of trading status so
        # halted/reduce-only/delisted states remain representable in history.
        # The original status is preserved in the immutable snapshot and must
        # be checked by every downstream decision gate.
        validate_futures_rules({**rules, "status": "TRADING"})
        margin_tiers = validate_margin_tiers(raw["maintenance_margin_tiers"])
        for key in ("contract_multiplier",):
            try:
                number = float(raw[key])
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError(f"{key} must be finite and positive") from exc
            if not (number > 0 and number < float("inf")):
                raise ValueError(f"{key} must be finite and positive")
        if not str(raw["quote_asset"]).strip() or not str(raw["margin_asset"]).strip() or not str(raw["settlement_asset"]).strip():
            raise ValueError("quote, margin and settlement assets are required")

        normalized = dict(raw)
        normalized.update({
            "exchange": venue, "symbol": symbol, "market_type": market_type,
            "contract_type": "linear", "status": str(raw["status"]).upper(),
            "observed_at": observed, "effective_from": start, "effective_to": end,
            "maintenance_margin_tiers": margin_tiers,
        })
        payload_json = _canonical(normalized)
        fingerprint = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()

        for existing in self._snapshots:
            same_identity = (existing.venue, existing.instrument_id, existing.market_type) == (venue, symbol, market_type)
            if not same_identity:
                continue
            old_end = existing.effective_to or datetime.max.replace(tzinfo=UTC)
            new_end = end or datetime.max.replace(tzinfo=UTC)
            if start < old_end and existing.effective_from < new_end:
                if existing.fingerprint == fingerprint:
                    return existing
                raise ValueError("effective interval overlaps an existing contract snapshot")

        snapshot = ContractSnapshot(
            venue=venue, instrument_id=symbol, market_type=market_type,
            effective_from=start, effective_to=end, observed_at=observed,
            source=str(raw["source"]), version=str(raw["version"]),
            fingerprint=fingerprint, payload_json=payload_json,
        )
        self._snapshots.append(snapshot)
        self._snapshots.sort(key=lambda item: (item.venue, item.instrument_id, item.market_type, item.effective_from))
        return snapshot

    def resolve(
        self, *, venue: str, instrument_id: str, market_type: str,
        as_of: datetime, max_observation_age_seconds: float = 86400,
    ) -> dict[str, Any]:
        point = _utc(as_of, "as_of")
        try:
            max_age = float(max_observation_age_seconds)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("max_observation_age_seconds must be finite and positive") from exc
        if not (0 < max_age < float("inf")):
            raise ValueError("max_observation_age_seconds must be finite and positive")
        identity = (str(venue).strip().lower(), str(instrument_id).strip().upper(), str(market_type).strip().lower())
        matches = [
            item for item in self._snapshots
            if (item.venue, item.instrument_id, item.market_type) == identity
            and item.effective_from <= point
            and (item.effective_to is None or point < item.effective_to)
        ]
        if not matches:
            return {"status": "CONTRACT_NOT_FOUND", "contract": None, "fingerprint": None}
        selected = max(matches, key=lambda item: item.effective_from)
        age = (point - selected.observed_at).total_seconds()
        if age < 0:
            return {"status": "CONTRACT_OBSERVATION_FROM_FUTURE", "contract": None, "fingerprint": selected.fingerprint}
        if age > max_age:
            return {"status": "CONTRACT_STALE", "contract": None, "fingerprint": selected.fingerprint,
                    "observation_age_seconds": age}
        payload = selected.payload()
        payload["registry_fingerprint"] = selected.fingerprint
        status = "CONTRACT_RESOLVED" if payload["status"] == "TRADING" else "CONTRACT_NOT_TRADING"
        return {"status": status, "contract": payload,
                "fingerprint": selected.fingerprint, "observation_age_seconds": age}


__all__ = ["ContractRegistry", "ContractSnapshot"]
