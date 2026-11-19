"""Canonical instrument identity shared by Crypto and US equities."""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
import math
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

class AssetClass(str, Enum):
    CRYPTO_SPOT = "crypto_spot"
    CRYPTO_FUTURES = "crypto_futures"
    EQUITY = "equity"
    ETF = "etf"
    INDEX = "index"
    OPTION = "option"

class MarketType(str, Enum):
    SPOT = "spot"
    LINEAR_FUTURES = "linear_futures"
    INVERSE_FUTURES = "inverse_futures"
    EQUITY_CASH = "equity_cash"
    OPTION = "option"
    INDEX = "index"

class InstrumentStatus(str, Enum):
    ACTIVE = "active"
    HALTED = "halted"
    DELISTED = "delisted"
    UNKNOWN = "unknown"

_SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9._:/-]{0,31}$")
_CURRENCY_RE = re.compile(r"^[A-Z][A-Z0-9]{1,9}$")

@dataclass(frozen=True, slots=True)
class Instrument:
    """Venue-specific instrument identity; symbols are never assumed global."""
    canonical_id: str
    symbol: str
    venue: str
    asset_class: AssetClass
    market_type: MarketType
    base_currency: str
    quote_currency: str
    timezone: str = "UTC"
    status: InstrumentStatus = InstrumentStatus.UNKNOWN
    contract_multiplier: float = 1.0
    contract_type: str | None = None

    def __post_init__(self) -> None:
        try:
            asset_class = AssetClass(self.asset_class)
            market_type = MarketType(self.market_type)
            status = InstrumentStatus(self.status)
        except ValueError as exc:
            raise ValueError("asset_class, market_type and status must use supported values") from exc
        cid, symbol, venue = str(self.canonical_id).strip(), str(self.symbol).strip().upper(), str(self.venue).strip().upper()
        if not cid or len(cid) > 96:
            raise ValueError("canonical_id must contain 1..96 characters")
        if not _SYMBOL_RE.fullmatch(symbol):
            raise ValueError("symbol contains unsupported characters or has invalid length")
        if not venue or len(venue) > 32:
            raise ValueError("venue must contain 1..32 characters")
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, TypeError) as exc:
            raise ValueError("timezone must be a valid IANA timezone") from exc
        base, quote = str(self.base_currency).strip().upper(), str(self.quote_currency).strip().upper()
        if not _CURRENCY_RE.fullmatch(base) or not _CURRENCY_RE.fullmatch(quote):
            raise ValueError("base_currency and quote_currency must be valid currency codes")
        try:
            multiplier = float(self.contract_multiplier)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("contract_multiplier must be positive and finite") from exc
        if not math.isfinite(multiplier) or multiplier <= 0:
            raise ValueError("contract_multiplier must be positive and finite")
        if asset_class == AssetClass.CRYPTO_FUTURES:
            if market_type not in {MarketType.LINEAR_FUTURES, MarketType.INVERSE_FUTURES}:
                raise ValueError("crypto futures require a futures market_type")
            if self.contract_type not in {"linear", "inverse"}:
                raise ValueError("crypto futures require contract_type linear or inverse")
            if (self.contract_type == "linear") != (market_type == MarketType.LINEAR_FUTURES):
                raise ValueError("contract_type and market_type are inconsistent")
        elif market_type in {MarketType.LINEAR_FUTURES, MarketType.INVERSE_FUTURES}:
            raise ValueError("futures market_type requires crypto_futures asset_class")
        elif self.contract_type is not None:
            raise ValueError("contract_type is only valid for futures instruments")
        object.__setattr__(self, "asset_class", asset_class)
        object.__setattr__(self, "market_type", market_type)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "canonical_id", cid)
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "venue", venue)
        object.__setattr__(self, "base_currency", base)
        object.__setattr__(self, "quote_currency", quote)
        object.__setattr__(self, "contract_multiplier", multiplier)

    @property
    def venue_key(self) -> tuple[str, str, str]:
        return (self.venue, self.symbol, self.market_type.value)

    @property
    def is_crypto(self) -> bool:
        return self.asset_class in {AssetClass.CRYPTO_SPOT, AssetClass.CRYPTO_FUTURES}

    @property
    def is_us_equity(self) -> bool:
        return self.asset_class in {AssetClass.EQUITY, AssetClass.ETF} and self.venue in {"NASDAQ", "NYSE", "NYSE_ARCA"}
