import asyncio
import json

import pytest

from scripts import binance_collector_preflight as preflight


class FakeSocket:
    def __init__(self, payload):
        self.payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def recv(self):
        return json.dumps(self.payload)


def test_probe_market_accepts_expected_trade_frame(monkeypatch):
    monkeypatch.setattr(
        preflight,
        "connect",
        lambda *args, **kwargs: FakeSocket({"e": "trade", "s": "BTCUSDT", "T": 123}),
    )
    result = asyncio.run(preflight.probe_market("spot", "wss://example.invalid", 1))
    assert result["status"] == "PASS"
    assert result["symbol"] == "BTCUSDT"
    assert result["event_time_ms"] == 123
    assert result["raw_frame_bytes"] > 0


def test_probe_market_rejects_wrong_symbol(monkeypatch):
    monkeypatch.setattr(
        preflight,
        "connect",
        lambda *args, **kwargs: FakeSocket({"e": "trade", "s": "ETHUSDT", "T": 123}),
    )
    with pytest.raises(RuntimeError, match="unexpected symbol"):
        asyncio.run(preflight.probe_market("spot", "wss://example.invalid", 1))


def test_probe_market_rejects_non_trade_payload(monkeypatch):
    monkeypatch.setattr(
        preflight,
        "connect",
        lambda *args, **kwargs: FakeSocket({"e": "depthUpdate", "s": "BTCUSDT"}),
    )
    with pytest.raises(RuntimeError, match="unexpected trade payload"):
        asyncio.run(preflight.probe_market("spot", "wss://example.invalid", 1))
