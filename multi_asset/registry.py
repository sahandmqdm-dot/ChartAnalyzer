"""Thread-safe, conflict-safe instrument registry."""
from __future__ import annotations
from threading import RLock
from typing import Iterable
from .instrument import Instrument

class MarketRegistry:
    """Indexes instruments by canonical id and venue-native identity."""
    def __init__(self, instruments: Iterable[Instrument] = ()) -> None:
        self._lock = RLock()
        self._by_id: dict[str, Instrument] = {}
        self._by_venue: dict[tuple[str, str, str], Instrument] = {}
        for item in instruments:
            self.register(item)

    def register(self, instrument: Instrument) -> Instrument:
        if not isinstance(instrument, Instrument):
            raise TypeError("instrument must be an Instrument instance")
        with self._lock:
            old_id, old_venue = self._by_id.get(instrument.canonical_id), self._by_venue.get(instrument.venue_key)
            if old_id is not None and old_id != instrument:
                raise ValueError(f"canonical_id already registered: {instrument.canonical_id}")
            if old_venue is not None and old_venue != instrument:
                raise ValueError(f"venue symbol already registered: {instrument.venue_key}")
            self._by_id[instrument.canonical_id] = instrument
            self._by_venue[instrument.venue_key] = instrument
            return instrument

    def get(self, canonical_id: str) -> Instrument:
        with self._lock:
            try:
                return self._by_id[canonical_id]
            except KeyError as exc:
                raise KeyError(f"unknown instrument: {canonical_id}") from exc

    def get_by_venue(self, venue: str, symbol: str, market_type: str) -> Instrument:
        key = (str(venue).strip().upper(), str(symbol).strip().upper(), str(market_type).strip().lower())
        with self._lock:
            try:
                return self._by_venue[key]
            except KeyError as exc:
                raise KeyError(f"unknown venue instrument: {key}") from exc

    def list(self, *, venue: str | None = None, asset_class: str | None = None) -> tuple[Instrument, ...]:
        with self._lock:
            values = tuple(self._by_id.values())
        if venue is not None:
            values = tuple(x for x in values if x.venue == venue.strip().upper())
        if asset_class is not None:
            values = tuple(x for x in values if x.asset_class.value == str(asset_class).strip().lower())
        return tuple(sorted(values, key=lambda x: x.canonical_id))

    def __len__(self) -> int:
        with self._lock:
            return len(self._by_id)
