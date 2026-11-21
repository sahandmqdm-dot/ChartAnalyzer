from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field

from app.market_data.symbol import Symbol


class FuturesMarketDataError(RuntimeError):
    """Raised when a derivatives market-data provider fails or returns invalid data."""


class FuturesOverview(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider: str
    market_type: str
    symbol: str
    mark_price: Decimal = Field(gt=0)
    index_price: Decimal = Field(gt=0)
    funding_rate: Decimal
    open_interest: Decimal = Field(ge=0)
    open_interest_value: Decimal = Field(ge=0)
    next_funding_time: datetime | None = None
    observed_at: datetime

    @classmethod
    def normalize_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("datetime must be timezone-aware")
        return value.astimezone(UTC)


class FuturesMarketDataProvider(Protocol):
    name: str

    async def fetch_overview(self, symbol: str) -> FuturesOverview:
        """Fetch a normalized linear perpetual-futures market snapshot."""


class _HttpFuturesAdapter:
    name = "futures"
    timeout = 10.0

    def __init__(self, client: httpx.AsyncClient | None = None):
        self._client = client

    async def _get_json(self, url: str, params: dict[str, str]) -> dict | list:
        client = self._client
        own_client = client is None
        if own_client:
            client = httpx.AsyncClient(timeout=self.timeout)
        try:
            response = await client.get(url, params=params)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise FuturesMarketDataError("Futures market-data request failed") from exc
        finally:
            if own_client and client is not None:
                await client.aclose()
        if not isinstance(payload, (dict, list)):
            raise FuturesMarketDataError("Futures provider returned an unexpected payload")
        return payload


class BinanceUsdmFuturesAdapter(_HttpFuturesAdapter):
    """Public Binance USDⓈ-M linear perpetual market-data adapter."""

    name = "binance_usdm_futures"

    async def fetch_overview(self, symbol: str) -> FuturesOverview:
        normalized = str(Symbol(symbol))
        premium = await self._get_json(
            "https://fapi.binance.com/fapi/v1/premiumIndex",
            {"symbol": normalized},
        )
        interest = await self._get_json(
            "https://fapi.binance.com/fapi/v1/openInterest",
            {"symbol": normalized},
        )
        if not isinstance(premium, dict) or not isinstance(interest, dict):
            raise FuturesMarketDataError("Binance returned an unexpected futures payload")
        try:
            mark = Decimal(str(premium["markPrice"]))
            index = Decimal(str(premium["indexPrice"]))
            open_interest = Decimal(str(interest["openInterest"]))
            next_time = datetime.fromtimestamp(
                int(premium["nextFundingTime"]) / 1000, tz=UTC
            )
            return FuturesOverview(
                provider=self.name,
                market_type="linear_perpetual",
                symbol=normalized,
                mark_price=mark,
                index_price=index,
                funding_rate=Decimal(str(premium["lastFundingRate"])),
                open_interest=open_interest,
                open_interest_value=open_interest * mark,
                next_funding_time=next_time,
                observed_at=datetime.now(UTC),
            )
        except (KeyError, ValueError, TypeError, ArithmeticError) as exc:
            raise FuturesMarketDataError("Binance returned invalid futures market data") from exc


class BybitLinearFuturesAdapter(_HttpFuturesAdapter):
    """Public Bybit V5 linear perpetual market-data adapter."""

    name = "bybit_linear_futures"

    async def fetch_overview(self, symbol: str) -> FuturesOverview:
        normalized = str(Symbol(symbol))
        payload = await self._get_json(
            "https://api.bybit.com/v5/market/tickers",
            {"category": "linear", "symbol": normalized},
        )
        if not isinstance(payload, dict) or payload.get("retCode") != 0:
            raise FuturesMarketDataError("Bybit returned an unsuccessful futures response")
        result = payload.get("result")
        rows = result.get("list") if isinstance(result, dict) else None
        if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
            raise FuturesMarketDataError("Bybit returned an unexpected futures ticker payload")
        ticker = rows[0]
        try:
            next_time_raw = ticker.get("nextFundingTime")
            next_time = (
                datetime.fromtimestamp(int(next_time_raw) / 1000, tz=UTC)
                if next_time_raw
                else None
            )
            return FuturesOverview(
                provider=self.name,
                market_type="linear_perpetual",
                symbol=normalized,
                mark_price=Decimal(str(ticker["markPrice"])),
                index_price=Decimal(str(ticker["indexPrice"])),
                funding_rate=Decimal(str(ticker["fundingRate"])),
                open_interest=Decimal(str(ticker["openInterest"])),
                open_interest_value=Decimal(str(ticker.get("openInterestValue", "0"))),
                next_funding_time=next_time,
                observed_at=datetime.now(UTC),
            )
        except (KeyError, ValueError, TypeError, ArithmeticError) as exc:
            raise FuturesMarketDataError("Bybit returned invalid futures market data") from exc
