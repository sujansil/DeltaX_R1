"""Development provisioning guards with a fake DB; no real credentials or writes."""
from unittest.mock import AsyncMock, MagicMock
import httpx
import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def invoke(monkeypatch, environment, *, exists=None, payload=None):
    from app.main import app
    from app.api import auth
    from app.db import get_db
    fake = MagicMock()
    fake.scalar = AsyncMock(return_value=exists)
    fake.commit = AsyncMock()
    monkeypatch.setattr(auth.settings, "environment", environment)
    monkeypatch.setattr(auth, "hash_password_async", AsyncMock(return_value="fixture-hash"))
    async def database():
        yield fake
    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = database
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/auth/admin/setup", json=payload or {
                "username": "fixture-admin", "password": "fixture-password"
            })
        return response, fake, auth.hash_password_async
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


async def test_setup_disabled_outside_development(monkeypatch):
    for environment in ("production", "test", "staging"):
        response, db, hash_fn = await invoke(monkeypatch, environment)
        assert response.status_code == 404
        db.scalar.assert_not_awaited()
        db.commit.assert_not_awaited()
        hash_fn.assert_not_awaited()


async def test_setup_never_resets_existing_account(monkeypatch):
    response, db, hash_fn = await invoke(monkeypatch, "development", exists=7)
    assert response.status_code == 409
    db.commit.assert_not_awaited()
    hash_fn.assert_not_awaited()
    db.add.assert_not_called()


async def test_setup_uses_json_and_returns_no_password(monkeypatch):
    response, db, hash_fn = await invoke(monkeypatch, "development")
    assert response.status_code == 200
    assert response.json() == {"created": True, "username": "fixture-admin"}
    assert "fixture-password" not in response.text and "fixture-hash" not in response.text
    db.commit.assert_awaited_once()
    account = db.add.call_args.args[0]
    assert account.username == "fixture-admin" and account.password_hash == "fixture-hash"


async def test_setup_validates_password_and_blank_username(monkeypatch):
    response, db, _ = await invoke(monkeypatch, "development", payload={"username": " ", "password": "fixture-password"})
    assert response.status_code == 422
    db.commit.assert_not_awaited()
    response, db, _ = await invoke(monkeypatch, "development", payload={"username": "fixture", "password": "short"})
    assert response.status_code == 422
    db.commit.assert_not_awaited()
