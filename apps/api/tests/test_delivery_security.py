import pytest
from pydantic import ValidationError

from interview_api.core.config import Settings


@pytest.mark.parametrize("environment,secret", [
    ("development", "change_me_to_a_random_string"), ("test", "test-secret"),
])
def test_local_jwt_allowed(environment, secret):
    assert Settings(_env_file=None, app_env=environment, jwt_secret_key=secret).app_env == environment


@pytest.mark.parametrize("secret", [
    "", "change_me_to_a_random_string", "change-me", "your-jwt-secret",
    "placeholder-secret-for-production-please-change", "a" * 64,
])
def test_production_jwt_rejected_without_secret_disclosure(secret):
    with pytest.raises(ValidationError) as caught:
        Settings(_env_file=None, app_env="production", jwt_secret_key=secret)
    assert "JWT_SECRET_KEY" in str(caught.value)
    assert "input_value=" not in str(caught.value)


def test_missing_production_secret_rejected(monkeypatch):
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, app_env="production")


def test_production_strong_secret_allowed():
    # Test-only deterministic value, not a deployable credential.
    assert Settings(_env_file=None, app_env="production",
                    jwt_secret_key="f9V2!k6Z#r0Q8@w3X7$n1L5%j4C0&b2M9").app_env == "production"


def test_unknown_environment_fails_closed():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, app_env="prodution", jwt_secret_key="change-me")
