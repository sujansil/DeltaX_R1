import io
import json
import os

import httpx
import openpyxl
import pytest
from sqlalchemy import delete

from tests.db_guard import require_disposable_test_database

pytestmark = [pytest.mark.database, pytest.mark.asyncio(loop_scope="session"),
              pytest.mark.skipif(os.getenv("RUN_DB_TESTS") != "1", reason="requires disposable PostgreSQL")]


async def _reset():
    from app.db import SessionLocal
    from app.models import Admin, AdminAction, LeaderboardSnapshot, ScoreEvent, Submission, Team
    async with SessionLocal() as db:
        for model in (AdminAction, LeaderboardSnapshot, ScoreEvent, Submission, Admin, Team):
            await db.execute(delete(model))
        await db.commit()


async def test_xlsx_export_is_authorized_reconciled_and_literal():
    require_disposable_test_database(); await _reset()
    from app.db import SessionLocal
    from app.main import app
    from app.models import Admin, AdminAction, LeaderboardSnapshot, ScoreEvent, Submission, Team
    from app.security import create_token
    async with SessionLocal() as db:
        team = Team(code="=FORMULA", name="+Literal Team", login_username="export-user",
                    password_hash="NEVER_EXPORT_HASH", activation_token_hash="NEVER_EXPORT_TOKEN")
        admin = Admin(username="export-admin", password_hash="NEVER_EXPORT_ADMIN_HASH")
        db.add_all((team, admin)); await db.flush()
        submission = Submission(team_id=team.id, round_code="R1B", question_position=1,
                                player_number=1, answer_text="001234567890123456789.000",
                                result="CORRECT", points=10, finalization_source="TIMEOUT_DRAFT")
        db.add(submission); await db.flush()
        db.add_all((ScoreEvent(team_id=team.id, round_code="R1B", submission_id=submission.id,
                              reason="R1B_CORRECT", points=10),
                    LeaderboardSnapshot(round_code="R1B", data_json=json.dumps([{
                        "rank": 1, "team_id": team.id, "team_code": team.code,
                        "team_name": team.name, "score": 10}])),
                    AdminAction(admin_id=admin.id, action="EXPORT_TEST", details="@literal audit")))
        await db.commit()

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/api/admin/exports/results.xlsx")).status_code in (401, 403)
        response = await client.get("/api/admin/exports/results.xlsx?scope=BOTH", headers={
            "Authorization": "Bearer " + create_token("export-admin", "admin")})
    assert response.status_code == 200
    assert response.content[:2] == b"PK"
    workbook = openpyxl.load_workbook(io.BytesIO(response.content), data_only=False)
    assert workbook.sheetnames == ["Export Metadata", "Team Results", "Question Outcomes",
                                   "R1A Enrolment", "Published Snapshots", "Admin Audit"]
    teams = workbook["Team Results"]
    assert teams["B2"].value == "=FORMULA" and teams["B2"].data_type == "s"
    assert teams["F2"].value == 10
    outcomes = workbook["Question Outcomes"]
    assert outcomes["H2"].value == "001234567890123456789.000"
    assert outcomes["H2"].data_type == "s" and outcomes["M2"].value == 10
    all_text = " ".join(str(cell.value) for sheet in workbook for row in sheet for cell in row)
    assert "NEVER_EXPORT" not in all_text
    await _reset()
