"""Targeted corrections; reset behavior uses mocks, never a database reset.

Other integration cases reuse the guarded, isolated-schema publication fixture.
"""
import asyncio
import io
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import openpyxl
import pytest
from sqlalchemy import event, select, text

from tests.test_r1_global_features import ctx, preview, publish, seed_combined

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest.mark.parametrize("mode", ["CONTINUOUS", "PAUSE_DURING_HANDOVER"])
@pytest.mark.parametrize("selected", [False, True])
@pytest.mark.parametrize("status", ["DRAFT", "ENDED"])
async def test_timer_configuration_survives_mocked_rehearsal_clear(monkeypatch, mode, selected, status):
    from app.api import admin

    rounds = [SimpleNamespace(code=code, status=status, r1b_timer_mode=mode,
                              r1b_timer_mode_selected=selected, started_at="old",
                              ended_at="old", paused_at="old", current_position=3,
                              question_started_at="old", r1a_result_due_at="old",
                              leaderboard_published_at="old", rules_snapshot="old",
                              rules_version="old") for code in ("R1A", "R1B")]
    db = MagicMock()
    db.scalars = AsyncMock(return_value=SimpleNamespace(all=lambda: rounds))
    db.execute = AsyncMock(return_value=SimpleNamespace(rowcount=0))
    db.commit = AsyncMock()
    monkeypatch.setattr(admin, "reset_authorization", AsyncMock(return_value=(True, "test-only mock")))
    monkeypatch.setattr(admin, "acting_admin_id", AsyncMock(return_value=1))
    result = await admin.reset_round_one(admin.ResetRoundOneIn(
        reason="mocked correction test", confirmation="RESET ROUND 1"), {"sub": "test"}, db)
    assert result["ok"]
    for round_obj in rounds:
        assert round_obj.status == "DRAFT"
        assert (round_obj.r1b_timer_mode, round_obj.r1b_timer_mode_selected) == (mode, selected)
        assert all(getattr(round_obj, key) is None for key in (
            "started_at", "ended_at", "paused_at", "current_position", "question_started_at",
            "r1a_result_due_at", "leaderboard_published_at", "rules_snapshot", "rules_version"))
    assert {call.args[0].table.name for call in db.execute.call_args_list
            if getattr(call.args[0], "is_delete", False)} == {
        "score_events", "r1a_receipts", "r1b_receipts", "submissions", "r1b_drafts",
        "team_round_states", "round_enrollments", "leaderboard_snapshots"}
    db.commit.assert_awaited_once()


async def test_competition_environment_cannot_clear_publications_even_with_admin(monkeypatch):
    from fastapi import HTTPException
    from app.api import admin

    monkeypatch.setattr(admin.settings, "environment", "production")
    db = MagicMock()
    db.scalars = AsyncMock(return_value=SimpleNamespace(all=lambda: [
        SimpleNamespace(code=code, status="ENDED") for code in ("R1A", "R1B")]))
    db.execute = AsyncMock()
    db.commit = AsyncMock()
    with pytest.raises(HTTPException) as error:
        await admin.reset_round_one(admin.ResetRoundOneIn(reason="denied",
            confirmation="RESET ROUND 1"), {"sub": "admin"}, db)
    assert error.value.status_code == 403
    db.execute.assert_not_awaited()
    db.commit.assert_not_awaited()


async def workbook(ctx, scope):
    response = await ctx["client"].get("/api/admin/exports/results.xlsx?scope=" + scope,
                                       headers=ctx["headers"])
    assert response.status_code == 200, response.text
    return openpyxl.load_workbook(io.BytesIO(response.content), data_only=False)


async def prepare_export(ctx):
    async with ctx["local"]() as db:
        await db.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
        await db.execute(text("INSERT INTO alembic_version VALUES ('0016_rehearsal_reset_guard')"))
        await db.commit()


def sheet_values(sheet):
    return [[cell.value for cell in row] for row in sheet]


@pytest.mark.database
async def test_frozen_xlsx_and_individual_exports_do_not_follow_source_changes(ctx):
    from app.models import ScoreEvent, Team

    ids = await seed_combined(ctx)
    await prepare_export(ctx)
    async with ctx["local"]() as db:
        (await db.get(Team, ids["A"])).name = "=Literal Team"
        await db.commit()
    before = {scope: await workbook(ctx, scope) for scope in ("R1A", "R1B", "BOTH")}
    assert all("Final Combined R1" not in book.sheetnames for book in before.values())
    assert (await publish(ctx)).status_code == 200
    final = (await ctx["client"].get("/api/leaderboard/final")).json()
    combined = await workbook(ctx, "BOTH")
    sheet = combined["Final Combined R1"]
    rows = sheet_values(sheet)
    assert rows[0][:5] == ["Rank", "Team Name", "R1(a) Score", "R1(b) Score", "Combined Total"]
    assert [row[:5] for row in rows[1:]] == [
        [row["rank"], row["team_name"], row["r1a_score"], row["r1b_score"], row["score"]]
        for row in final["data"]]
    assert sheet["B2"].value == "=Literal Team" and sheet["B2"].data_type == "s"
    assert any(row[4] == -1500 for row in rows[1:])
    assert all(row[7] == "YES" and row[9] == final["publication_id"] for row in rows[1:])
    for scope in ("R1A", "R1B"):
        after = await workbook(ctx, scope)
        assert after.sheetnames == before[scope].sheetnames
        for name in ("Team Results", "Question Outcomes", "R1A Enrolment", "Published Snapshots"):
            assert sheet_values(after[name]) == sheet_values(before[scope][name])
    async with ctx["local"]() as db:
        (await db.get(Team, ids["A"])).name = "Changed afterward"
        db.add(ScoreEvent(team_id=ids["A"], round_code="R1B", points=-999999, reason="source edit"))
        await db.commit()
    assert sheet_values((await workbook(ctx, "BOTH"))["Final Combined R1"]) == rows
    assert (await ctx["client"].get("/api/leaderboard/final")).json() == final


