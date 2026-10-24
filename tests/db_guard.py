import os
from urllib.parse import urlsplit


EXPECTED_GUARD = "round1-disposable-postgres"
EXPECTED_DATABASE = "round1_test"
ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1"}


def require_disposable_test_database() -> None:
    """Refuse database tests unless the target is the local disposable database."""
    if os.getenv("RUN_DB_TESTS") != "1":
        raise RuntimeError("Database tests require RUN_DB_TESTS=1")
    if os.getenv("ROUND1_TEST_DATABASE_GUARD") != EXPECTED_GUARD:
        raise RuntimeError("Database tests require the disposable-database guard")

    database_url = os.getenv("DATABASE_URL", "")
    parsed = urlsplit(database_url)
    database = parsed.path.lstrip("/")
    if parsed.scheme != "postgresql+asyncpg":
        raise RuntimeError("Database tests require a postgresql+asyncpg URL")
    if parsed.hostname not in ALLOWED_HOSTS:
        raise RuntimeError("Database tests may only target local PostgreSQL")
    if database != EXPECTED_DATABASE:
        raise RuntimeError(f"Database tests may only target {EXPECTED_DATABASE}")

