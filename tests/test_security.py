import inspect
import io
import os
import queue

import pandas as pd
import pytest

import app
from holdout_vault import HoldoutVault
from iran_crypto_market import IranCryptoMarket
from iran_crypto_mcp import MCPError, StdioMCPClient, _child_environment
from research_registry import ResearchRegistry
from research_rule_engine import compile_long_flat_rule


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
    child_env = _child_environment({"IRAN_CRYPTO_MCP_PROXY": "http://127.0.0.1:8080"})
    assert "OPENROUTER_API_KEY" not in child_env
    assert "XAI_API_KEY" not in child_env
    assert "ACCESS_TOKEN" not in child_env
    assert child_env["IRAN_CRYPTO_MCP_PROXY"] == "http://127.0.0.1:8080"


@pytest.mark.parametrize("config", [
    {"command": 123, "args": ["iran-crypto-mcp"], "timeout_sec": 25},
    {"command": "uvx" + chr(0) + "bad", "args": ["iran-crypto-mcp"], "timeout_sec": 25},
    {"command": "uvx", "args": "iran-crypto-mcp", "timeout_sec": 25},
    {"command": "uvx", "args": ["iran-crypto-mcp", None], "timeout_sec": 25},
    {"command": "uvx", "args": ["iran-crypto-mcp"], "timeout_sec": 999},
])
def test_mcp_launch_configuration_rejects_invalid_values(config):
    market = IranCryptoMarket({"enabled": True, **config})
    with pytest.raises(MCPError):
        market._get_client()


@pytest.mark.parametrize("token,expected", [
    ("A" * 16, True),
    ("A" * 512, True),
    ("short", False),
    ("contains space token", False),
    ("token;injection" + "A" * 16, False),
    ("token" + chr(13) + chr(10) + "X-Evil: yes" + "A" * 16, False),
    (None, False),
])
def test_access_token_is_safe_for_query_and_cookie_transport(token, expected):
    assert app._valid_access_token(token) is expected


def test_default_server_host_matches_documented_loopback_address():
    assert app.DEFAULT_HOST == "127.0.0.1"


def test_auth_uses_constant_time_comparison_and_loopback_policy():
    assert app._eq("same-token", "same-token")
    assert not app._eq("same-token", "other-token")
    assert app.authorized("127.1.0.1", {}, "", "") == (True, False)
    assert app.authorized("192.0.2.10", {}, "", "expected") == (False, False)
    assert app.authorized("192.0.2.10", {"t": ["expected"]}, "", "expected") == (True, True)
    assert app.authorized("192.0.2.10", {}, "at=expected", "expected") == (True, False)


def test_http_response_security_headers_are_defined():
    assert "nosniff" in inspect.getsource(app.make_handler)
    assert "frame-ancestors 'none'" in app.CSP
    assert "base-uri 'none'" in app.CSP


@pytest.mark.parametrize("headers,host,expected", [
    ({}, "127.1.0.1:8765", True),
    ({"Sec-Fetch-Site": "same-origin"}, "127.1.0.1:8765", True),
    ({"Sec-Fetch-Site": "none"}, "127.1.0.1:8765", True),
    ({"Sec-Fetch-Site": "cross-site"}, "127.1.0.1:8765", False),
    ({"Sec-Fetch-Site": "same-site"}, "127.1.0.1:8765", False),
    ({"Origin": "http://127.1.0.1:8765"}, "127.1.0.1:8765", True),
    ({"Origin": "https://attacker.example"}, "127.1.0.1:8765", False),
    ({"Origin": "null"}, "127.1.0.1:8765", False),
    ({"Origin": "http://127.1.0.1:8765/path"}, "127.1.0.1:8765", False),
    ({"Referer": "http://127.1.0.1:8765/dashboard"}, "127.1.0.1:8765", True),
    ({"Referer": "http://attacker.example/"}, "127.1.0.1:8765", False),
])
def test_browser_request_origin_policy(headers, host, expected):
    assert app._same_origin_request(headers, host) is expected


