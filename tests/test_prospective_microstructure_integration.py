# ruff: noqa: I001  # Flat-layout import ordering differs across Ruff versions.
import hashlib
import json
from datetime import UTC, datetime, timedelta

import app
from binance_stream_collector import HashChainJSONLStore


BASE = datetime(2020, 1, 1, 12, 0, tzinfo=UTC)
SESSION = "app-bound-active-session"


def _stamp(seconds):
    return (BASE + timedelta(seconds=seconds)).isoformat()


def test_app_builds_and_archives_cutoff_matched_binance_evidence(monkeypatch, tmp_path):
    store = HashChainJSONLStore(tmp_path, fsync_each_event=False)
    store.append(
        "spot", "session_manifest",
        {"state": "started", "streams": ["btcusdt@trade"], "started_at": _stamp(0)},
        received_at=_stamp(0), session_id=SESSION,
    )
    raw = '{"stream":"btcusdt@trade","data":{"t":1}}'
    store.append(
        "spot", "raw_frames",
        {"encoding": "utf-8", "raw_message": raw},
        received_at=_stamp(1), session_id=SESSION,
    )
    store.append(
        "spot", "trades",
        {"s": "BTCUSDT", "p": "90000", "q": "0.2", "m": False, "T": 1577880001000, "t": 1},
        received_at=_stamp(1), event_time=_stamp(0.5),
        stream_name="btcusdt@trade", sequence={"t": 1}, session_id=SESSION,
    )
    store.close()
    monkeypatch.setenv("CHARTANALYZER_DATA_DIR", str(tmp_path))

    provenance = app._prospective_binance_microstructure_provenance(
        {
            "symbol": "BTCUSDT",
            "microstructure_windows_seconds": [5, 60, 300],
            "microstructure_book_depth": 10,
        },
        datetime.fromisoformat(_stamp(3)),
    )

    artifact = tmp_path / provenance["artifact_path"]
    payload = artifact.read_bytes()
    assert hashlib.sha256(payload).hexdigest() == provenance["artifact_sha256"]
    document = json.loads(payload)
    assert document["dataset_hash"] == provenance["dataset_hash"]
    assert document["snapshots"][0]["decision_time"] == _stamp(3)
    assert provenance["point_in_time_valid"] is True
    assert provenance["session_closed"] is False
