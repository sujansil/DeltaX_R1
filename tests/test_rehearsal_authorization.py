"""Provisioning rejects unsafe targets before making any database connection."""
import pytest

from scripts.authorize_rehearsal import validate_target


def safe_environment():
    return {
        "DATABASE_URL": "postgresql+asyncpg://test:unused@127.0.0.1:5432/round1_rehearsal",
        "ENVIRONMENT": "rehearsal", "R1_RESET_ENABLED": "true",
        "R1_RESET_DATABASE_NAME": "round1_rehearsal", "R1_REHEARSAL_TOKEN": "x" * 48,
    }


def test_authorization_validates_exact_disposable_identity_without_connecting():
    env = safe_environment()
    url, digest = validate_target(env, "round1_rehearsal")
    assert url.database == "round1_rehearsal" and len(digest) == 64
    assert env["R1_REHEARSAL_TOKEN"] not in digest


@pytest.mark.parametrize("key,value", [
    ("DATABASE_URL", "postgresql+asyncpg://test:unused@competition.postgres.database.azure.com/round1_rehearsal"),
    ("DATABASE_URL", "postgresql+asyncpg://test:unused@127.0.0.1/round1"),
    ("DATABASE_URL", "postgresql+asyncpg://test:unused@127.0.0.1/round1_rehearsal?host=competition"),
    ("DATABASE_URL", "sqlite:///round1_rehearsal"),
    ("ENVIRONMENT", "production"), ("ENVIRONMENT", "development"),
    ("R1_RESET_ENABLED", "false"), ("R1_RESET_DATABASE_NAME", "competition"),
    ("R1_REHEARSAL_TOKEN", "short"),
])
def test_authorization_refuses_unsafe_target_before_connecting(key, value):
    env = safe_environment()
    env[key] = value
    with pytest.raises(ValueError):
        validate_target(env, "round1_rehearsal")


def test_authorization_does_not_fall_back_to_repository_env():
    with pytest.raises(Exception):
        validate_target({}, "round1_rehearsal")
