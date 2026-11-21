"""Fail-closed preflight for contract-true linear Futures research inputs.

This gate establishes input readiness only. It never authorizes a trade and
does not calculate fills, PnL, funding cashflows or liquidation prices.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, datetime
from typing import Any, Mapping


def _time(value: Any, name: str) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"{name} must be an ISO-8601 timestamp") from exc
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _number(value: Any, name: str, *, positive: bool = False) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be finite numeric")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be finite numeric") from exc
    if not math.isfinite(result) or (positive and result <= 0):
        raise ValueError(f"{name} must be finite" + (" and positive" if positive else ""))
    return result


def _age(now: datetime, observed: Any, name: str, maximum: float, blockers: list[str]) -> None:
    try:
        stamp = _time(observed, name)
    except ValueError as exc:
        blockers.append(str(exc))
        return
    seconds = (now - stamp).total_seconds()
    if seconds < 0:
        blockers.append(f"{name} is in the future")
    elif seconds > maximum:
        blockers.append(f"{name} is stale ({seconds:.3f}s > {maximum:.3f}s)")


def evaluate_futures_reality_gate(
    *,
    instrument_id: str,
    venue: str,
    as_of: datetime,
    contract: Mapping[str, Any],
    prices: Mapping[str, Any],
    funding: Mapping[str, Any],
    execution: Mapping[str, Any],
    margin_model_status: str,
    max_contract_age_seconds: float = 86400.0,
    max_market_age_seconds: float = 5.0,
    max_funding_age_seconds: float = 900.0,
) -> dict[str, Any]:
    """Check whether required Futures inputs are sufficiently identified/fresh.

    READY_FOR_RESEARCH means only that this preflight found no missing/stale
    critical inputs. It is not a signal, fill estimate, profitability result,
    exchange liquidation oracle, paper authorization or order authorization.
    """
    now = _time(as_of, "as_of")
    symbol = str(instrument_id).strip().upper()
    exchange = str(venue).strip().lower()
    if not symbol or not exchange:
        raise ValueError("instrument_id and venue are required")

    for name, value in (
        ("max_contract_age_seconds", max_contract_age_seconds),
        ("max_market_age_seconds", max_market_age_seconds),
        ("max_funding_age_seconds", max_funding_age_seconds),
    ):
        _number(value, name, positive=True)

    blockers: list[str] = []
    warnings: list[str] = []

    contract_required = (
        "exchange", "symbol", "market_type", "contract_type", "quote_asset",
        "margin_asset", "settlement_asset", "contract_multiplier", "tick_size",
        "step_size", "min_qty", "min_notional", "max_qty", "max_leverage",
        "maintenance_margin_tiers", "source", "observed_at",
    )
    missing = [key for key in contract_required if contract.get(key) is None]
    if missing:
        blockers.append("contract metadata missing: " + ", ".join(missing))
    else:
        if str(contract["exchange"]).lower() != exchange:
            blockers.append("contract venue does not match requested venue")
        if str(contract["symbol"]).upper() != symbol:
            blockers.append("contract symbol does not match requested instrument")
        if str(contract["market_type"]).lower() not in {"futures", "linear_perpetual"}:
            blockers.append("contract market_type is not a supported linear Futures market")
        if str(contract.get("status", "TRADING")).upper() != "TRADING":
            blockers.append("contract is not in TRADING status")
        if str(contract["contract_type"]).lower() != "linear":
            blockers.append("only linear contracts are supported by this gate")
        if not str(contract["source"]).strip():
            blockers.append("contract metadata source is missing")
        for key in ("contract_multiplier", "tick_size", "step_size", "min_qty",
                    "min_notional", "max_qty", "max_leverage"):
            try:
                _number(contract[key], f"contract.{key}", positive=True)
            except ValueError as exc:
                blockers.append(str(exc))
        tiers = contract["maintenance_margin_tiers"]
        if not isinstance(tiers, list) or not tiers:
            blockers.append("maintenance margin tiers are missing")
        try:
            effective_value = contract.get("effective_from", contract.get("effective_at"))
            if effective_value is None:
                blockers.append("contract effective timestamp is missing")
            else:
                effective = _time(effective_value, "contract.effective_from")
                if effective > now:
                    blockers.append("contract metadata is not yet effective")
        except ValueError as exc:
            blockers.append(str(exc))
        _age(now, contract["observed_at"], "contract.observed_at",
             float(max_contract_age_seconds), blockers)

    price_required = ("exchange", "symbol", "market_type", "last", "mark", "index",
                      "bid", "ask", "source", "observed_at")
    missing = [key for key in price_required if prices.get(key) is None]
    if missing:
        blockers.append("price snapshot missing: " + ", ".join(missing))
    else:
        if str(prices["exchange"]).lower() != exchange:
            blockers.append("price venue does not match requested venue")
        if str(prices["symbol"]).upper() != symbol:
            blockers.append("price symbol does not match requested instrument")
        if str(prices["market_type"]).lower() != "futures":
            blockers.append("price market_type is not futures")
        if not str(prices["source"]).strip():
            blockers.append("price snapshot source is missing")
        values: dict[str, float] = {}
        for key in ("last", "mark", "index", "bid", "ask"):
            try:
                values[key] = _number(prices[key], f"prices.{key}", positive=True)
            except ValueError as exc:
                blockers.append(str(exc))
        if "bid" in values and "ask" in values and values["bid"] > values["ask"]:
            blockers.append("best bid exceeds best ask")
        _age(now, prices["observed_at"], "prices.observed_at",
             float(max_market_age_seconds), blockers)

    funding_required = ("exchange", "symbol", "market_type", "funding_rate",
                        "observed_at", "next_funding_time", "source")
    missing = [key for key in funding_required if funding.get(key) is None]
    if missing:
        blockers.append("funding context missing: " + ", ".join(missing))
    else:
        if str(funding["exchange"]).lower() != exchange:
            blockers.append("funding venue does not match requested venue")
        if str(funding["symbol"]).upper() != symbol:
            blockers.append("funding symbol does not match requested instrument")
        if str(funding["market_type"]).lower() != "futures":
            blockers.append("funding market_type is not futures")
        if not str(funding["source"]).strip():
            blockers.append("funding source is missing")
        try:
            _number(funding["funding_rate"], "funding.funding_rate")
            next_funding = _time(funding["next_funding_time"], "funding.next_funding_time")
            if next_funding <= now:
                blockers.append("next funding timestamp is not in the future")
        except ValueError as exc:
            blockers.append(str(exc))
        _age(now, funding["observed_at"], "funding.observed_at",
             float(max_funding_age_seconds), blockers)

    for key in ("fee_schedule_source", "fee_schedule_version", "impact_model_source",
                "impact_model_version"):
        if not str(execution.get(key, "")).strip():
            blockers.append(f"execution.{key} is missing")
    for key in ("maker_fee_rate", "taker_fee_rate"):
        try:
            value = _number(execution.get(key), f"execution.{key}")
            if value < 0:
                blockers.append(f"execution.{key} cannot be negative")
        except ValueError as exc:
            blockers.append(str(exc))
    if str(margin_model_status).lower() != "exchange_tier_aware":
        blockers.append("margin model is not verified as exchange-tier-aware")

    # Content identity for audit linkage. This is a digest, not a signature.
    try:
        canonical = json.dumps(dict(contract), sort_keys=True, separators=(",", ":"),
                               default=lambda value: _time(value, "contract timestamp").isoformat())
        contract_digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    except (TypeError, ValueError):
        contract_digest = None
        blockers.append("contract metadata cannot be canonically fingerprinted")

    status = "READY_FOR_RESEARCH" if not blockers else "NOT_READY"
    return {
        "schema": "futures_reality_gate_v1",
        "status": status,
        "research_ready": not blockers,
        "actionable": False,
        "deployment_authorized": False,
        "venue": exchange,
        "instrument_id": symbol,
        "contract_fingerprint": contract_digest,
        "blockers": blockers,
        "warnings": warnings + [
            "Preflight readiness is not a forecast, trade signal, execution simulation or profitability claim.",
            "Funding context is not a substitute for booking actual funding settlements over a holding period.",
            "This gate does not calculate exchange liquidation, queue position or realized fills.",
        ],
    }


__all__ = ["evaluate_futures_reality_gate"]
