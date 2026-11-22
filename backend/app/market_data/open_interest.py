from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict

from app.auth import current_user
from app.market_data.symbol import Symbol
from app.settings import settings


class OpenInterestSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider: str
    symbol: str
    state: Literal["connecting", "connected", "reconnecting", "stopped"]
    open_interest: Decimal | None = None
    observed_at: datetime | None = None
    last_error: str | None = None
    persistence_state: str = "disabled"
    persistence_error: str | None = None


def _normalize_open_interest_value(
    symbol_value: object,
    interest_value: object,
    timestamp_value: object,
    expected_symbol: str,
) -> dict | None:
    try:
        symbol = str(Symbol(str(symbol_value)))
        value = Decimal(str(interest_value))
        timestamp = datetime.fromtimestamp(int(timestamp_value) / 1000, tz=UTC)
        if symbol != expected_symbol or not value.is_finite() or value <= 0:
            return None
        return {
            "symbol": symbol,
            "open_interest": value,
            "observed_at": timestamp,
        }
    except (ValueError, TypeError, InvalidOperation, ArithmeticError, OverflowError):
        return None


def normalize_open_interest(payload: dict, expected_symbol: str) -> dict | None:
    """Normalize Binance USD-M current open-interest REST response."""
    return _normalize_open_interest_value(
        payload.get("symbol"),
        payload.get("openInterest"),
        payload.get("time"),
        expected_symbol,
    )


def normalize_bybit_open_interest(payload: dict, expected_symbol: str) -> dict | None:
    """Normalize Bybit V5 linear ticker response without inventing history."""
    if not isinstance(payload, dict) or payload.get("retCode") != 0:
        return None
    result = payload.get("result")
    rows = result.get("list") if isinstance(result, dict) else None
    if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
        return None
    row = rows[0]
    return _normalize_open_interest_value(
        row.get("symbol"),
        row.get("openInterest"),
        payload.get("time"),
        expected_symbol,
    )


class _OpenInterestCollector:
    """Poll one public linear-perpetual endpoint and persist local observations."""

    provider = "futures"
    endpoint = ""
    error_label = "Futures provider"

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

    def request_params(self) -> dict[str, str]:
        return {"symbol": self.symbol}

    def normalize(self, payload: dict) -> dict | None:
        raise NotImplementedError

    def start(self) -> None:
        if self._task is None or self._task.done():
            self.state = "connecting"
            self._task = asyncio.create_task(
                self._run(), name=f"futures-open-interest-{self.provider}-{self.symbol}"
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
            provider=self.provider,
            symbol=self.symbol,
            state=self.state,
            open_interest=self.open_interest,
            observed_at=self.observed_at,
            last_error=self.last_error,
            persistence_state=self.persistence_state,
            persistence_error=self.persistence_error,
        ).model_dump(mode="json")

    async def _poll_once(self, client: httpx.AsyncClient) -> None:
        response = await client.get(self.endpoint, params=self.request_params())
        response.raise_for_status()
        normalized = self.normalize(response.json())
        if normalized is None:
            raise ValueError(f"{self.error_label} returned invalid open-interest data")
        self.open_interest = normalized["open_interest"]
        self.observed_at = normalized["observed_at"]
        from app.market_data.open_interest_repository import save_open_interest

        try:
            await save_open_interest(
                self.provider,
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


class BinanceOpenInterestCollector(_OpenInterestCollector):
    """Poll Binance USD-M current open interest without trading credentials."""

    provider = "binance_usdm_futures"
    endpoint = "https://fapi.binance.com/fapi/v1/openInterest"
    error_label = "Binance"

    def normalize(self, payload: dict) -> dict | None:
        return normalize_open_interest(payload, self.symbol)


class BybitOpenInterestCollector(_OpenInterestCollector):
    """Poll Bybit V5 linear ticker open interest without trading credentials."""

    provider = "bybit_linear_futures"
    endpoint = "https://api.bybit.com/v5/market/tickers"
    error_label = "Bybit"

    def request_params(self) -> dict[str, str]:
        return {"category": "linear", "symbol": self.symbol}

    def normalize(self, payload: dict) -> dict | None:
        return normalize_bybit_open_interest(payload, self.symbol)


_collectors: dict[tuple[str, str], _OpenInterestCollector] = {}


def get_open_interest_collector(symbol: str) -> _OpenInterestCollector:
    normalized = str(Symbol(symbol))
    provider = settings.futures_market_data_provider
    collector_types = {
        "binance_usdm_futures": BinanceOpenInterestCollector,
        "bybit_linear_futures": BybitOpenInterestCollector,
    }
    collector_type = collector_types.get(provider)
    if collector_type is None:
        raise RuntimeError("No supported Futures Open Interest collector is configured")
    key = (provider, normalized)
    collector = _collectors.get(key)
    if collector is None:
        collector = collector_type(normalized)
        _collectors[key] = collector
    return collector


async def stop_all_open_interest_collectors() -> None:
    collectors = list(_collectors.values())
    await asyncio.gather(*(collector.stop() for collector in collectors))
    _collectors.clear()


router = APIRouter(prefix="/api/v1/derivatives", tags=["derivatives"])


@router.get("/open-interest-live")
async def live_open_interest(
    request: Request,
    symbol: str = Query(min_length=2, max_length=30),
):
    current_user(request)
    try:
        collector = get_open_interest_collector(symbol)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    collector.start()
    return collector.snapshot()


@router.delete("/open-interest-live")
async def stop_live_open_interest(
    request: Request,
    symbol: str = Query(min_length=2, max_length=30),
):
    current_user(request)
    try:
        collector = get_open_interest_collector(symbol)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await collector.stop()
    return collector.snapshot()


@router.get("/open-interest-history")
async def open_interest_history(
    request: Request,
    symbol: str = Query(min_length=2, max_length=30),
    hours: int = Query(default=24, ge=1, le=720),
    limit: int = Query(default=1000, ge=1, le=5000),
):
    current_user(request)
    try:
        normalized = str(Symbol(symbol))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    from app.market_data.open_interest_repository import list_open_interest

    provider = settings.futures_market_data_provider
    try:
        rows = await list_open_interest(
            normalized,
            datetime.now(UTC) - timedelta(hours=hours),
            limit=limit,
            provider=provider,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if rows is None:
        raise HTTPException(status_code=503, detail="Open-interest persistence is not configured")
    return {
        "provider": provider,
        "market_type": "linear_perpetual",
        "symbol": normalized,
        "hours": hours,
        "count": len(rows),
        "history_source": "locally_observed_polling",
        "observations": rows,
    }
