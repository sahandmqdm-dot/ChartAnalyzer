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


def test_futures_market_data_provider_defaults_to_binance_usdm():
    value = Settings(
        _env_file=None,
        app_secret="test-secret-with-more-than-32-characters",
    )
    assert value.futures_market_data_provider == "binance_usdm_futures"


def test_futures_market_data_provider_can_be_configured_independently():
    value = Settings(
        _env_file=None,
        app_secret="test-secret-with-more-than-32-characters",
        market_data_provider="binance_spot",
        futures_market_data_provider="bybit_linear_futures",
    )
    assert value.market_data_provider == "binance_spot"
    assert value.futures_market_data_provider == "bybit_linear_futures"


def test_futures_market_data_provider_rejects_unknown_values():
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            app_secret="test-secret-with-more-than-32-characters",
            futures_market_data_provider="not-a-futures-provider",
        )


def test_production_requires_strong_admin_password():
    with pytest.raises(ValidationError, match="at least 16"):
        Settings(
            _env_file=None,
            app_secret="test-secret-with-more-than-32-characters",
            APP_ENV="production",
            ADMIN_PASSWORD="short-password",
        )


def test_production_accepts_long_admin_password():
    value = Settings(
        _env_file=None,
        app_secret="test-secret-with-more-than-32-characters",
        APP_ENV="production",
        ADMIN_PASSWORD="long-unique-production-password",
    )
    assert value.admin_password == "long-unique-production-password"


def test_provider_setting_rejects_unknown_values():
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            app_secret="test-secret-with-more-than-32-characters",
            market_data_provider="not-an-exchange",
        )


def test_active_collector_limits_default_to_ten():
    value = Settings(_env_file=None, app_secret="test-secret-with-more-than-32-characters")
    assert value.max_active_futures_market_streams == 10
    assert value.max_active_open_interest_collectors == 10
    assert value.max_active_liquidation_streams == 10


@pytest.mark.parametrize(
    "field",
    [
        "max_active_futures_market_streams",
        "max_active_open_interest_collectors",
        "max_active_liquidation_streams",
    ],
)
@pytest.mark.parametrize("limit", [0, 101])
def test_active_collector_limits_are_bounded(field, limit):
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            app_secret="test-secret-with-more-than-32-characters",
            **{field: limit},
        )
