import pytest
from pydantic import ValidationError

from app.settings import Settings


def test_secret_is_required():
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_secret_is_accepted():
    assert Settings(_env_file=None, app_secret="test-secret").app_secret == "test-secret"
