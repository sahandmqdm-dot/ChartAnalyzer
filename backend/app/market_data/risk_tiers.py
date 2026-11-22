from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, ConfigDict, Field

from app.market_data.futures import FuturesMarketDataError, _HttpFuturesAdapter
from app.market_data.futures_risk import RiskTier
from app.market_data.symbol import Symbol


class RiskTierSet(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider: str
    category: str
    symbol: str
    tiers: list[RiskTier]
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class BybitLinearRiskTierAdapter(_HttpFuturesAdapter):
    """Public Bybit V5 risk-limit tiers for one linear contract."""

    name = "bybit_linear_futures"

    async def fetch_risk_tiers(self, symbol: str) -> RiskTierSet:
        normalized = str(Symbol(symbol))
        payload = await self._get_json(
            "https://api.bybit.com/v5/market/risk-limit",
            {"category": "linear", "symbol": normalized, "limit": "200"},
        )
        if not isinstance(payload, dict) or payload.get("retCode") != 0:
            raise FuturesMarketDataError("Bybit returned an unsuccessful risk-tier response")
        result = payload.get("result")
        rows = result.get("list") if isinstance(result, dict) else None
        if not isinstance(rows, list) or not rows:
            raise FuturesMarketDataError("Bybit returned no risk tiers for this symbol")
        try:
            selected = [
                row for row in rows
                if isinstance(row, dict) and row.get("symbol") == normalized
            ]
            if not selected:
                raise ValueError("symbol risk tiers are missing")
            selected.sort(key=lambda row: int(row["id"]))
            tiers: list[RiskTier] = []
            floor = Decimal(0)
            for index, row in enumerate(selected):
                cap = Decimal(str(row["riskLimitValue"]))
                tiers.append(
                    RiskTier(
                        tier=int(row["id"]),
                        notional_floor=floor,
                        notional_cap=cap,
                        maintenance_margin_rate=Decimal(str(row["maintenanceMargin"])),
                        maintenance_margin_deduction=Decimal(str(row["mmDeduction"])),
                        max_leverage=Decimal(str(row["maxLeverage"])),
                    )
                )
                floor = cap
            return RiskTierSet(
                provider=self.name,
                category="linear",
                symbol=normalized,
                tiers=tiers,
                observed_at=datetime.now(UTC),
            )
        except (KeyError, ValueError, TypeError, InvalidOperation, ArithmeticError) as exc:
            raise FuturesMarketDataError("Bybit returned invalid risk-tier data") from exc
