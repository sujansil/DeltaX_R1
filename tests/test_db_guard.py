import pytest

from tests.db_guard import EXPECTED_GUARD, require_disposable_test_database


def test_database_guard_accepts_only_expected_local_target(monkeypatch):
    monkeypatch.setenv("RUN_DB_TESTS", "1")
    monkeypatch.setenv("ROUND1_TEST_DATABASE_GUARD", EXPECTED_GUARD)
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://test-user:test-password@127.0.0.1:5432/round1_test",
    )
    require_disposable_test_database()


@pytest.mark.parametrize(
    "database_url",
    [
        "postgresql+asyncpg://test-user:test-password@database.example/round1_test",
        "postgresql+asyncpg://test-user:test-password@127.0.0.1:5432/round1",
    ],
)
def test_database_guard_rejects_non_test_targets(monkeypatch, database_url):
    monkeypatch.setenv("RUN_DB_TESTS", "1")
    monkeypatch.setenv("ROUND1_TEST_DATABASE_GUARD", EXPECTED_GUARD)
    monkeypatch.setenv("DATABASE_URL", database_url)
    with pytest.raises(RuntimeError):
        require_disposable_test_database()
