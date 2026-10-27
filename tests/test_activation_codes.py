import asyncio
import os
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import delete, select

from tests.db_guard import require_disposable_test_database


async def test_activation_code_formatting_and_normalization():
    from app.api.auth import (ACTIVATION_ALPHABET, format_activation_code,
                              new_activation_token, normalize_activation_code)
    code, digest = new_activation_token()
    assert len(code) == 9 and code[4] == "-"
    assert len(digest) == 64
    raw = normalize_activation_code(code)
    assert len(raw) == 8 and set(raw) <= set(ACTIVATION_ALPHABET)
    assert normalize_activation_code(" abcd - efgh ") == "ABCDEFGH"
    assert format_activation_code("abcd efgh") == "ABCD-EFGH"


pytestmark = [
    pytest.mark.database,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(os.getenv("RUN_DB_TESTS") != "1", reason="requires disposable PostgreSQL"),
]


async def seed_team(username, *, expired=False, legacy=False, activated=False):
    from app.api.auth import legacy_activation_hash, new_activation_token
    from app.db import SessionLocal
    from app.models import ActivationThrottle, Team
    from app.security import hash_password_async
    from app.timing import utc_now
    code, digest = new_activation_token()
    plain = "legacy-token-from-before-short-codes" if legacy else code
    if legacy:
        digest = legacy_activation_hash(plain)
    async with SessionLocal() as db:
        await db.execute(delete(ActivationThrottle).where(
            ActivationThrottle.identifier_digest.is_not(None)))
        old = await db.scalar(select(Team).where(Team.login_username == username))
        if old:
            await db.delete(old)
            await db.flush()
        team = Team(
            code=("A" + username)[-32:], name="Activation test", login_username=username,
            password_hash=await hash_password_async("existing-pass") if activated else None,
            activation_token_hash=None if activated else digest,
            activation_expires_at=None if activated else utc_now() + timedelta(hours=-1 if expired else 2),
        )
        db.add(team)
        await db.commit()
        return team.id, plain


async def activate(client, username, code, password="new-password"):
    return await client.post("/api/auth/team/set-password", json={
        "username": username, "activation_token": code, "new_password": password,
    })


async def test_correct_normalized_and_already_consumed_code():
    require_disposable_test_database()
    _, code = await seed_team("activation-correct")
    pasted = "  " + code.lower().replace("-", " - ") + "  "
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=__import__("app.main", fromlist=["app"]).app), base_url="http://test") as client:
        assert (await activate(client, "activation-correct", pasted)).status_code == 200
        consumed = await activate(client, "activation-correct", code, "another-pass")
    assert consumed.status_code == 400
    assert consumed.json()["detail"] == "Invalid or expired activation details"


async def test_incorrect_expired_and_legacy_credentials_use_generic_response():
    require_disposable_test_database()
    await seed_team("activation-wrong")
    _, expired = await seed_team("activation-expired", expired=True)
    _, legacy = await seed_team("activation-legacy", legacy=True)
    app = __import__("app.main", fromlist=["app"]).app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        wrong = await activate(client, "activation-wrong", "AAAA-AAAA")
        expired_response = await activate(client, "activation-expired", expired)
        legacy_response = await activate(client, "activation-legacy", legacy)
    assert wrong.status_code == expired_response.status_code == 400
    assert wrong.json() == expired_response.json() == {"detail": "Invalid or expired activation details"}
    assert legacy_response.status_code == 200


async def test_regeneration_invalidates_old_code_and_audit_omits_plaintext():
    require_disposable_test_database()
    from app.db import SessionLocal
    from app.models import Admin, AdminAction
    from app.security import create_token, hash_password_async
    team_id, old_code = await seed_team("activation-regenerate")
    async with SessionLocal() as db:
        admin = await db.scalar(select(Admin).where(Admin.username == "activation-admin"))
        if not admin:
            db.add(Admin(username="activation-admin", password_hash=await hash_password_async("admin-pass")))
            await db.commit()
    headers = {"Authorization": "Bearer " + create_token("activation-admin", "admin")}
    app = __import__("app.main", fromlist=["app"]).app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        regenerated = await client.post(f"/api/admin/teams/{team_id}/activation/regenerate",
                                        headers=headers, json={"reason": "replacement requested"})
        assert regenerated.status_code == 200
        body = regenerated.json()
        assert body["activation_token"] != old_code
        assert (await activate(client, "activation-regenerate", old_code)).status_code == 400
        assert (await activate(client, "activation-regenerate", body["activation_token"])).status_code == 200
    async with SessionLocal() as db:
        audit = await db.scalar(select(AdminAction).where(
            AdminAction.action == "REGENERATE_TEAM_ACTIVATION").order_by(AdminAction.id.desc()))
        assert audit and audit.details == f"team_id={team_id}"
        assert body["activation_token"] not in audit.details


