"""ChartAnalyzer read-only integration with iran-crypto-mcp."""
from __future__ import annotations

import os
import threading
import time
from typing import Any, Dict

from iran_crypto_mcp import MCPError, StdioMCPClient


class IranCryptoMarket:
    TOOL_NAMES = {
        "compare": "ic_compare_price",
        "ticker": "ic_get_ticker",
        "orderbook": "ic_get_orderbook",
        "candles": "ic_candles",
        "overview": "ic_market_overview",
    }
    ALIASES = {
        "coin": ("coin", "symbol", "base", "currency"),
        "quote": ("quote", "quote_currency", "market"),
        "exchange": ("exchange", "exchange_id"),
        "levels": ("levels", "depth", "limit"),
        "timeframe": ("timeframe", "interval", "tf"),
        "limit": ("limit", "count", "candles"),
    }

    def __init__(self, config: Dict[str, Any]):
        self.config = config or {}
        self._client = None
        self._lock = threading.RLock()

    def _get_client(self):
        with self._lock:
            if self._client is None:
                cfg = self.config
                env = {}
                proxy = str(cfg.get("proxy") or os.getenv("IRAN_CRYPTO_MCP_PROXY", "")).strip()
                if proxy:
                    env["IRAN_CRYPTO_MCP_PROXY"] = proxy
                self._client = StdioMCPClient(
                    command=str(cfg.get("command") or "uvx"),
                    args=cfg.get("args") or ["iran-crypto-mcp"],
                    timeout=float(cfg.get("timeout_sec", 25)), env=env,
                )
            return self._client

    def status(self):
        if not self.config.get("enabled", False):
            return {"enabled": False, "connected": False, "state": "DISABLED",
                    "message": "Iran Crypto MCP is disabled in config.json."}
        try:
            tools = self._get_client().tools
            wanted = set(self.TOOL_NAMES.values())
            available = sorted(wanted.intersection(tools))
            return {"enabled": True, "connected": True, "state": "CONNECTED",
                    "server": "iran-crypto-mcp", "tools": available,
                    "tool_count": len(tools), "read_only": True,
                    "message": "Connected. Public market data only; no account or trading access."}
        except Exception as exc:
            return {"enabled": True, "connected": False, "state": "UNAVAILABLE",
                    "message": f"{type(exc).__name__}: {exc}"}

    def _call(self, logical_tool: str, values: Dict[str, Any]):
        client = self._get_client()
        name = self.TOOL_NAMES[logical_tool]
        tool = client.tools.get(name)
        if not tool:
            raise MCPError(f"Server does not advertise {name}")
        schema = tool.get("inputSchema") or {}
        props = schema.get("properties") or {}
        required = schema.get("required") or []
        args = {}
        for canonical, value in values.items():
            if value is None:
                continue
            aliases = self.ALIASES.get(canonical, (canonical,))
            key = next((x for x in aliases if x in props), None)
            if key:
                args[key] = value
        missing = [x for x in required if x not in args]
        if missing:
            raise MCPError(f"Cannot map required parameters for {name}: {', '.join(missing)}")
        return client.call_tool(name, args)

    def compare(self, coin="BTC", quote="IRT"):
        return self._call("compare", {"coin": coin, "quote": quote})

    def ticker(self, coin="BTC", exchange="wallex", quote="IRT"):
        return self._call("ticker", {"coin": coin, "exchange": exchange, "quote": quote})

    def orderbook(self, coin="BTC", exchange="wallex", quote="IRT", levels=10):
        return self._call("orderbook", {"coin": coin, "exchange": exchange, "quote": quote, "levels": max(1, min(20, int(levels)))})

    def candles(self, coin="BTC", exchange="wallex", quote="IRT", timeframe="1h", limit=100):
        if timeframe not in ("15m", "1h", "4h", "1d"):
            raise ValueError("iran-crypto-mcp candle timeframe must be 15m, 1h, 4h or 1d")
        return self._call("candles", {"coin": coin, "exchange": exchange, "quote": quote,
                                      "timeframe": timeframe, "limit": max(1, min(100, int(limit)))})

    def close(self):
        with self._lock:
            if self._client:
                self._client.close()
                self._client = None


__all__ = ["IranCryptoMarket"]