def test_csv_ingestion_rejects_more_than_row_limit(tmp_path, monkeypatch):
    path = tmp_path / "large.csv"
    path.write_text("header\\n", encoding="utf-8")
    monkeypatch.setattr(app.pd, "read_csv",
                        lambda *_args, **_kwargs: pd.DataFrame({"x": range(app.CSV_MAX_ROWS + 1)}))
    with pytest.raises(ValueError, match="row safety limit"):
        app._read_csv_bounded(path)


def test_csv_ingestion_rejects_oversized_file_before_parsing(tmp_path, monkeypatch):
    path = tmp_path / "huge.csv"
    path.write_text("header\\n", encoding="utf-8")
    monkeypatch.setattr(app.os.path, "getsize", lambda _path: app.CSV_MAX_BYTES + 1)
    monkeypatch.setattr(app.pd, "read_csv",
                        lambda *_args, **_kwargs: pytest.fail("oversized CSV must not be parsed"))
    with pytest.raises(ValueError, match="MiB safety limit"):
        app._read_csv_bounded(path)


def test_research_robustness_payloads_are_bounded_and_typed():
    assert app._bounded_research_rows({"rows": [{"symbol": "BTC"}]}, "rows") == [{"symbol": "BTC"}]
    with pytest.raises(TypeError, match="JSON array"):
        app._bounded_research_rows({"rows": {}}, "rows")
    with pytest.raises(TypeError, match="JSON objects"):
        app._bounded_research_rows({"rows": ["not-an-object"]}, "rows")
    with pytest.raises(ValueError, match="row limit"):
        app._bounded_research_rows({"rows": [{}] * 5001}, "rows")


def test_holdout_is_not_consumed_when_backtest_fails(tmp_path, monkeypatch):
    import research_provenance as provenance

    path = tmp_path / "holdout.json"
    vault = HoldoutVault(path)
    vault.seal(dataset_id="D1", config_hash="C1", source="csv", symbol="BTCUSDT",
               timeframe="1h", start_timestamp=1, end_timestamp=2)
    frame = pd.DataFrame({"time": [1.0, 2.0]})
    monkeypatch.setattr(app, "HOLDOUT_VAULT_PATH", str(path))
    monkeypatch.setattr(app, "load", lambda *args, **kwargs: frame)
    monkeypatch.setattr(app, "_audit_research_frame", lambda *args, **kwargs: {"state": "OK"})
    monkeypatch.setattr(app, "conf", dict)
    monkeypatch.setattr(app, "_research_execution_config", lambda *args, **kwargs: {})
    monkeypatch.setattr(provenance, "dataset_fingerprint", lambda *args, **kwargs: "D1")
    monkeypatch.setattr(provenance, "config_fingerprint", lambda *args, **kwargs: "C1")

    def fail_backtest(*args, **kwargs):
        raise RuntimeError("simulated transient backtest failure")

    monkeypatch.setattr(app, "backtest", fail_backtest)
    payload = {"source": "csv", "symbol": "BTCUSDT", "tf": "1h", "n": 100}
    with pytest.raises(app.HTTPException):
        app.api_evaluate_holdout(payload)
    assert vault.status()["status"] == "SEALED"
    assert vault.status()["access_count"] == 0



@pytest.mark.parametrize("address", [
    "127.0.0.1",
    "127.0.0.2",
    "127.255.255.254",
    "::1",
    "::ffff:127.0.0.1",
])
def test_all_loopback_addresses_are_recognized(address):
    assert app._loopback(address)


@pytest.mark.parametrize("address", ["192.0.2.1", "10.0.0.1", "2001:db8::1", "not-an-ip"])
def test_non_loopback_addresses_are_not_trusted(address):
    assert not app._loopback(address)


def test_holdout_vault_rejects_non_object_metadata_and_writes_private_file(tmp_path):
    path = tmp_path / "vault.json"
    path.write_text("[]", encoding="utf-8")
    vault = HoldoutVault(path)
    assert vault.status()["status"] == "CORRUPT"
    path.unlink()
    vault.seal(dataset_id="D1", config_hash="C1", source="csv", symbol="BTCUSDT",
               timeframe="1h", start_timestamp=1, end_timestamp=2)
    if os.name == "posix":
        assert path.stat().st_mode & 0o777 == 0o600
    vault.claim(dataset_id="D1", config_hash="C1", source="csv", symbol="BTCUSDT",
                timeframe="1h", start_timestamp=1, end_timestamp=2)
    if os.name == "posix":
        assert path.stat().st_mode & 0o777 == 0o600


