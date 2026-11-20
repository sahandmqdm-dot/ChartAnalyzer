import json
from pathlib import Path

import pytest

from prospective_collector import ProspectiveCollector, _load_config


def _config(tmp_path):
    return {
        "api_base_url": "http://127.0.0.1:8765",
        "poll_seconds": 300,
        "request_timeout_seconds": 45,
        "state_file": str(tmp_path / "state.json"),
        "cohort_payload": {
            "cohort_id": "BTC-SPOT-1H-H4",
            "source": "ramzinex",
            "symbol": "BTCUSDT",
            "tf": "1h",
            "horizon_bars": 4,
            "paths": 3000,
            "n": 1200,
        },
    }


def test_config_rejects_non_loopback_api(tmp_path):
    config_path = tmp_path / "collector.json"
    data = _config(tmp_path)
    data["api_base_url"] = "http://0.0.0.0:8765"
    config_path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="loopback only"):
        _load_config(config_path)


def test_config_requires_frozen_cohort_payload(tmp_path):
    config_path = tmp_path / "collector.json"
    data = _config(tmp_path)
    data["cohort_payload"].pop("symbol")
    config_path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="symbol"):
        _load_config(config_path)


class FakeResponse:
    def __init__(self, body):
        self.body = body

    def raise_for_status(self):
        return None

    def json(self):
        return self.body


class FakeSession:
    def __init__(self, status_body, tick_body):
        self.status_body = status_body
        self.tick_body = tick_body
        self.headers = {}
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return FakeResponse(self.status_body)

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return FakeResponse(self.tick_body)


def test_collector_checks_ledger_integrity_before_tick(tmp_path):
    config = _config(tmp_path)
    status = {"cohort": {"cohort_id": config["cohort_payload"]["cohort_id"]},
              "integrity": {"status": "FAIL"}}
    session = FakeSession(status, {})
    collector = ProspectiveCollector({
        **config, "state_file": Path(config["state_file"]),
        "cohort_id": config["cohort_payload"]["cohort_id"],
    }, session=session)
    with pytest.raises(RuntimeError, match="integrity"):
        collector.run_once()
    assert [call[0] for call in session.calls] == ["GET"]


def test_collector_ticks_only_the_registered_cohort(tmp_path):
    config = _config(tmp_path)
    cohort_id = config["cohort_payload"]["cohort_id"]
    status = {"cohort": {"cohort_id": cohort_id, "report_available": False},
              "integrity": {"status": "PASS"}}
    tick = {"cohort": {"cohort_id": cohort_id, "status": "COLLECTING",
                       "forecast_count": 1, "resolved_count": 0},
            "issued_forecast": {"forecast_id": "f-1"},
            "resolved_this_tick": 0, "unresolved_due": [], "report_available": False}
    session = FakeSession(status, tick)
    collector = ProspectiveCollector({
        **config, "state_file": Path(config["state_file"]), "cohort_id": cohort_id,
    }, session=session)
    result = collector.run_once()
    assert result["cohort"]["forecast_count"] == 1
    assert result["issued_forecast"]["forecast_id"] == "f-1"
    assert [call[0] for call in session.calls] == ["GET", "POST"]
    assert session.calls[1][2]["json"] == config["cohort_payload"]


def test_collector_never_requests_final_evaluation(tmp_path):
    config = _config(tmp_path)
    cohort_id = config["cohort_payload"]["cohort_id"]
    status = {"cohort": {"cohort_id": cohort_id, "report_available": True},
              "integrity": {"status": "PASS"}}
    session = FakeSession(status, {})
    collector = ProspectiveCollector({
        **config, "state_file": Path(config["state_file"]), "cohort_id": cohort_id,
    }, session=session)
    result = collector.run_once()
    assert result["terminal"] is True
    assert [call[0] for call in session.calls] == ["GET"]


def test_atomic_state_write_is_valid_json_and_restrictive(tmp_path):
    from prospective_collector import _atomic_state_write

    path = tmp_path / "state.json"
    _atomic_state_write(path, {"ok": True, "cohort_id": "cohort"})
    assert json.loads(path.read_text(encoding="utf-8"))["ok"] is True
    assert not list(tmp_path.glob("*.tmp"))
