import sqlite3

import pytest

import agent_core
from research_registry import ResearchRegistry


def _assert_context_manager_closes(connection):
    with connection as active:
        assert active.execute("SELECT 1").fetchone()[0] == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("SELECT 1")


def test_prediction_journal_connection_closes_after_context(monkeypatch, tmp_path):
    monkeypatch.setattr(agent_core, "DB_PATH", str(tmp_path / "agent-journal.db"))
    _assert_context_manager_closes(agent_core._db())


def test_research_registry_connection_closes_after_context(tmp_path):
    registry = ResearchRegistry(tmp_path / "research-registry.db")
    _assert_context_manager_closes(registry._db())


def test_connection_closes_when_context_body_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(agent_core, "DB_PATH", str(tmp_path / "agent-journal-error.db"))
    connection = agent_core._db()
    with pytest.raises(RuntimeError, match="expected test failure"), connection:
        raise RuntimeError("expected test failure")
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("SELECT 1")
