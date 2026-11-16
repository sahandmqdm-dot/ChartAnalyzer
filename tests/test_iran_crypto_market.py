import json
import os
import sys
import tempfile
import textwrap

from iran_crypto_market import IranCryptoMarket
from iran_crypto_mcp import StdioMCPClient


class FakeClient:
    def __init__(self):
        self.tools = {
            "ic_compare_price": {"inputSchema": {"properties": {"coin": {}, "quote": {}}, "required": ["coin", "quote"]}},
            "ic_get_orderbook": {"inputSchema": {"properties": {"coin": {}, "exchange": {}, "quote": {}, "levels": {}}, "required": ["coin", "exchange", "quote", "levels"]}},
            "ic_candles": {"inputSchema": {"properties": {"coin": {}, "exchange": {}, "quote": {}, "timeframe": {}, "limit": {}}, "required": ["coin", "exchange", "quote", "timeframe", "limit"]}},
            "ic_get_ticker": {"inputSchema": {"properties": {"coin": {}, "exchange": {}, "quote": {}}, "required": ["coin", "exchange", "quote"]}},
        }
        self.calls = []

    def call_tool(self, name, args):
        self.calls.append((name, args))
        return {"ok": True}

    def close(self):
        pass


def test_market_maps_arguments_and_clamps_limits():
    market = IranCryptoMarket({"enabled": True})
    fake = FakeClient()
    market._client = fake
    assert market.compare("BTC", "IRT")["ok"]
    assert market.orderbook("ETH", "wallex", "USDT", 99)["ok"]
    assert market.candles("BTC", "ramzinex", "IRT", "1h", 999)["ok"]
    assert fake.calls[0] == ("ic_compare_price", {"coin": "BTC", "quote": "IRT"})
    assert fake.calls[1][1]["levels"] == 20
    assert fake.calls[2][1]["limit"] == 100


def test_candles_reject_unsupported_timeframe():
    market = IranCryptoMarket({"enabled": True})
    market._client = FakeClient()
    try:
        market.candles(timeframe="5m")
    except ValueError as exc:
        assert "15m, 1h, 4h or 1d" in str(exc)
    else:
        raise AssertionError("unsupported timeframe should fail")


def test_disabled_status_does_not_spawn_process():
    market = IranCryptoMarket({"enabled": False})
    status = market.status()
    assert status["state"] == "DISABLED"
    assert market._client is None


def test_stdio_client_initialize_and_tool_call_roundtrip():
    server = textwrap.dedent(r'''
        import json, sys
        for line in sys.stdin:
            msg=json.loads(line); method=msg.get("method"); rid=msg.get("id")
            if method == "initialize":
                out={"jsonrpc":"2.0","id":rid,"result":{"protocolVersion":"2024-11-05","capabilities":{"tools":{}},"serverInfo":{"name":"fake","version":"1"}}}
            elif method == "tools/list":
                out={"jsonrpc":"2.0","id":rid,"result":{"tools":[{"name":"ic_compare_price","inputSchema":{"type":"object","properties":{"coin":{"type":"string"},"quote":{"type":"string"}},"required":["coin","quote"]}}]}}
            elif method == "tools/call":
                out={"jsonrpc":"2.0","id":rid,"result":{"content":[{"type":"text","text":"{\"price\":123}"}]}}
            else:
                continue
            print(json.dumps(out), flush=True)
    ''')
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "fake_mcp.py")
        with open(path, "w", encoding="utf-8") as f:
            f.write(server)
        client = StdioMCPClient(sys.executable, [path], timeout=5)
        try:
            result = client.call_tool("ic_compare_price", {"coin": "BTC", "quote": "IRT"})
            assert result == {"price": 123}
        finally:
            client.close()


def test_app_exposes_mcp_routes_and_keeps_feature_disabled_by_default(monkeypatch):
    import app as chart_app

    expected = {
        ("GET", "/api/iran-crypto/status"),
        ("GET", "/api/iran-crypto/compare"),
        ("GET", "/api/iran-crypto/orderbook"),
        ("GET", "/api/iran-crypto/candles"),
    }
    assert expected.issubset(set(chart_app.app.routes))
    monkeypatch.setattr(chart_app, "conf", lambda: {"iran_crypto_mcp": {"enabled": False}})
    assert chart_app.api_iran_crypto_status()["state"] == "DISABLED"
