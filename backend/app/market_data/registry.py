from app.market_data.binance import BinanceSpotAdapter
from app.market_data.provider import MarketDataProvider


def get_market_data_provider() -> MarketDataProvider:
    """Return the configured market-data provider.

    Binance Spot is the initial default. Keeping construction behind this
    function lets the API route and tests swap providers without changing
    domain or HTTP code.
    """
    return BinanceSpotAdapter()