async def test_concurrent_activation_succeeds_exactly_once():
    require_disposable_test_database()
    _, code = await seed_team("activation-race")
    app = __import__("app.main", fromlist=["app"]).app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        responses = await asyncio.gather(*(
            activate(client, "activation-race", code, f"password-{i}") for i in range(6)
        ))
    assert [r.status_code for r in responses].count(200) == 1
    assert [r.status_code for r in responses].count(400) == 5


async def test_failed_verification_is_shared_and_throttled():
    require_disposable_test_database()
    from app.api.auth import throttle_digest
    from app.config import get_settings
    from app.db import SessionLocal
    from app.models import ActivationThrottle
    _, code = await seed_team("activation-throttle")
    settings = get_settings()
    app = __import__("app.main", fromlist=["app"]).app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        for _ in range(settings.activation_failed_attempt_limit):
            assert (await activate(client, "activation-throttle", "AAAA-AAAA")).status_code == 400
        assert (await activate(client, "activation-throttle", code)).status_code == 400
    async with SessionLocal() as db:
        throttle = await db.get(ActivationThrottle, throttle_digest("activation-throttle"))
        assert throttle.failed_attempts >= settings.activation_failed_attempt_limit
        assert throttle.locked_until is not None


async def test_activated_team_and_existing_device_are_unchanged():
    require_disposable_test_database()
    from app.db import SessionLocal
    from app.models import Admin, PlayerDevice, Team
    from app.security import create_token, hash_password_async
    from app.timing import utc_now
    team_id, _ = await seed_team("activation-existing", activated=True)
    async with SessionLocal() as db:
        admin = await db.scalar(select(Admin).where(Admin.username == "activation-admin"))
        if not admin:
            db.add(Admin(username="activation-admin", password_hash=await hash_password_async("admin-pass")))
        db.add(PlayerDevice(team_id=team_id, player_number=1, round_code="R1A",
                            session_id="a" * 64, expires_at=utc_now() + timedelta(minutes=5),
                            last_seen_at=utc_now()))
        await db.commit()
    headers = {"Authorization": "Bearer " + create_token("activation-admin", "admin")}
    app = __import__("app.main", fromlist=["app"]).app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(f"/api/admin/teams/{team_id}/activation/regenerate",
                                     headers=headers, json={"reason": "must not reset"})
    assert response.status_code == 409
    async with SessionLocal() as db:
        team = await db.get(Team, team_id)
        device = await db.scalar(select(PlayerDevice).where(PlayerDevice.team_id == team_id))
        assert team.password_hash is not None
        assert device.active and device.revoked_at is None


async def test_admin_create_regenerate_and_team_activate_end_to_end():
    require_disposable_test_database()
    from app.db import SessionLocal
    from app.models import Admin
    from app.security import create_token, hash_password_async
    async with SessionLocal() as db:
        admin = await db.scalar(select(Admin).where(Admin.username == "activation-flow-admin"))
        if not admin:
            db.add(Admin(username="activation-flow-admin",
                         password_hash=await hash_password_async("admin-pass")))
            await db.commit()
    headers = {"Authorization": "Bearer " + create_token("activation-flow-admin", "admin")}
    app = __import__("app.main", fromlist=["app"]).app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/admin/teams", headers=headers, json={
            "code": "ACT-FLOW", "name": "Activation flow", "username": "activation-flow",
            "password": None,
        })
        assert created.status_code == 200
        first = created.json()
        assert len(first["activation_token"]) == 9 and first["activation_token"][4] == "-"
        regenerated = await client.post(
            f"/api/admin/teams/{first['id']}/activation/regenerate",
            headers=headers, json={"reason": "end-to-end verification"},
        )
        assert regenerated.status_code == 200
        second = regenerated.json()
        assert len(second["activation_token"]) == 9 and second["activation_token"][4] == "-"
        assert second["activation_token"] != first["activation_token"]
        assert (await activate(client, "activation-flow", first["activation_token"])).status_code == 400
        assert (await activate(client, "activation-flow", second["activation_token"])).status_code == 200
