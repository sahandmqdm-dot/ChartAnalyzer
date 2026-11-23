from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict


class CoinalyzeError(RuntimeError):
    """Raised when Coinalyze is unavailable or returns invalid market data."""


class CoinalyzeSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    open_interest: Decimal | None = None
    open_interest_updated_at: datetime | None = None
    funding_rate: Decimal | None = None
    funding_rate_updated_at: datetime | None = None
    observed_at: datetime


_SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9_.-]{1,63}$")
_BASE_URL = "https://api.coinalyze.net/v1"


def normalize_symbols(symbols: list[str]) -> list[str]:
    if not symbols:
        raise ValueError("at least one Coinalyze symbol is required")
    if len(symbols) > 20:
        raise ValueError("Coinalyze accepts at most 20 symbols per request")

    normalized: list[str] = []
    seen: set[str] = set()
    for raw in symbols:
        symbol = raw.strip().upper()
        if not _SYMBOL_RE.fullmatch(symbol):
            raise ValueError("invalid Coinalyze market symbol")
        if symbol in seen:
            raise ValueError("duplicate Coinalyze market symbol")
        seen.add(symbol)
        normalized.append(symbol)
    return normalized


def _decimal(value: Any) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise CoinalyzeError("Coinalyze returned a non-numeric value") from exc
    if not parsed.is_finite():
        raise CoinalyzeError("Coinalyze returned a non-finite value")
    return parsed


def _updated_at(value: Any) -> datetime:
    try:
        milliseconds = int(value)
        if milliseconds <= 0:
            raise ValueError
        return datetime.fromtimestamp(milliseconds / 1000, tz=UTC)
    except (ValueError, TypeError, OverflowError, OSError) as exc:
        raise CoinalyzeError("Coinalyze returned an invalid millisecond timestamp") from exc


class CoinalyzeClient:
    """Read-only Coinalyze client for current futures OI and funding snapshots."""

    timeout = 12.0

    def __init__(
        self,
        api_key: str,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key or not api_key.strip():
            raise ValueError("Coinalyze API key is required")
        self._api_key = api_key.strip()
        self._client = client

    async def _get_rows(
        self,
        client: httpx.AsyncClient,
        endpoint: str,
        symbols: list[str],
    ) -> dict[str, tuple[Decimal, datetime]]:
        try:
            response = await client.get(
                f"{_BASE_URL}/{endpoint}",
                headers={"api_key": self._api_key, "Accept": "application/json"},
                params={"symbols": ",".join(symbols)},
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status == 429:
                raise CoinalyzeError("Coinalyze rate limit exceeded") from exc
            if status in (401, 403):
                raise CoinalyzeError("Coinalyze authentication was rejected") from exc
            raise CoinalyzeError(f"Coinalyze request failed with HTTP {status}") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise CoinalyzeError("Coinalyze request failed or returned invalid JSON") from exc

        if not isinstance(payload, list):
            raise CoinalyzeError("Coinalyze returned an unexpected response shape")

        result: dict[str, tuple[Decimal, datetime]] = {}
        requested = set(symbols)
        for row in payload:
            if not isinstance(row, dict):
                raise CoinalyzeError("Coinalyze returned an invalid data record")
            symbol = row.get("symbol")
            if not isinstance(symbol, str) or symbol not in requested or symbol in result:
                raise CoinalyzeError("Coinalyze returned an unexpected or duplicate symbol")
            value = _decimal(row.get("value"))
            if endpoint == "open-interest" and value < 0:
                raise CoinalyzeError("Coinalyze returned negative open interest")
            result[symbol] = (value, _updated_at(row.get("update")))
        return result

    async def fetch_snapshot(self, symbols: list[str]) -> list[CoinalyzeSnapshot]:
        normalized = normalize_symbols(symbols)
        own_client = self._client is None
        client = self._client or httpx.AsyncClient(
            timeout=self.timeout,
            follow_redirects=False,
        )
        try:
            open_interest, funding_rate = await asyncio.gather(
                self._get_rows(client, "open-interest", normalized),
                self._get_rows(client, "funding-rate", normalized),
            )
        finally:
            if own_client:
                await client.aclose()

        observed_at = datetime.now(UTC)
        snapshots = []
        for symbol in normalized:
            oi = open_interest.get(symbol)
            funding = funding_rate.get(symbol)
            snapshots.append(
                CoinalyzeSnapshot(
                    symbol=symbol,
                    open_interest=oi[0] if oi else None,
                    open_interest_updated_at=oi[1] if oi else None,
                    funding_rate=funding[0] if funding else None,
                    funding_rate_updated_at=funding[1] if funding else None,
                    observed_at=observed_at,
                )
            )
        return snapshots
