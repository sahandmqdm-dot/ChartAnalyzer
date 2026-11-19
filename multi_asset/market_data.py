"""Provider-neutral OHLCV contracts and strict normalization."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from enum import Enum
import math
from typing import Any, Iterable, Mapping
from .instrument import Instrument

class CandleInterval(str, Enum):
    M1="1m"; M5="5m"; M15="15m"; M30="30m"; H1="1h"; H4="4h"; H12="12h"; D1="1d"; W1="1w"

_INTERVAL_SECONDS = {
    CandleInterval.M1:60, CandleInterval.M5:300, CandleInterval.M15:900,
    CandleInterval.M30:1800, CandleInterval.H1:3600, CandleInterval.H4:14400,
    CandleInterval.H12:43200, CandleInterval.D1:86400, CandleInterval.W1:604800,
}

def _number(name: str, value: Any) -> float:
    try: result = float(value)
    except (TypeError, ValueError, OverflowError) as exc: raise ValueError(f"{name} must be numeric") from exc
    if not math.isfinite(result): raise ValueError(f"{name} must be finite")
    return result

def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc)

@dataclass(frozen=True, slots=True)
class Candle:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    quote_volume: float | None = None
    trade_count: int | None = None

    def __post_init__(self) -> None:
        ts = _utc(self.timestamp)
        o,h,l,c,v = (_number(k, getattr(self,k)) for k in ("open","high","low","close","volume"))
        if min(o,h,l,c) <= 0: raise ValueError("OHLC prices must be > 0")
        if h < max(o,c,l) or l > min(o,c,h): raise ValueError("OHLC relationship is invalid")
        if v < 0: raise ValueError("volume must be >= 0")
        qv = None if self.quote_volume is None else _number("quote_volume", self.quote_volume)
        if qv is not None and qv < 0: raise ValueError("quote_volume must be >= 0")
        if self.trade_count is not None and (not isinstance(self.trade_count,int) or self.trade_count < 0):
            raise ValueError("trade_count must be a non-negative integer")
        object.__setattr__(self,"timestamp",ts)
        for key,val in zip(("open","high","low","close","volume"),(o,h,l,c,v)): object.__setattr__(self,key,val)
        object.__setattr__(self,"quote_volume",qv)

@dataclass(frozen=True, slots=True)
class NormalizedCandleSeries:
    instrument_id: str
    interval: CandleInterval
    candles: tuple[Candle,...]
    source: str
    gaps: tuple[datetime,...] = ()

    def __post_init__(self) -> None:
        if not self.instrument_id or not self.source: raise ValueError("instrument_id and source are required")
        if not self.candles: raise ValueError("candle series cannot be empty")
        if any(not isinstance(c,Candle) for c in self.candles): raise TypeError("all series elements must be Candle instances")
        stamps=[c.timestamp for c in self.candles]
        if stamps != sorted(stamps) or len(set(stamps)) != len(stamps):
            raise ValueError("candles must be strictly chronological without duplicates")

class MarketDataNormalizer:
    """Normalize provider rows to UTC. Never fabricate or fill missing bars."""
    _ALIASES = {
        "timestamp":("timestamp","time","datetime","t"), "open":("open","o"),
        "high":("high","h"), "low":("low","l"), "close":("close","c"),
        "volume":("volume","vol","v"), "quote_volume":("quote_volume","quoteVolume"),
        "trade_count":("trade_count","trades","n"),
    }

    @staticmethod
    def _timestamp(value: Any) -> datetime:
        if isinstance(value,datetime): return _utc(value)
        if isinstance(value,(int,float)) and math.isfinite(float(value)):
            raw=float(value); seconds=raw/1000.0 if abs(raw)>=100_000_000_000 else raw
            try: return datetime.fromtimestamp(seconds,tz=timezone.utc)
            except (OverflowError,OSError,ValueError) as exc: raise ValueError("timestamp is outside supported epoch range") from exc
        if isinstance(value,str):
            try: parsed=datetime.fromisoformat(value.strip().replace("Z","+00:00"))
            except ValueError as exc: raise ValueError("timestamp string must be ISO-8601") from exc
            return _utc(parsed)
        raise ValueError("timestamp must be aware datetime, epoch seconds/ms, or ISO-8601")

    @classmethod
    def _pick(cls,row: Mapping[str,Any],name: str,required: bool=True) -> Any:
        for alias in cls._ALIASES[name]:
            if alias in row: return row[alias]
        if required: raise ValueError(f"missing required market-data field: {name}")
        return None

    @classmethod
    def candle(cls,row: Mapping[str,Any]) -> Candle:
        if not isinstance(row,Mapping): raise TypeError("each market-data row must be a mapping")
        ts=cls._timestamp(cls._pick(row,"timestamp"))
        vals={name:cls._pick(row,name) for name in ("open","high","low","close","volume")}
        qv=cls._pick(row,"quote_volume",False); count=cls._pick(row,"trade_count",False)
        if count is not None:
            try: count=int(count)
            except (TypeError,ValueError,OverflowError) as exc: raise ValueError("trade_count must be an integer") from exc
        return Candle(timestamp=ts,**vals,quote_volume=qv,trade_count=count)

    @classmethod
    def normalize(cls,instrument: Instrument,interval: CandleInterval|str,rows: Iterable[Mapping[str,Any]],*,source: str,detect_gaps: bool=True) -> NormalizedCandleSeries:
        if not isinstance(instrument,Instrument): raise TypeError("instrument must be an Instrument")
        try: iv=interval if isinstance(interval,CandleInterval) else CandleInterval(str(interval))
        except ValueError as exc: raise ValueError(f"unsupported candle interval: {interval}") from exc
        candles=tuple(sorted((cls.candle(row) for row in rows),key=lambda c:c.timestamp))
        if not candles: raise ValueError("provider returned no candles")
        stamps=[c.timestamp for c in candles]
        if len(set(stamps)) != len(stamps): raise ValueError("provider returned duplicate candle timestamps")
        gaps=[]; step=_INTERVAL_SECONDS[iv]
        if detect_gaps:
            for prev,cur in zip(stamps,stamps[1:]):
                delta=(cur-prev).total_seconds()
                if delta > step:
                    missing=min(int(delta//step)-1,10000)
                    gaps.extend(prev+timedelta(seconds=step*i) for i in range(1,missing+1))
        return NormalizedCandleSeries(instrument.canonical_id,iv,candles,str(source).strip(),tuple(gaps))
