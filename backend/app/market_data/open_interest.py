from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict

from app.market_data.symbol import Symbol


class OpenInterestSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider: str = "binance_usdm_futures"
    symbol: str
    state: Literal["connecting", "connected", "reconnecting", "stopped"]
    open_interest: Decimal | None = None
    observed_at: datetime | None = None
    last_error: str | None = None
    persistence_state: str = "disabled"
    persistence_error: str | None = None


def normalize_open_interest(payload: dict, expected_symbol: str) -> dict | None:
    try:
        symbol = str(Symbol(str(payload["symbol"])))
        value = Decimal(str(payload["openInterest"]))
        timestamp = datetime.fromtimestamp(int(payload["time"]) / 1000, tz=UTC)
        if symbol != expected_symbol or not value.is_finite() or value <= 0:
            return None
        return {
            "symbol": symbol,
            "open_interest": value,
            "observed_at": timestamp,
        }
    except (KeyError, ValueError, TypeError, InvalidOperation, ArithmeticError, OverflowError):
        return None


class BinanceOpenInterestCollector:
    """Poll the public USD-M current open-interest endpoint without trading credentials."""

    endpoint = "https://fapi.binance.com/fapi/v1/openInterest"

    def __init__(self, symbol: str, *, interval_seconds: float = 60.0):
        if interval_seconds < 10:
            raise ValueError("open-interest polling interval must be at least 10 seconds")
        self.symbol = str(Symbol(symbol))
        self.interval_seconds = interval_seconds
        self.state: Literal["connecting", "connected", "reconnecting", "stopped"] = "stopped"
        self.open_interest: Decimal | None = None
        self.observed_at: datetime | None = None
        self.last_error: str | None = None
        self.persistence_state = "disabled"
        self.persistence_error: str | None = None
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self.state = "connecting"
            self._task = asyncio.create_task(
                self._run(), name=f"futures-open-interest-{self.symbol}"
            )

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self.state = "stopped"

    def snapshot(self) -> dict:
        return OpenInterestSnapshot(
            symbol=self.symbol,
            state=self.state,
            open_interest=self.open_interest,
            observed_at=self.observed_at,
            last_error=self.last_error,
            persistence_state=self.persistence_state,
            persistence_error=self.persistence_error,
        ).model_dump(mode="json")

    async def _poll_once(self, client: httpx.AsyncClient) -> None:
        response = await client.get(self.endpoint, params={"symbol": self.symbol})
        response.raise_for_status()
        normalized = normalize_open_interest(response.json(), self.symbol)
        if normalized is None:
            raise ValueError("Binance returned an invalid open-interest payload")
        self.open_interest = normalized["open_interest"]
        self.observed_at = normalized["observed_at"]
        from app.market_data.open_interest_repository import save_open_interest

        try:
            await save_open_interest(
                "binance_usdm_futures",
                self.symbol,
                self.observed_at,
                self.open_interest,
            )
            from app.database import database_enabled

            self.persistence_state = "available" if database_enabled() else "disabled"
            self.persistence_error = None
        except Exception as exc:  # noqa: BLE001 - persistence must not stop polling
            self.persistence_state = "unavailable"
            self.persistence_error = type(exc).__name__

    async def _run(self) -> None:
        delay = self.interval_seconds
        timeout = httpx.Timeout(8.0, connect=5.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            while True:
                try:
                    await self._poll_once(client)
                    self.state = "connected"
                    self.last_error = None
                    delay = self.interval_seconds
                    await asyncio.sleep(self.interval_seconds)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - retry transient public API errors
                    self.state = "reconnecting"
                    self.last_error = type(exc).__name__
                    await asyncio.sleep(delay)
                    delay = min(max(delay * 2, self.interval_seconds), 300.0)


_collectors: dict[str, BinanceOpenInterestCollector] = {}


def get_open_interest_collector(symbol: str) -> BinanceOpenInterestCollector:
    normalized = str(Symbol(symbol))
    collector = _collectors.get(normalized)
    if collector is None:
        collector = BinanceOpenInterestCollector(normalized)
        _collectors[normalized] = collector
    return collector


async def stop_all_open_interest_collectors() -> None:
    collectors = list(_collectors.values())
    await asyncio.gather(*(collector.stop() for collector in collectors))
    _collectors.clear()