@pytest.mark.database
async def test_frozen_xlsx_reports_cutoff_tie_without_selecting_qualifiers(ctx):
    from app.models import RoundEnrollment, Team

    await seed_combined(ctx)
    await prepare_export(ctx)
    async with ctx["local"]() as db:
        for number in range(18):
            team = Team(code=f"TIE{number:02}", name=f"Tied {number}", login_username=f"tie{number}")
            db.add(team)
            await db.flush()
            db.add(RoundEnrollment(team_id=team.id, round_code="R1A"))
        await db.commit()
    assert (await publish(ctx)).status_code == 200
    rows = sheet_values((await workbook(ctx, "BOTH"))["Final Combined R1"])[1:]
    assert len(rows) == 23
    assert all(row[7] == "PENDING" and row[8] == "BLOCKED_CUTOFF_TIE" for row in rows)


@pytest.mark.database
async def test_projector_never_substitutes_admin_preview_for_publication(ctx):
    from app.audience import ACTION, Controls
    from app.models import AdminAction, LeaderboardSnapshot
    from app.question_reveal import FINAL_KIND
    import json

    await seed_combined(ctx)
    assert (await preview(ctx)).json()["ready"]
    async with ctx["local"]() as db:
        db.add(AdminAction(action=ACTION, details=Controls(leaderboard_visible=True,
                                                         preview_upcoming=True).model_dump_json()))
        db.add(LeaderboardSnapshot(round_code="R1B", data_json=json.dumps({
            "kind": FINAL_KIND, "rows": [{"rank": 1, "team_id": 99,
                                           "team_name": "Subround only", "score": 17}]})))
        await db.commit()
    for _ in range(3):
        public = (await ctx["client"].get("/api/leaderboard/final")).json()
        audience = (await ctx["client"].get("/api/public/audience")).json()
        assert not public["published"] and public["data"] == []
        assert audience["round_code"] != "R1"
        assert not audience["leaderboard"] or all(
            "r1a_score" not in row and "r1b_score" not in row for row in audience["leaderboard"]["data"])
    assert (await publish(ctx)).status_code == 200
    audience = (await ctx["client"].get("/api/public/audience")).json()
    assert audience["round_code"] == "R1" and audience["leaderboard"]["published"]
    assert audience["leaderboard"]["data"] == (await ctx["client"].get("/api/leaderboard/final")).json()["data"]


@pytest.mark.database
async def test_top16_and_ordinary_routes_cannot_change_a_published_roster(ctx):
    from app.models import RoundEnrollment, ScoreEvent, Team

    await seed_combined(ctx)
    async with ctx["local"]() as db:
        for number in range(20):
            team = Team(code=f"EXTRA{number:02}", name=f"Extra {number}", login_username=f"extra{number}")
            db.add(team)
            await db.flush()
            db.add_all([RoundEnrollment(team_id=team.id, round_code="R1A"),
                        ScoreEvent(team_id=team.id, round_code="R1B", points=10000 + number, reason="fixture")])
        await db.commit()
    assert (await publish(ctx)).status_code == 200
    path = "/api/admin/leaderboard/combined/qualifiers"
    frozen = (await ctx["client"].get(path, headers=ctx["headers"])).json()
    assert len(frozen["team_ids"]) == 16
    async with ctx["local"]() as db:
        for team in (await db.scalars(select(Team))).all():
            team.name = "Changed " + team.code
            db.add(ScoreEvent(team_id=team.id, round_code="R1A", points=-team.id * 100000, reason="later edit"))
        await db.commit()
    assert (await publish(ctx)).json()["already_published"]
    for method in ("PUT", "PATCH", "DELETE"):
        for target in ("/api/leaderboard/final", "/api/admin/leaderboard/combined", path):
            rejected = await ctx["client"].request(method, target, headers=ctx["headers"], json={"data": []})
            assert rejected.status_code in (404, 405)
    assert (await ctx["client"].get(path, headers=ctx["headers"])).json() == frozen


@pytest.mark.database
async def test_concurrent_final_reads_use_one_snapshot_query_per_request(ctx):
    await seed_combined(ctx)
    assert (await publish(ctx)).status_code == 200
    statements = []
    def record(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    event.listen(ctx["engine"].sync_engine, "before_cursor_execute", record)
    try:
        responses = await asyncio.gather(*[ctx["client"].get("/api/leaderboard/final") for _ in range(100)])
    finally:
        event.remove(ctx["engine"].sync_engine, "before_cursor_execute", record)
    assert all(response.status_code == 200 for response in responses)
    assert len(statements) == 100
    assert all("leaderboard_snapshots" in sql and "JOIN" not in sql and "sum(" not in sql.lower()
               and "FOR UPDATE" not in sql for sql in statements)
