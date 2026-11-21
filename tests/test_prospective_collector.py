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


def test_config_rejects_non_origin_loopback_url(tmp_path):
    config_path = tmp_path / "collector.json"
    data = _config(tmp_path)
    data["api_base_url"] = "http://127.0.0.1:8765/private"
    config_path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="origin"):
        _load_config(config_path)


def test_config_rejects_invalid_poll_interval(tmp_path):
    config_path = tmp_path / "collector.json"
    data = _config(tmp_path)
    data["poll_seconds"] = 5
    config_path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="poll_seconds"):
        _load_config(config_path)


def test_collector_rejects_status_for_another_cohort(tmp_path):
    config = _config(tmp_path)
    session = FakeSession(
        {"cohort": {"cohort_id": "different"}, "integrity": {"status": "PASS"}},
        {},
    )
    collector = ProspectiveCollector({
        **config, "state_file": Path(config["state_file"]),
        "cohort_id": config["cohort_payload"]["cohort_id"],
    }, session=session)
    with pytest.raises(RuntimeError, match="different or unregistered"):
        collector.run_once()
    assert [call[0] for call in session.calls] == ["GET"]


def test_collector_persists_heartbeat_and_preserves_last_success(tmp_path):
    config = _config(tmp_path)
    state_path = Path(config["state_file"])
    cohort_id = config["cohort_payload"]["cohort_id"]
    collector = ProspectiveCollector({
        **config, "state_file": state_path, "cohort_id": cohort_id,
    }, session=FakeSession({}, {}))
    collector._write_state(success=True, result={"terminal": False})
    first = json.loads(state_path.read_text(encoding="utf-8"))
    assert first["last_success_at"]
    collector.failures = 2
    collector._write_state(success=False, error="provider timeout")
    second = json.loads(state_path.read_text(encoding="utf-8"))
    assert second["last_success_at"] == first["last_success_at"]
    assert second["consecutive_failures"] == 2
    assert second["last_error"] == "provider timeout"


def test_single_instance_lock_rejects_second_process_lock(tmp_path):
    from prospective_collector import _single_instance_lock

    lock_path = tmp_path / "collector.lock"
    with _single_instance_lock(lock_path), pytest.raises(RuntimeError, match="already holds"):
        _single_instance_lock(lock_path).__enter__()


def test_run_forever_stops_after_cohort_is_evaluated(tmp_path):
    config = _config(tmp_path)
    cohort_id = config["cohort_payload"]["cohort_id"]
    collector = ProspectiveCollector({
        **config, "state_file": Path(config["state_file"]), "cohort_id": cohort_id,
    }, session=FakeSession({}, {}))
    collector.run_once = lambda: {
        "terminal": True,
        "cohort": {"status": "EVALUATED_ONCE", "forecast_count": 200, "resolved_count": 200},
        "issued_forecast": None,
        "resolved_this_tick": 0,
    }
    collector.run_forever()
    state = json.loads(Path(config["state_file"]).read_text(encoding="utf-8"))
    assert state["last_tick"]["terminal"] is True


def test_state_write_fails_closed_on_corrupt_state(tmp_path):
    config = _config(tmp_path)
    state_path = Path(config["state_file"])
    state_path.write_text("{truncated", encoding="utf-8")
    collector = ProspectiveCollector({
        **config, "state_file": state_path,
        "cohort_id": config["cohort_payload"]["cohort_id"],
    }, session=FakeSession({}, {}))
    with pytest.raises(RuntimeError, match="unreadable"):
        collector._write_state(success=True, result={"terminal": False})
    assert state_path.read_text(encoding="utf-8") == "{truncated"


def test_state_write_rejects_different_cohort(tmp_path):
    config = _config(tmp_path)
    state_path = Path(config["state_file"])
    state_path.write_text(json.dumps({"cohort_id": "other-cohort"}), encoding="utf-8")
    collector = ProspectiveCollector({
        **config, "state_file": state_path,
        "cohort_id": config["cohort_payload"]["cohort_id"],
    }, session=FakeSession({}, {}))
    with pytest.raises(RuntimeError, match="different cohort"):
        collector._write_state(success=True, result={"terminal": False})
