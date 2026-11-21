import pytest
from pydantic import ValidationError

from app.settings import Settings


def test_secret_is_required(monkeypatch):
    monkeypatch.delenv("APP_SECRET", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_secret_minimum_length():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, app_secret="too-short")


def test_secret_is_accepted():
    config = Settings(_env_file=None, app_secret="test-secret-that-is-long-enough")
    assert config.app_secret == "test-secret-that-is-long-enough"
