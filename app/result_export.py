"""Consistent, injection-safe Round 1 XLSX result export."""
from __future__ import annotations

import io
import json
from datetime import datetime

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Font
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .models import (Admin, AdminAction, LeaderboardSnapshot, QuestionOption,
                     RoundEnrollment, ScoreEvent, Submission, Team)
from .timing import utc_now

APP_VERSION = "1.0.0"


def _text_cell(ws, value):
    """Force text into XLSX string cells so formula-like input is never executable."""
    cell = WriteOnlyCell(ws, value="" if value is None else str(value))
    cell.data_type = "s"
    return cell


def _row(ws, values, text_columns=()):
    ws.append([_text_cell(ws, value.isoformat() if isinstance(value, datetime) else value)
               if index in text_columns or isinstance(value, datetime) else value
               for index, value in enumerate(values)])


def _sheet(workbook, name, headers):
    ws = workbook.create_sheet(name)
    cells = []
    for header in headers:
        cell = WriteOnlyCell(ws, value=header); cell.font = Font(bold=True); cells.append(cell)
    ws.append(cells)
    return ws


async def build_results_workbook(db: AsyncSession, scope: str) -> tuple[bytes, datetime, str]:
    rounds = ["R1A", "R1B"] if scope == "BOTH" else [scope]
    exported_at = utc_now()
    revision = await db.scalar(text("SELECT version_num FROM alembic_version"))

    teams = (await db.scalars(select(Team).order_by(Team.code))).all()
    totals_rows = (await db.execute(select(
        ScoreEvent.team_id, ScoreEvent.round_code, func.sum(ScoreEvent.points)
    ).where(ScoreEvent.round_code.in_(rounds)).group_by(
        ScoreEvent.team_id, ScoreEvent.round_code))).all()
    totals = {(team_id, code): int(points) for team_id, code, points in totals_rows}

    outcomes = (await db.execute(select(Submission, Team, QuestionOption)
        .join(Team, Team.id == Submission.team_id)
        .outerjoin(QuestionOption, QuestionOption.id == Submission.selected_option_id)
        .where(Submission.round_code.in_(rounds))
        .order_by(Submission.round_code, Submission.question_position, Team.code))).all()
    enrolments = (await db.execute(select(RoundEnrollment, Team)
        .join(Team, Team.id == RoundEnrollment.team_id)
        .where(RoundEnrollment.round_code == "R1A")
        .order_by(Team.code))).all() if "R1A" in rounds else []
    publications = (await db.scalars(select(LeaderboardSnapshot).where(
        LeaderboardSnapshot.round_code.in_(rounds)
    ).order_by(LeaderboardSnapshot.published_at, LeaderboardSnapshot.id))).all()
    audit_rows = (await db.execute(select(AdminAction, Admin.username)
        .outerjoin(Admin, Admin.id == AdminAction.admin_id)
        .order_by(AdminAction.created_at, AdminAction.id))).all()

    wb = Workbook(write_only=True)
    metadata = _sheet(wb, "Export Metadata", ["Field", "Value"])
    for key, value in (
        ("Exported at (UTC)", exported_at.isoformat()),
        ("Timezone", "UTC"), ("Scope", scope),
        ("Schema revision", revision), ("Application version", APP_VERSION),
        ("Score semantics", "Team Results are current calculated ScoreEvent totals; Published Snapshots are immutable historical publications"),
    ):
        _row(metadata, [key, value], text_columns=(0, 1))

    team_headers = ["Team ID", "Team Code", "Team Name"]
    if "R1A" in rounds: team_headers.append("Current R1A Total")
    if "R1B" in rounds: team_headers.append("Current R1B Total")
    if scope == "BOTH": team_headers.append("Current Combined Total")
    team_sheet = _sheet(wb, "Team Results", team_headers)
    for team in teams:
        values = [team.id, team.code, team.name]
        if "R1A" in rounds: values.append(totals.get((team.id, "R1A"), 0))
        if "R1B" in rounds: values.append(totals.get((team.id, "R1B"), 0))
        if scope == "BOTH": values.append(sum(totals.get((team.id, code), 0) for code in rounds))
        _row(team_sheet, values, text_columns=(1, 2))

    outcome_sheet = _sheet(wb, "Question Outcomes", [
        "Submission ID", "Team ID", "Team Code", "Team Name", "Round",
        "Question / Turn", "Relay Player", "Submitted Input (Exact Text)",
        "Selected Option ID", "Selected Option Position", "Selected Option Text",
        "Result", "Points", "Elapsed ms", "Player Submitted At", "Effective Timeout At",
        "Recorded At", "Finalization Source", "Draft ID", "Draft Version",
    ])
    for submission, team, option in outcomes:
        exact_input = (submission.answer_text if submission.round_code == "R1B"
                       else option.option_text if option else None)
        _row(outcome_sheet, [submission.id, team.id, team.code, team.name,
             submission.round_code, submission.question_position, submission.player_number,
             exact_input, submission.selected_option_id, option.position if option else None,
             option.option_text if option else None, submission.result, submission.points,
             submission.elapsed_ms, submission.submitted_at, submission.timed_out_at,
             submission.recorded_at, submission.finalization_source,
             submission.draft_id, submission.draft_version],
             text_columns=(2, 3, 4, 7, 10, 11, 17))

    roster = _sheet(wb, "R1A Enrolment", ["Enrolment ID", "Team ID", "Team Code", "Team Name", "Enrolled At"])
    for enrollment, team in enrolments:
        _row(roster, [enrollment.id, team.id, team.code, team.name, enrollment.enrolled_at],
             text_columns=(2, 3))

    published = _sheet(wb, "Published Snapshots", [
        "Publication ID", "Published At", "Round", "Rank", "Team ID",
        "Team Code", "Team Name", "Published Score",
    ])
    for snapshot in publications:
        for item in json.loads(snapshot.data_json):
            _row(published, [snapshot.id, snapshot.published_at, snapshot.round_code,
                 item.get("rank"), item.get("team_id"), item.get("team_code"),
                 item.get("team_name"), item.get("score")], text_columns=(2, 5, 6))

    audit = _sheet(wb, "Admin Audit", [
        "Audit ID", "Timestamp", "Admin ID", "Admin Username", "Action", "Target / Details",
    ])
    for action, username in audit_rows:
        _row(audit, [action.id, action.created_at, action.admin_id, username,
                     action.action, action.details], text_columns=(3, 4, 5))

    output = io.BytesIO(); wb.save(output)
    return output.getvalue(), exported_at, revision
