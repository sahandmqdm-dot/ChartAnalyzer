import pytest

from app.market_data.binance import BinanceSpotAdapter
from app.market_data.bybit import BybitSpotAdapter
from app.market_data.provider import MarketDataProvider
from app.market_data.registry import get_market_data_provider
from app.settings import settings


def test_binance_adapter_matches_provider_contract():
    provider: MarketDataProvider = BinanceSpotAdapter()
    assert provider.name == "binance_spot"
    assert callable(provider.fetch_ohlcv)


def test_registry_returns_configured_binance_provider(monkeypatch):
    monkeypatch.setattr(settings, "market_data_provider", "binance_spot")
    provider = get_market_data_provider()
    assert isinstance(provider, BinanceSpotAdapter)
    assert provider.name == "binance_spot"


def test_registry_returns_configured_bybit_provider(monkeypatch):
    monkeypatch.setattr(settings, "market_data_provider", "bybit_spot")
    provider = get_market_data_provider()
    assert isinstance(provider, BybitSpotAdapter)
    assert provider.name == "bybit_spot"


def test_registry_rejects_unknown_provider(monkeypatch):
    monkeypatch.setattr(settings, "market_data_provider", "unknown")
    with pytest.raises(RuntimeError, match="Unsupported"):
        get_market_data_provider()
