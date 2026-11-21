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
    value = "test-secret-with-more-than-32-characters"
    assert Settings(_env_file=None, app_secret=value).app_secret == value
