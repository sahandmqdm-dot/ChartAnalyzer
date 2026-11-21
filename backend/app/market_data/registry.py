from app.market_data.binance import BinanceSpotAdapter
from app.market_data.bybit import BybitSpotAdapter
from app.market_data.provider import MarketDataProvider
from app.settings import settings


def get_market_data_provider() -> MarketDataProvider:
    """Construct the configured public market-data provider."""
    providers: dict[str, type[MarketDataProvider]] = {
        "binance_spot": BinanceSpotAdapter,
        "bybit_spot": BybitSpotAdapter,
    }
    provider_type = providers.get(settings.market_data_provider.strip().lower())
    if provider_type is None:
        raise RuntimeError("Unsupported MARKET_DATA_PROVIDER configuration")
    return provider_type()
