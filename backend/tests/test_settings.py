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


def test_market_data_provider_defaults_to_binance():
    value = Settings(_env_file=None, app_secret="test-secret-with-more-than-32-characters")
    assert value.market_data_provider == "binance_spot"


def test_market_data_provider_can_be_configured():
    value = Settings(
        _env_file=None,
        app_secret="test-secret-with-more-than-32-characters",
        market_data_provider="bybit_spot",
    )
    assert value.market_data_provider == "bybit_spot"


def test_production_requires_strong_admin_password():
    with pytest.raises(ValidationError, match="at least 16"):
        Settings(
            _env_file=None,
            app_secret="test-secret-with-more-than-32-characters",
            environment="production",
            admin_password="short-password",
        )


def test_production_accepts_long_admin_password():
    value = Settings(
        _env_file=None,
        app_secret="test-secret-with-more-than-32-characters",
        environment="production",
        admin_password="long-unique-production-password",
    )
    assert value.admin_password == "long-unique-production-password"


def test_provider_setting_rejects_unknown_values():
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            app_secret="test-secret-with-more-than-32-characters",
            market_data_provider="not-an-exchange",
        )
