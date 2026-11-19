from datetime import datetime, timezone
import pytest
from multi_asset import AssetClass, Candle, CandleInterval, Instrument, InstrumentStatus, MarketDataNormalizer, MarketRegistry, MarketType

def btc():
    return Instrument("crypto:BTC/USDT","BTC/USDT","BINANCE",AssetClass.CRYPTO_SPOT,MarketType.SPOT,"BTC","USDT",status=InstrumentStatus.ACTIVE)

def aapl():
    return Instrument("us:NASDAQ:AAPL","AAPL","NASDAQ",AssetClass.EQUITY,MarketType.EQUITY_CASH,"AAPL","USD",timezone="America/New_York",status=InstrumentStatus.ACTIVE)

def row(ts,p=100):
    return {"timestamp":ts,"open":p,"high":p+2,"low":p-2,"close":p+1,"volume":10}

def test_instrument_crypto_and_equity():
    assert btc().is_crypto and aapl().is_us_equity
    assert aapl().timezone=="America/New_York"

def test_futures_requires_consistent_contract():
    with pytest.raises(ValueError):
        Instrument("crypto:BTC-PERP","BTCUSDT","BINANCE",AssetClass.CRYPTO_FUTURES,MarketType.LINEAR_FUTURES,"BTC","USDT")
    x=Instrument("crypto:BTC-PERP","BTCUSDT","BINANCE",AssetClass.CRYPTO_FUTURES,MarketType.LINEAR_FUTURES,"BTC","USDT",contract_type="linear")
    assert x.market_type is MarketType.LINEAR_FUTURES

@pytest.mark.parametrize("kw",[{"canonical_id":""},{"symbol":"bad symbol"},{"timezone":"Mars/Olympus"},{"base_currency":"1BTC"},{"contract_multiplier":0}])
def test_invalid_instrument_rejected(kw):
    values=dict(canonical_id="crypto:BTC/USDT",symbol="BTC/USDT",venue="BINANCE",asset_class=AssetClass.CRYPTO_SPOT,market_type=MarketType.SPOT,base_currency="BTC",quote_currency="USDT")
    values.update(kw)
    with pytest.raises(ValueError): Instrument(**values)

def test_registry_idempotent_and_conflict_safe():
    r=MarketRegistry([btc(),aapl()])
    assert len(r)==2 and r.get("crypto:BTC/USDT")==btc()
    assert r.get_by_venue("nasdaq","aapl","equity_cash")==aapl()
    assert len(r.list(venue="NASDAQ"))==1
    conflict=Instrument("crypto:BTC/USDT","BTCUSDT","BINANCE",AssetClass.CRYPTO_SPOT,MarketType.SPOT,"BTC","USDT")
    with pytest.raises(ValueError): r.register(conflict)

def test_candle_utc_and_ohlc_validation():
    c=Candle(datetime.fromisoformat("2026-01-01T03:30:00+03:30"),100,105,99,103,0)
    assert c.timestamp==datetime(2026,1,1,tzinfo=timezone.utc)
    with pytest.raises(ValueError): Candle(datetime.now(timezone.utc),100,90,95,98,1)

def test_normalizer_epoch_seconds_ms_and_sorting():
    s=MarketDataNormalizer.normalize(btc(),"1m",[row(1767225660000),row(1767225600)],source="fixture")
    assert s.candles[0].timestamp<s.candles[1].timestamp and s.interval is CandleInterval.M1

def test_normalizer_rejects_duplicate_and_missing_fields():
    with pytest.raises(ValueError,match="duplicate"):
        MarketDataNormalizer.normalize(btc(),"1m",[row(1767225600),row(1767225600)],source="test")
    with pytest.raises(ValueError,match="missing required"):
        MarketDataNormalizer.normalize(btc(),"1m",[{"timestamp":1767225600}],source="test")

def test_normalizer_reports_gaps_without_filling():
    s=MarketDataNormalizer.normalize(btc(),"1m",[row(1767225600),row(1767225780)],source="test")
    assert len(s.candles)==2 and len(s.gaps)==2

def test_naive_timestamp_rejected():
    with pytest.raises(ValueError,match="timezone-aware"): Candle(datetime(2026,1,1),100,101,99,100,1)

def test_instrument_normalizes_enum_strings_and_rejects_unknown_values():
    item=Instrument("us:NASDAQ:MSFT","msft","nasdaq","equity","equity_cash","msft","usd",
                    timezone="America/New_York",status="active")
    assert item.asset_class is AssetClass.EQUITY
    assert item.market_type is MarketType.EQUITY_CASH
    assert item.status is InstrumentStatus.ACTIVE
    with pytest.raises(ValueError):
        Instrument("bad","MSFT","NASDAQ","unknown","equity_cash","MSFT","USD")

def test_fractional_trade_count_is_rejected():
    bad = row(1767225600)
    bad["trade_count"] = 1.5
    with pytest.raises(ValueError, match="trade_count"):
        MarketDataNormalizer.candle(bad)
