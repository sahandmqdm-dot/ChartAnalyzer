import time

import pytest

import agent_core as AC


def test_resolved_prediction_journal_bridges_frozen_decision_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr(AC, "DB_PATH", str(tmp_path / "journal.db"))
    bar = (int(time.time()) // 3600 - 1) * 3600
    AC.log_prediction(
        "ramzinex:BTCIRR", "1h", bar, 1, 100.0, 0.6, 98.0, 101.0, 104.0,
        2.5, "Bullish", gate="ALLOWED",
        extra={
            "decision_action": "ACT",
            "decision_reason_code": "GATE_ALLOWED",
            "fee_roundtrip": 0.001,
            "spread_slippage_roundtrip": 0.001,
            "market_type": "spot",
        },
    )
    with AC._db() as con:
        con.execute(
            "UPDATE predictions SET status='resolved', outcome=?, resolved_at=?",
            (102.0, bar + 7201),
        )

    report = AC.evaluate_decision_journal("ramzinex:BTCIRR", "1h", 1)
    assert report["status"] == "DESCRIPTIVE_ONLY"
    assert report["resolved_rows_seen"] == 1
    assert report["metrics"]["sample"]["acted"] == 1
    assert report["metrics"]["policy"]["unit_notional_net_return_sum"] == pytest.approx(0.018)
    assert report["metrics"]["evaluation_contract"]["causal_claim"] is False
    assert "not stop/target execution PnL" in report["limitations"][0]


def test_unannotated_legacy_rows_are_not_silently_scored(tmp_path, monkeypatch):
    monkeypatch.setattr(AC, "DB_PATH", str(tmp_path / "legacy.db"))
    bar = (int(time.time()) // 3600 - 1) * 3600
    AC.log_prediction(
        "ramzinex:BTCIRR", "1h", bar, 1, 100.0, 0.6, 98.0, 101.0, 104.0,
        2.5, "Bullish",
    )
    with AC._db() as con:
        con.execute(
            "UPDATE predictions SET status='resolved', outcome=?, resolved_at=?",
            (102.0, time.time()),
        )

    report = AC.evaluate_decision_journal("ramzinex:BTCIRR", "1h", 1)
    assert report["status"] == "INSUFFICIENT_DATA"
    assert report["metadata_exclusions"]["missing_decision_metadata"] == 1
    assert "metrics" not in report
