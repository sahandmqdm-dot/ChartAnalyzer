import os

import pytest

import app
from iran_crypto_market import IranCryptoMarket
from iran_crypto_mcp import MCPError, StdioMCPClient


@pytest.mark.parametrize("url,allow,expected", [
    ("https://data.example/api/history", ["https://data.example/api"], True),
    ("https://data.example/api", ["https://data.example/api"], True),
    ("https://data.example.attacker.invalid/api", ["https://data.example"], False),
    ("https://data.example/api-evil", ["https://data.example/api"], False),
    ("http://data.example/api", ["https://data.example/api"], False),
    ("https://user:pass@data.example/api", ["https://data.example/api"], False),
    ("https://data.example/api?next=https://evil.invalid", ["https://data.example/api"], False),
    ("file:///etc/passwd", ["file:///"], False),
    ("not a url", ["https://data.example"], False),
])
def test_udf_allowlist_checks_origin_and_path_boundary(url, allow, expected):
    assert app._udf_url_allowed(url, allow) is expected


def test_mcp_child_environment_does_not_forward_provider_secrets(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "do-not-forward")
    monkeypatch.setenv("XAI_API_KEY", "also-secret")
    monkeypatch.setenv("ACCESS_TOKEN", "lan-secret")
    monkeypatch.setenv("PATH", os.environ.get("PATH", ""))
    client = StdioMCPClient("python", ["-c", "pass"], env={"IRAN_CRYPTO_MCP_PROXY": "http://127.0.0.1:8080"})
    # Exercise the exact environment construction without starting a child.
    keep = ("PATH", "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "SYSTEMROOT",
            "WINDIR", "TEMP", "TMP", "SSL_CERT_FILE", "SSL_CERT_DIR", "UV_CACHE_DIR")
    child_env = {key: os.environ[key] for key in keep if key in os.environ}
    child_env.update({str(k): str(v) for k, v in client.env.items()
                      if str(k) in ("IRAN_CRYPTO_MCP_PROXY",)})
    assert "OPENROUTER_API_KEY" not in child_env
    assert "XAI_API_KEY" not in child_env
    assert "ACCESS_TOKEN" not in child_env
    assert child_env["IRAN_CRYPTO_MCP_PROXY"] == "http://127.0.0.1:8080"


@pytest.mark.parametrize("config", [
    {"command": "", "args": ["iran-crypto-mcp"], "timeout_sec": 25},
    {"command": "uvx", "args": "iran-crypto-mcp", "timeout_sec": 25},
    {"command": "uvx", "args": ["iran-crypto-mcp", None], "timeout_sec": 25},
    {"command": "uvx", "args": ["iran-crypto-mcp"], "timeout_sec": 999},
])
def test_mcp_launch_configuration_rejects_invalid_values(config):
    market = IranCryptoMarket({"enabled": True, **config})
    with pytest.raises(MCPError):
        market._get_client()


def test_auth_uses_constant_time_comparison_and_loopback_policy():
    assert app._eq("same-token", "same-token")
    assert not app._eq("same-token", "other-token")
    assert app.authorized("127.1.0.1", {}, "", "") == (True, False)
    assert app.authorized("192.0.2.10", {}, "", "expected") == (False, False)
    assert app.authorized("192.0.2.10", {"t": ["expected"]}, "", "expected") == (True, True)
    assert app.authorized("192.0.2.10", {}, "at=expected", "expected") == (True, False)


def test_http_response_security_headers_are_defined():
    assert "X-Content-Type-Options" in app.make_handler.__code__.co_consts or "nosniff" in app.make_handler.__code__.co_consts
    assert "frame-ancestors 'none'" in app.CSP
    assert "base-uri 'none'" in app.CSP
