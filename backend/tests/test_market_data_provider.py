from app.market_data.binance import BinanceSpotAdapter
from app.market_data.bybit import BybitSpotAdapter
from app.market_data.provider import MarketDataProvider
from app.market_data.registry import get_market_data_provider
from app.settings import settings


def test_binance_adapter_matches_provider_contract():
    provider: MarketDataProvider = BinanceSpotAdapter()
    assert provider.name == "binance_spot"
    assert callable(provider.fetch_ohlcv)


def test_registry_returns_default_provider():
    provider = get_market_data_provider()
    assert isinstance(provider, BinanceSpotAdapter)
    assert provider.name == "binance_spot"
