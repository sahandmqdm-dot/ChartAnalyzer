from pydantic import SecretStr

from app.settings import Settings


def test_coinalyze_api_key_is_optional_and_secret(monkeypatch):
    monkeypatch.delenv("COINALYZE_API_KEY", raising=False)

    default = Settings(
        _env_file=None,
        app_secret="test-secret-with-more-than-32-characters",
    )
    assert default.coinalyze_api_key is None

    configured = Settings(
        _env_file=None,
        app_secret="test-secret-with-more-than-32-characters",
        coinalyze_api_key="test-coinalyze-key",
    )
    assert isinstance(configured.coinalyze_api_key, SecretStr)
    assert configured.coinalyze_api_key.get_secret_value() == "test-coinalyze-key"
    assert "test-coinalyze-key" not in repr(configured)