def test_research_registry_database_is_private(tmp_path):
    path = tmp_path / "research.db"
    registry = ResearchRegistry(path)
    registry.summary()
    if os.name == "posix":
        assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("spec", [
    {"entry": {"all": [{"feature": "score", "op": ">", "value": 0}]}, "max_holding_bars": 5001},
    {"entry": {"all": [{"feature": "score", "op": ">", "value": float("inf")}]}},
    {"entry": {"all": [{"feature": "score", "op": ">", "value": 0, "python": "eval"}]}},
    {"entry": {"all": [{"feature": "score", "op": ">", "value": 0}] * 33}},
    {"entry": {"all": [{"feature": "score", "op": ">", "value": True}]}},
])
def test_research_rule_compiler_rejects_unbounded_or_unsafe_specs(spec):
    features = pd.DataFrame({"score": [0.0, 1.0, 2.0]})
    with pytest.raises(ValueError):
        compile_long_flat_rule(features, spec)


def test_mcp_constructor_rejects_invalid_argument_types_and_timeouts():
    with pytest.raises(MCPError):
        StdioMCPClient("python", "not-a-list")
    with pytest.raises(MCPError):
        StdioMCPClient("python", ["-c", "pass"], timeout=float("nan"))
    with pytest.raises(MCPError):
        StdioMCPClient("python", ["-c", "pass"], timeout=999)


def test_mcp_stdout_line_limit_terminates_oversized_child():
    class FakeProcess:
        def __init__(self):
            self.stdout = io.StringIO("x" * 1_000_001)
            self.killed = False

        def kill(self):
            self.killed = True

    client = StdioMCPClient("python", ["-c", "pass"])
    client.proc = FakeProcess()
    waiter = queue.Queue(maxsize=1)
    client._responses[1] = waiter
    client._read_stdout()
    assert client.proc.killed
    assert waiter.get_nowait() == {"_transport_closed": True}



def test_failed_login_tracking_has_a_hard_memory_bound(monkeypatch):
    monkeypatch.setattr(app, "_FAILS", {
        f"192.0.2.{i}": (1, 1.0) for i in range(4096)
    })
    monkeypatch.setattr(app.time, "time", lambda: 100.0)
    app._fail("198.51.100.1")
    assert len(app._FAILS) <= 4096
    assert "198.51.100.1" in app._FAILS


def test_holdout_seal_reloads_and_verifies_server_side_identity(tmp_path, monkeypatch):
    import research_provenance as provenance

    frame = pd.DataFrame({
        "time": [1_700_000_000.0, 1_700_003_600.0],
        "open": [100.0, 101.0], "high": [102.0, 103.0],
        "low": [99.0, 100.0], "close": [101.0, 102.0],
        "volume": [10.0, 11.0],
    })
    vault_path = tmp_path / "holdout.json"
    monkeypatch.setattr(app, "HOLDOUT_VAULT_PATH", str(vault_path))
    monkeypatch.setattr(app, "RESEARCH_REGISTRY_PATH", str(tmp_path / "registry.db"))
    monkeypatch.setattr(app, "load_cached", lambda *args, **kwargs: frame)
    monkeypatch.setattr(app, "_audit_research_frame", lambda *args, **kwargs: {
        "valid": True, "gap_count": 0, "state": "PASS", "warnings": [], "errors": []})
    monkeypatch.setattr(app, "conf", lambda: {})
    monkeypatch.setattr(app, "_research_execution_config", lambda *args, **kwargs: {"frozen": True})
    monkeypatch.setattr(provenance, "dataset_fingerprint", lambda *args, **kwargs: "server-dataset")
    monkeypatch.setattr(provenance, "config_fingerprint", lambda *args, **kwargs: "server-config")
    monkeypatch.setattr(ResearchRegistry, "latest_sample_end", lambda *args, **kwargs: 1_699_999_999.0)

    payload = {"source": "csv", "symbol": "BTCUSDT", "timeframe": "1h",
               "dataset_id": "server-dataset", "config_hash": "server-config"}
    result = app.api_seal_holdout(payload)
    assert result["status"] == "SEALED"
    assert result["dataset_id"] == "server-dataset"
    assert result["config_hash"] == "server-config"
    assert result["previous_research_sample_end"] == 1_699_999_999.0
    assert result["independent_temporal_sample"] is True


def test_holdout_seal_rejects_unregistered_or_overlapping_prior_sample(tmp_path, monkeypatch):
    frame = pd.DataFrame({
        "time": [1_700_000_000.0, 1_700_003_600.0],
        "open": [100.0, 101.0], "high": [102.0, 103.0],
        "low": [99.0, 100.0], "close": [101.0, 102.0],
        "volume": [10.0, 11.0],
    })
    monkeypatch.setattr(app, "HOLDOUT_VAULT_PATH", str(tmp_path / "holdout.json"))
    monkeypatch.setattr(app, "RESEARCH_REGISTRY_PATH", str(tmp_path / "registry.db"))
    monkeypatch.setattr(app, "load_cached", lambda *args, **kwargs: frame)
    monkeypatch.setattr(app, "_audit_research_frame", lambda *args, **kwargs: {
        "valid": True, "gap_count": 0, "state": "PASS", "warnings": [], "errors": []})
    monkeypatch.setattr(ResearchRegistry, "latest_sample_end", lambda *args, **kwargs: None)
    with pytest.raises(app.HTTPException) as exc:
        app.api_seal_holdout({"source": "csv", "symbol": "BTCUSDT", "timeframe": "1h"})
    assert "prior research sample" in exc.value.detail


def test_holdout_seal_rejects_caller_supplied_fingerprint_mismatch(tmp_path, monkeypatch):
    import research_provenance as provenance

    frame = pd.DataFrame({
        "time": [1_700_000_000.0, 1_700_003_600.0],
        "open": [100.0, 101.0], "high": [102.0, 103.0],
        "low": [99.0, 100.0], "close": [101.0, 102.0],
        "volume": [10.0, 11.0],
    })
    monkeypatch.setattr(app, "HOLDOUT_VAULT_PATH", str(tmp_path / "holdout.json"))
    monkeypatch.setattr(app, "RESEARCH_REGISTRY_PATH", str(tmp_path / "registry.db"))
    monkeypatch.setattr(app, "load_cached", lambda *args, **kwargs: frame)
    monkeypatch.setattr(app, "_audit_research_frame", lambda *args, **kwargs: {
        "valid": True, "gap_count": 0, "state": "PASS", "warnings": [], "errors": []})
    monkeypatch.setattr(app, "conf", lambda: {})
    monkeypatch.setattr(app, "_research_execution_config", lambda *args, **kwargs: {"frozen": True})
    monkeypatch.setattr(provenance, "dataset_fingerprint", lambda *args, **kwargs: "actual-dataset")
    monkeypatch.setattr(provenance, "config_fingerprint", lambda *args, **kwargs: "actual-config")
    monkeypatch.setattr(ResearchRegistry, "latest_sample_end", lambda *args, **kwargs: 1_699_999_999.0)
    with pytest.raises(app.HTTPException) as exc:
        app.api_seal_holdout({"source": "csv", "symbol": "BTCUSDT", "timeframe": "1h",
                              "dataset_id": "forged-dataset", "config_hash": "actual-config"})
    assert "dataset fingerprint" in exc.value.detail
    assert not (tmp_path / "holdout.json").exists()


def test_api_compute_slots_reject_over_capacity_without_blocking():
    acquired = []
    try:
        for _ in range(app._API_MAX_CONCURRENT):
            assert app._API_SEMAPHORE.acquire(blocking=False)
            acquired.append(True)
        assert app._acquire_api_slot() is False
    finally:
        for _ in acquired:
            app._API_SEMAPHORE.release()


def test_api_compute_slots_allow_capacity_and_release():
    assert app._acquire_api_slot() is True
    app._API_SEMAPHORE.release()
