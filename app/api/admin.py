from datetime import timedelta
from decimal import Decimal, InvalidOperation
from io import BytesIO
import json
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import and_, delete, func, literal, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from ..db import get_db
from ..dependencies import require_admin
from ..models import *
from ..security import hash_password_async
from ..services import LeaderboardService, StorageService
from ..timing import utc_now
from ..config import get_settings
from .auth import new_activation_token
from ..round_locking import lock_round
from ..r1a_reconciliation import reconcile_r1a
from ..r1b_finalization import effective_remaining_ms, finalize_expired_team
from ..round_validation import validate_round_configuration
from ..result_export import build_results_workbook
from ..competition_rules import configured_rules, round_rules

router = APIRouter(prefix="/api/admin", tags=["admin"])
settings = get_settings()

class TeamCreate(BaseModel):
    code: str
    name: str
    username: str
    password: str | None = Field(default=None, min_length=8, max_length=128)

class TeamUpdate(BaseModel):
    code: str | None = Field(default=None, min_length=1, max_length=32)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    username: str | None = Field(default=None, min_length=1, max_length=120)
    enabled: bool | None = None

class PasswordResetIn(BaseModel):
    new_password: str = Field(min_length=8, max_length=128)

class ActivationRegenerateIn(BaseModel):
    reason: str = Field(min_length=1, max_length=500)

class ValidationIssueOut(BaseModel):
    code: str
    field: str
    message: str

class ValidationOut(BaseModel):
    round_code: str
    valid: bool
    issues: list[ValidationIssueOut]

class RoundAction(BaseModel):
    round_code: str

class R1BTimerConfig(BaseModel):
    timer_mode: R1BTimerMode

class QuestionCreate(BaseModel):
    round_code: str
    position: int = Field(ge=1, le=100)
    question_text: str | None = None
    answer_type: str
    correct_option_id: int | None = None
    correct_numeric: str | None = None
    tolerance_abs: str = "0"
    tolerance_rel: str = "0"
    enabled: bool = True

class QuestionUpdate(BaseModel):
    position: int | None = Field(default=None, ge=1)
    question_text: str | None = None
    question_image_url: str | None = None
    correct_option_id: int | None = None
    correct_numeric: str | None = None
    tolerance_abs: str | None = None
    tolerance_rel: str | None = None
    enabled: bool | None = None

class RevokeSessionsIn(BaseModel):
    reason: str = Field(min_length=1, max_length=500)

class ResetRoundOneIn(BaseModel):
    reason: str = Field(min_length=1, max_length=500)
    confirmation: str

async def acting_admin_id(db, user):
    return await db.scalar(select(Admin.id).where(Admin.username == user.get("sub")))

def admin_error(status: int, code: str, message: str):
    return HTTPException(status, {"code": code, "message": message})

async def audit(db, user, action: str, details: str):
    db.add(AdminAction(admin_id=await acting_admin_id(db, user), action=action, details=details))

def decimal_value(value: str | None, field: str, *, nonnegative: bool = False):
    if value is None:
        return None
    try:
        parsed = Decimal(value.strip())
    except (InvalidOperation, AttributeError):
        raise admin_error(422, "INVALID_NUMERIC", f"{field} must be a finite decimal")
    if not parsed.is_finite() or (nonnegative and parsed < 0):
        qualifier = "finite nonnegative" if nonnegative else "finite"
        raise admin_error(422, "INVALID_NUMERIC", f"{field} must be a {qualifier} decimal")
    return parsed

async def lock_draft_round(db, round_code: str):
    r = await lock_round(db, round_code, exclusive=True)
    if not r:
        raise admin_error(404, "ROUND_NOT_FOUND", "Round not found")
    if r.status != RoundStatus.DRAFT.value or r.started_at is not None:
        raise admin_error(409, "ROUND_CONTENT_LOCKED", "Round content is locked after start")
    return r

async def reconcile_due_r1b_locked(db, round_obj, now):
    reference = round_obj.paused_at if round_obj.status == RoundStatus.PAUSED.value else now
    states = (await db.scalars(select(TeamRoundState).where(
        TeamRoundState.round_code == "R1B",
        TeamRoundState.turn_status.in_(("ACTIVE", "WAITING")),
        TeamRoundState.clock_running_since.is_not(None),
        TeamRoundState.team_deadline.is_not(None),
        TeamRoundState.team_deadline <= reference,
    ).with_for_update())).all()
    if round_obj.status == RoundStatus.PAUSED.value:
        original = round_obj.status
        round_obj.status = RoundStatus.LIVE.value
        try:
            for state in states:
                await finalize_expired_team(db, round_obj, state, reference)
        finally:
            round_obj.status = original
    else:
        for state in states:
            await finalize_expired_team(db, round_obj, state, now)
    return len(states)

@router.post("/bootstrap")
async def bootstrap(username: str="admin", password: str="change-me-now", db: AsyncSession=Depends(get_db)):
    if settings.environment != "development":
        raise HTTPException(404)
    exists = (await db.execute(select(Admin).where(Admin.username==username))).scalar_one_or_none()
    if exists: raise HTTPException(409, "Admin already exists")
    db.add(Admin(username=username, password_hash=await hash_password_async(password)))
    await db.commit()
    return {"username": username, "password": password}

@router.post("/teams")
async def create_team(data: TeamCreate, user=Depends(require_admin), db: AsyncSession=Depends(get_db)):
    activation_token = None
    activation_hash = None
    activation_expires = None
    if data.password is None:
        activation_token, activation_hash = new_activation_token()
        activation_expires = utc_now() + timedelta(hours=settings.activation_token_hours)
    team = Team(code=data.code, name=data.name, login_username=data.username,
                password_hash=await hash_password_async(data.password) if data.password else None,
                activation_token_hash=activation_hash, activation_expires_at=activation_expires)
    db.add(team); await db.flush()
    await audit(db, user, "CREATE_TEAM", f"team_id={team.id};code={team.code}")
    await db.commit(); await db.refresh(team)
    return {"id": team.id, "code": team.code, "name": team.name,
            "username": team.login_username, "activation_token": activation_token,
            "activation_expires_at": activation_expires}

@router.get("/rounds/{round_code}/validate", response_model=ValidationOut)
async def validate_round(round_code: str, user=Depends(require_admin), db: AsyncSession=Depends(get_db)):
    if round_code not in ("R1A", "R1B"):
        raise admin_error(400, "ROUND_CODE_INVALID", "Unsupported round code")
    r = await db.scalar(select(Round).where(Round.code == round_code))
    if not r:
        raise admin_error(404, "ROUND_NOT_FOUND", "Round not found")
    issues = await validate_round_configuration(db, r)
    return {"round_code": round_code, "valid": not issues, "issues": issues}


@router.post("/rounds/start")
async def start_round(data: RoundAction, user=Depends(require_admin), db: AsyncSession=Depends(get_db)):
    if data.round_code not in ("R1A", "R1B"):
        raise admin_error(400, "ROUND_CODE_INVALID", "Unsupported round code")
    r = await lock_round(db, data.round_code, exclusive=True)
    if not r:
        raise HTTPException(404, "Round not found")
    if r.status != RoundStatus.DRAFT.value:
        raise HTTPException(409, "A round can only be started once")
    issues = await validate_round_configuration(db, r)
    if issues:
        raise HTTPException(409, {"code": "ROUND_CONFIGURATION_INVALID",
                                  "message": "Round configuration is invalid", "issues": issues})
    effective_rules = configured_rules()
    if r.code == "R1B" and not r.r1b_timer_mode_selected:
        r.r1b_timer_mode = effective_rules.r1b.default_timer_mode
    r.rules_version = effective_rules.version
    r.rules_snapshot = effective_rules.snapshot_json()
    r.status = RoundStatus.LIVE.value
    r.started_at = utc_now()
    if r.code == "R1A":
        # Eligibility is fixed at the instant the round starts. Session presence
        # and later Team.enabled changes deliberately have no effect on it.
        await db.execute(insert(RoundEnrollment).from_select(
            ["team_id", "round_code", "enrolled_at"],
            select(Team.id, literal("R1A"), literal(r.started_at)).where(Team.enabled.is_(True))
        ).on_conflict_do_nothing(constraint="uq_round_enrollment"))
        r.current_position = 1
        r.question_started_at = r.started_at
    r.ended_at = None
    await audit(db, user, "START_ROUND", f"round={r.code}")
    await db.commit()
    return {"round_code": r.code, "status": r.status, "started_at": r.started_at}


@router.get("/r1b/timer")
async def get_r1b_timer(user=Depends(require_admin), db: AsyncSession=Depends(get_db)):
    r = await lock_round(db, "R1B", exclusive=False)
    if not r:
        raise HTTPException(404, "Round not found")
    return {
        "round_code": r.code,
        "timer_mode": r.r1b_timer_mode,
        "status": r.status,
        "locked": r.status != RoundStatus.DRAFT.value or r.started_at is not None,
    }


@router.put("/r1b/timer")
async def set_r1b_timer(data: R1BTimerConfig, user=Depends(require_admin),
                        db: AsyncSession=Depends(get_db)):
    r = await lock_round(db, "R1B", exclusive=True)
    if not r:
        raise HTTPException(404, "Round not found")
    if r.status != RoundStatus.DRAFT.value or r.started_at is not None:
        raise HTTPException(409, "R1B timer mode is locked after the round starts")
    r.r1b_timer_mode = data.timer_mode.value
    r.r1b_timer_mode_selected = True
    await audit(db, user, "SET_R1B_TIMER", f"mode={r.r1b_timer_mode}")
    await db.commit()
    return {
        "round_code": r.code,
        "timer_mode": r.r1b_timer_mode,
        "status": r.status,
        "locked": False,
    }


@router.post("/rounds/pause")
async def pause_round(data: RoundAction, user=Depends(require_admin), db: AsyncSession=Depends(get_db)):
    r = await lock_round(db, data.round_code, exclusive=True)
    if not r:
        raise HTTPException(404, "Round not found")
    if r.code == "R1A" and r.status == RoundStatus.LIVE.value:
        await reconcile_r1a(db, locked_round=r)
    if r.code == "R1B" and r.status == RoundStatus.LIVE.value:
        await reconcile_due_r1b_locked(db, r, utc_now())
    if r.status != RoundStatus.LIVE.value:
        await db.commit()
        raise HTTPException(409, "Only a live round can be paused")
    now = utc_now()
    r.status = RoundStatus.PAUSED.value
    r.paused_at = now
    await audit(db, user, "PAUSE_ROUND", f"round={r.code}")
    await db.commit()
    return {"round_code": r.code, "status": r.status, "paused_at": r.paused_at}


@router.post("/rounds/resume")
async def resume_round(data: RoundAction, user=Depends(require_admin), db: AsyncSession=Depends(get_db)):
    r = await lock_round(db, data.round_code, exclusive=True)
    if not r:
        raise HTTPException(404, "Round not found")
    if r.status != RoundStatus.PAUSED.value or not r.paused_at:
        raise HTTPException(409, "Only a paused round can be resumed")
    now = utc_now()
    paused_for = now - r.paused_at
    if r.code == "R1A" and r.r1a_result_due_at:
        r.r1a_result_due_at += paused_for
    # Shift R1A only if it was still active when pause acquired its lock.
    # A pause after the deadline must not resurrect an expired question.
    if (r.code == "R1A" and r.question_started_at
            and r.paused_at < r.question_started_at + timedelta(
                milliseconds=round_rules(r).r1a.question_duration_ms)):
        r.question_started_at += paused_for
    if r.code == "R1B":
        states = (await db.scalars(select(TeamRoundState).where(
            TeamRoundState.round_code == "R1B",
            TeamRoundState.turn_status.not_in(("FINISHED", "EXPIRED"))
        ).with_for_update())).all()
        for state in states:
            if state.clock_running_since:
                state.clock_running_since += paused_for
            if state.team_deadline:
                state.team_deadline += paused_for
            if state.ready_at:
                state.ready_at += paused_for
    r.status = RoundStatus.LIVE.value
    r.paused_at = None
    await audit(db, user, "RESUME_ROUND", f"round={r.code}")
    await db.commit()
    return {"round_code": r.code, "status": r.status, "resumed_at": now}


class NextQuestion(BaseModel):
    expected_position: int = Field(ge=0)


@router.post("/r1a/next")
async def next_question(data: NextQuestion, user=Depends(require_admin), db: AsyncSession=Depends(get_db)):
    r = await lock_round(db, "R1A", exclusive=True)
    if not r:
        raise HTTPException(409, "Start Round 1(a) first")
    now = utc_now()
    if round_rules(r).r1a.advance_mode == "AUTO":
        raise HTTPException(409, "Next Question is disabled while R1A automatic advancement is enabled")
    if r.status == RoundStatus.LIVE.value:
        await reconcile_r1a(db, now=now, locked_round=r)
    if r.status != RoundStatus.LIVE.value:
        await db.commit()
        raise HTTPException(409, "Start Round 1(a) first")
    if (r.current_position or 0) != data.expected_position:
        raise HTTPException(409, "Another admin advanced the question. Refresh the dashboard.")
    rules = round_rules(r).r1a
    if r.current_position == rules.question_count:
        await db.commit()
        raise HTTPException(409, f"All {rules.question_count} questions have been revealed")
    if r.question_started_at and now < r.question_started_at + timedelta(milliseconds=rules.question_duration_ms):
        await db.commit()
        raise HTTPException(409, "Wait for the current question timer to finish")
    position = (r.current_position or 0) + 1
    q = await db.scalar(select(Question).where(Question.round_code == "R1A",
                       Question.position == position, Question.enabled.is_(True)))
    if not q or q.correct_option_id is None:
        raise HTTPException(409, "Next question is not configured")
    r.current_position = position
    r.question_started_at = now
    await audit(db, user, "REVEAL_R1A_QUESTION", f"position={position}")
    await db.commit()
    return {"position": position, "question_deadline": now + timedelta(milliseconds=rules.question_duration_ms)}


@router.get("/dashboard")
async def dashboard(r1b_offset: int = Query(0, ge=0), r1b_limit: int = Query(100, ge=1, le=500),
                    user=Depends(require_admin), db: AsyncSession=Depends(get_db)):
    from .r1a import position_at
    now = utc_now()
    rounds = (await db.scalars(select(Round).order_by(Round.code))).all()
    by_code = {r.code: r for r in rounds}
    r1a = by_code.get("R1A")
    r1a_progress = {"accepted": 0, "unanswered": 0, "finalized": 0}
    if r1a and r1a.current_position:
        counts = (await db.execute(select(Submission.result, func.count(Submission.id))
                  .where(Submission.round_code == "R1A",
                         Submission.question_position == r1a.current_position)
                  .group_by(Submission.result))).all()
        result_counts = dict(counts)
        r1a_progress = {
            "accepted": sum(count for result, count in counts if result != "UNANSWERED"),
            "unanswered": result_counts.get("UNANSWERED", 0),
            "finalized": sum(result_counts.values()),
        }
    active_sessions = (select(PlayerDevice.team_id.label("team_id"),
                              func.count(PlayerDevice.id).label("session_count"),
                              func.max(PlayerDevice.last_seen_at).label("last_seen_at"))
                       .where(PlayerDevice.active.is_(True), PlayerDevice.expires_at > now,
                              PlayerDevice.round_code == "R1B")
                       .group_by(PlayerDevice.team_id).subquery())
    total_r1b = await db.scalar(select(func.count()).select_from(Team).where(Team.enabled.is_(True)))
    state_rows = (await db.execute(select(TeamRoundState, Team, active_sessions.c.session_count,
                                          active_sessions.c.last_seen_at)
                 .select_from(Team)
                 .outerjoin(TeamRoundState, and_(TeamRoundState.team_id == Team.id,
                                                TeamRoundState.round_code == "R1B"))
                 .outerjoin(active_sessions, active_sessions.c.team_id == Team.id)
                 .where(Team.enabled.is_(True))
                 .order_by(Team.code).offset(r1b_offset).limit(r1b_limit))).all()
    r1b_round = by_code.get("R1B")
    reference = (r1b_round.paused_at if r1b_round and r1b_round.status == "PAUSED"
                 and r1b_round.paused_at else now)
    return {"server_now": now, "environment": settings.environment, "presence_semantics":
            "Session presence is a server lease and does not prove a browser is physically open or closed",
            "r1a_progress": r1a_progress, "rounds": [
        {"code": r.code, "status": r.status, "position": r.current_position,
         "timer_mode": r.r1b_timer_mode if r.code == "R1B" else None,
         "question_active": position_at(r, now) is not None if r.code == "R1A" else False,
         "question_deadline": r.question_started_at + timedelta(milliseconds=round_rules(r).r1a.question_duration_ms) if r.question_started_at else None,
         "r1a_phase": ("RESULT" if r.code == "R1A" and r.r1a_result_due_at else
                       "QUESTION" if r.code == "R1A" and position_at(r, now) is not None else None),
         "next_reveal_at": r.r1a_result_due_at if r.code == "R1A" else None,
         "rules": round_rules(r).public_dict(),
         "paused_at": r.paused_at}
        for r in rounds],
        "r1b_teams": {"total": total_r1b or 0, "offset": r1b_offset, "limit": r1b_limit,
          "items": [{"team_id": team.id, "team_code": team.code, "team_name": team.name,
                     "current_player": state.current_player if state else 1,
                     "current_question_position": state.current_question_position if state else 1,
                     "turn_status": state.turn_status if state else "NOT_STARTED",
                     "timer_running": bool(r1b_round and r1b_round.status == "LIVE"
                                           and state and state.clock_running_since
                                           and state.turn_status not in ("FINISHED", "EXPIRED")),
                     "timer_frozen": bool(not state or not state.clock_running_since or
                                          (r1b_round and r1b_round.status == "PAUSED")),
                     "remaining_ms": effective_remaining_ms(state, reference) if state else round_rules(r1b_round).r1b.total_answering_budget_ms,
                     "deadline": state.team_deadline if state and state.clock_running_since else None,
                     "finished_at": state.finished_at if state else None,
                     "expired_at": state.expired_at if state else None,
                     "active_session_count": session_count or 0, "last_seen_at": last_seen}
                    for state, team, session_count, last_seen in state_rows]}}


@router.get("/submissions")
async def submissions(round_code: str | None = Query(None, pattern="^R1[AB]$"),
                      position: int | None = Query(None, ge=1),
                      team_id: int | None = Query(None, ge=1),
                      offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500),
                      user=Depends(require_admin), db: AsyncSession=Depends(get_db)):
    from sqlalchemy import func
    filters = []
    if round_code is not None:
        filters.append(Submission.round_code == round_code)
    if position is not None:
        filters.append(Submission.question_position == position)
    if team_id is not None:
        filters.append(Submission.team_id == team_id)
    total = await db.scalar(select(func.count()).select_from(Submission).where(*filters))
    rows = (await db.execute(select(Submission, Team, QuestionOption)
        .join(Team, Team.id == Submission.team_id)
        .outerjoin(QuestionOption, QuestionOption.id == Submission.selected_option_id)
        .where(*filters).order_by(Submission.submitted_at.desc(), Submission.id.desc())
        .offset(offset).limit(limit))).all()
    return {"total": total, "offset": offset, "limit": limit, "items": [
        {"id": s.id, "team_id": t.id, "team_code": t.code, "team_name": t.name,
         "round_code": s.round_code, "position": s.question_position,
         "player_number": s.player_number, "answer": s.answer_text,
         "option_id": s.selected_option_id, "option_position": o.position if o else None,
         "option_text": o.option_text if o else None, "option_image_url": o.image_url if o else None,
         "submitted_at": s.submitted_at, "elapsed_ms": s.elapsed_ms,
         "timed_out_at": s.timed_out_at, "recorded_at": s.recorded_at,
         "finalization_source": s.finalization_source,
         "draft_id": s.draft_id, "draft_version": s.draft_version,
         "result": s.result, "points": s.points}
        for s, t, o in rows]}


@router.get("/teams")
async def list_teams(offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500),
                     user=Depends(require_admin), db: AsyncSession=Depends(get_db)):
    """Return the team directory with live activity and scoring totals."""
    from sqlalchemy import func
    now = utc_now()
    score = (select(Submission.team_id.label("team_id"),
                    func.count(Submission.id).label("submission_count"),
                    func.coalesce(func.sum(Submission.points), 0).label("points"),
                    func.max(Submission.submitted_at).label("last_submission_at"))
             .group_by(Submission.team_id).subquery())
    presence = (select(PlayerDevice.team_id.label("team_id"),
                       func.count(PlayerDevice.id).label("active_count"),
                       func.max(PlayerDevice.last_seen_at).label("last_seen_at"))
                .where(PlayerDevice.active.is_(True), PlayerDevice.expires_at > now)
                .group_by(PlayerDevice.team_id).subquery())
    total = await db.scalar(select(func.count()).select_from(Team))
    rows = (await db.execute(select(Team, score.c.submission_count, score.c.points,
                                    score.c.last_submission_at, presence.c.active_count,
                                    presence.c.last_seen_at)
             .outerjoin(score, score.c.team_id == Team.id)
             .outerjoin(presence, presence.c.team_id == Team.id)
             .order_by(Team.code).offset(offset).limit(limit))).all()
    return {"total": total, "offset": offset, "limit": limit, "items": [
        {"id": team.id, "code": team.code, "name": team.name,
         "username": team.login_username, "enabled": team.enabled,
         "submission_count": count or 0, "points": points or 0,
         "last_submission_at": last_submission,
         "session_active": bool(active_count),
         "active_session_count": active_count or 0,
         "last_seen_at": last_seen,
         "presence_basis": "server lease; not proof the browser is physically open"}
        for team, count, points, last_submission, active_count, last_seen in rows
    ]}


@router.get("/simulator/inspect")
async def inspect_simulator_scope(user=Depends(require_admin), db: AsyncSession=Depends(get_db)):
    """Read-only facts needed by the local, development-only player launcher."""
    if settings.environment != "development":
        raise HTTPException(404, "Player simulator is available only in development")
    rounds = (await db.scalars(select(Round).where(
        Round.code.in_(("R1A", "R1B"))).order_by(Round.code))).all()
    by_code = {row.code: row for row in rounds}
    r1a, r1b = by_code.get("R1A"), by_code.get("R1B")
    login_scope = None
    if r1b and r1b.status in (RoundStatus.LIVE.value, RoundStatus.PAUSED.value):
        login_scope = "R1B"
    elif r1a and r1a.status != RoundStatus.ENDED.value:
        login_scope = "R1A"
    elif r1b and r1b.status != RoundStatus.ENDED.value:
        login_scope = "R1B"
    return {
        "environment": settings.environment,
        "login_scope": login_scope,
        "rounds": {row.code: {"status": row.status, "started_at": row.started_at}
                   for row in rounds},
    }


@router.get("/teams/{team_id}")
async def get_team(team_id: int, user=Depends(require_admin), db: AsyncSession=Depends(get_db)):
    team = await db.scalar(select(Team).where(Team.id == team_id))
    if not team:
        raise admin_error(404, "TEAM_NOT_FOUND", "Team not found")
    enrolled = (await db.scalars(select(RoundEnrollment.round_code).where(
        RoundEnrollment.team_id == team_id))).all()
    return {"id": team.id, "code": team.code, "name": team.name,
            "username": team.login_username, "enabled": team.enabled,
            "created_at": team.created_at, "enrolled_rounds": enrolled,
            "has_password": team.password_hash is not None}


@router.post("/teams/{team_id}/activation/regenerate")
async def regenerate_team_activation(team_id: int, data: ActivationRegenerateIn,
                                     user=Depends(require_admin), db: AsyncSession=Depends(get_db)):
    """Replace an unconsumed activation code; plaintext exists only in this response."""
    async with db.begin():
        team = await db.scalar(select(Team).where(Team.id == team_id).with_for_update())
        if not team:
            raise admin_error(404, "TEAM_NOT_FOUND", "Team not found")
        if team.password_hash is not None:
            raise admin_error(409, "TEAM_ALREADY_ACTIVATED", "Activated teams use password reset")
        activation_code, digest = new_activation_token()
        expires = utc_now() + timedelta(hours=settings.activation_token_hours)
        team.activation_token_hash = digest
        team.activation_expires_at = expires
        await audit(db, user, "REGENERATE_TEAM_ACTIVATION",
                    f"team_id={team.id}")
    return {"team_id": team.id, "username": team.login_username,
            "activation_token": activation_code, "activation_expires_at": expires}


@router.put("/teams/{team_id}")
async def update_team(team_id: int, data: TeamUpdate, user=Depends(require_admin),
                      db: AsyncSession=Depends(get_db)):
    rounds = (await db.scalars(select(Round).order_by(Round.code).with_for_update())).all()
    team = await db.scalar(select(Team).where(Team.id == team_id).with_for_update())
    if not team:
        raise admin_error(404, "TEAM_NOT_FOUND", "Team not found")
    values = data.model_fields_set
    if "enabled" in values and data.enabled != team.enabled:
        if any(r.started_at is not None for r in rounds):
            raise admin_error(409, "TEAM_ENROLMENT_LOCKED",
                              "Enable/disable is locked after competition enrolment starts")
        team.enabled = data.enabled
    for field, target in (("code", "code"), ("name", "name"), ("username", "login_username")):
        if field in values:
            value = getattr(data, field)
            if value is None:
                raise admin_error(422, "TEAM_FIELD_REQUIRED", f"{field} cannot be null")
            setattr(team, target, value)
    await audit(db, user, "UPDATE_TEAM", f"team_id={team.id};fields={','.join(sorted(values))}")
    await db.commit()
    return {"id": team.id, "code": team.code, "name": team.name,
            "username": team.login_username, "enabled": team.enabled}


@router.post("/teams/{team_id}/password-reset")
async def reset_team_password(team_id: int, data: PasswordResetIn,
                              user=Depends(require_admin), db: AsyncSession=Depends(get_db)):
    new_hash = await hash_password_async(data.new_password)
    await db.execute(select(Round).order_by(Round.code).with_for_update())
    team = await db.scalar(select(Team).where(Team.id == team_id).with_for_update())
    if not team:
        raise admin_error(404, "TEAM_NOT_FOUND", "Team not found")
    sessions = (await db.scalars(select(PlayerDevice).where(
        PlayerDevice.team_id == team_id).with_for_update())).all()
    now = utc_now(); admin_id = await acting_admin_id(db, user); revoked = 0
    team.password_hash = new_hash
    team.activation_token_hash = None
    team.activation_expires_at = None
    for session in sessions:
        if session.active:
            session.active = False; session.revoked_at = now
            session.revoked_by_admin_id = admin_id
            session.revocation_reason = "Password reset by admin"
            revoked += 1
    db.add(AdminAction(admin_id=admin_id, action="RESET_TEAM_PASSWORD",
                       details=f"team_id={team_id};revoked_sessions={revoked}"))
    await db.commit()
    return {"ok": True, "team_id": team_id, "revoked_sessions": revoked}

@router.post("/rounds/end")
async def end_round(data: RoundAction, user=Depends(require_admin), db: AsyncSession=Depends(get_db)):
    if data.round_code not in ("R1A", "R1B"):
        raise admin_error(400, "ROUND_CODE_INVALID", "Unsupported round code")
    r = await lock_round(db, data.round_code, exclusive=True)
    if not r:
        raise HTTPException(404, "Round not found")
    if r.code == "R1A" and r.status == RoundStatus.LIVE.value:
        await reconcile_r1a(db, locked_round=r)
    now = utc_now()
    if r.code == "R1B" and r.status in (RoundStatus.LIVE.value, RoundStatus.PAUSED.value):
        await reconcile_due_r1b_locked(db, r, now)
    if r.status not in (RoundStatus.LIVE.value, RoundStatus.PAUSED.value):
        await db.commit()
        raise HTTPException(409, "Round is not live")
    r.status = RoundStatus.ENDED.value
    r.ended_at = now
    if r.code == "R1B":
        reference = r.paused_at or now
        unfinished = (await db.scalars(select(TeamRoundState).where(
            TeamRoundState.round_code == "R1B",
            TeamRoundState.turn_status.not_in(("FINISHED", "EXPIRED")),
        ).with_for_update())).all()
        for state in unfinished:
            state.remaining_time_ms = effective_remaining_ms(state, reference)
            state.clock_running_since = None
            state.team_deadline = None
    r.paused_at = None
    await audit(db, user, "END_ROUND", f"round={r.code}")
    await db.commit()
    return {"round_code": r.code, "status": r.status, "ended_at": r.ended_at}

@router.post("/leaderboard/publish")
async def publish(data: RoundAction, user=Depends(require_admin), db: AsyncSession=Depends(get_db)):
    if data.round_code not in ("R1A", "R1B"):
        raise admin_error(400, "ROUND_CODE_INVALID", "Unsupported round code")
    r = await lock_round(db, data.round_code, exclusive=True)
    if not r:
        raise admin_error(404, "ROUND_NOT_FOUND", "Round not found")
    service = LeaderboardService()
    snap, data_out = await service.publish(db, data.round_code)
    r.leaderboard_published_at = snap.published_at
    await audit(db, user, "PUBLISH_LEADERBOARD",
                f"round={data.round_code};publication_id={snap.id}")
    await db.commit()
    return {"published": True, "publication_id": snap.id,
            "published_at": snap.published_at,
            "scope": {"round_code": data.round_code, "immutable": True},
            "data": data_out}

@router.post("/questions")
async def create_question(data: QuestionCreate, user=Depends(require_admin), db: AsyncSession=Depends(get_db)):
    round_code = data.round_code.upper()
    answer_type = data.answer_type.upper()
    if (round_code, answer_type) not in (("R1A", "MCQ"), ("R1B", "NUMERIC")):
        raise admin_error(422, "ANSWER_TYPE_INVALID", "R1A questions must be MCQ; R1B questions must be NUMERIC")
    rules = configured_rules()
    max_position = rules.r1a.question_count if round_code == "R1A" else rules.r1b.turn_count
    if data.position > max_position:
        raise admin_error(422, "POSITION_INVALID", f"{round_code} position must be 1..{max_position}")
    await lock_draft_round(db, round_code)
    if round_code == "R1B" and data.correct_numeric is None:
        raise admin_error(422, "INVALID_NUMERIC", "correct_numeric must be a finite decimal")
    correct_numeric = decimal_value(data.correct_numeric, "correct_numeric") if round_code == "R1B" else None
    tolerance_abs = decimal_value(data.tolerance_abs, "tolerance_abs", nonnegative=True)
    tolerance_rel = decimal_value(data.tolerance_rel, "tolerance_rel", nonnegative=True)
    if round_code == "R1A" and data.correct_option_id is not None:
        raise admin_error(422, "CORRECT_OPTION_OWNERSHIP",
                          "Create options first, then select a correct option on update")
    q = Question(round_code=round_code, position=data.position, question_text=data.question_text,
                 answer_type=answer_type, correct_option_id=data.correct_option_id,
                 correct_numeric=correct_numeric, tolerance_abs=tolerance_abs,
                 tolerance_rel=tolerance_rel, enabled=data.enabled)
    db.add(q); await db.flush()
    await audit(db, user, "CREATE_QUESTION", f"question_id={q.id};round={round_code};position={q.position}")
    await db.commit(); await db.refresh(q)
    return {"id": q.id}


class OptionCreate(BaseModel):
    position: int = Field(ge=1, le=20)
    option_text: str | None = None
    is_correct: bool = False

class OptionUpdate(BaseModel):
    position: int | None = Field(default=None, ge=1, le=20)
    option_text: str | None = None
    image_url: str | None = None
    is_correct: bool | None = None

@router.post("/questions/{question_id}/options")
async def create_option(question_id: int, data: OptionCreate, user=Depends(require_admin), db: AsyncSession=Depends(get_db)):
    round_code = await db.scalar(select(Question.round_code).where(Question.id == question_id))
    await db.rollback()
    if not round_code:
        raise admin_error(404, "QUESTION_NOT_FOUND", "Question not found")
    await lock_draft_round(db, round_code)
    q = (await db.execute(select(Question).where(Question.id == question_id).with_for_update())).scalar_one_or_none()
    if not q:
        raise admin_error(404, "QUESTION_NOT_FOUND", "Question not found")
    opt = QuestionOption(question_id=question_id, position=data.position, option_text=data.option_text)
    db.add(opt)
    await db.flush()
    if data.is_correct:
        if q.round_code != "R1A":
            raise admin_error(422, "OPTION_TYPE_INVALID", "Only R1A MCQ options can be marked correct")
        q.correct_option_id = opt.id
    await audit(db, user, "CREATE_OPTION", f"option_id={opt.id};question_id={q.id};position={opt.position}")
    await db.commit()
    return {"id": opt.id, "position": opt.position}


def question_json(q, options=None):
    return {"id": q.id, "round_code": q.round_code, "position": q.position,
            "question_text": q.question_text, "question_image_url": q.question_image_url,
            "answer_type": q.answer_type, "correct_option_id": q.correct_option_id,
            "correct_numeric": str(q.correct_numeric) if q.correct_numeric is not None else None,
            "tolerance_abs": str(q.tolerance_abs), "tolerance_rel": str(q.tolerance_rel),
            "enabled": q.enabled, "options": options}


def option_json(o):
    return {"id": o.id, "question_id": o.question_id, "position": o.position,
            "option_text": o.option_text, "image_url": o.image_url}


@router.get("/questions")
async def list_questions(round_code: str | None = Query(None, pattern="^R1[AB]$"),
                         offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=200),
                         user=Depends(require_admin), db: AsyncSession=Depends(get_db)):
    filters = [Question.round_code == round_code] if round_code else []
    total = await db.scalar(select(func.count()).select_from(Question).where(*filters))
    rows = (await db.scalars(select(Question).where(*filters)
            .order_by(Question.round_code, Question.position).offset(offset).limit(limit))).all()
    return {"total": total, "offset": offset, "limit": limit,
            "items": [question_json(q) for q in rows]}


@router.get("/questions/{question_id}")
async def get_question(question_id: int, user=Depends(require_admin), db: AsyncSession=Depends(get_db)):
    q = await db.scalar(select(Question).where(Question.id == question_id))
    if not q:
        raise admin_error(404, "QUESTION_NOT_FOUND", "Question not found")
    options = (await db.scalars(select(QuestionOption).where(
        QuestionOption.question_id == q.id).order_by(QuestionOption.position))).all()
    return question_json(q, [option_json(o) for o in options])


@router.put("/questions/{question_id}")
async def update_question(question_id: int, data: QuestionUpdate,
                          user=Depends(require_admin), db: AsyncSession=Depends(get_db)):
    round_code = await db.scalar(select(Question.round_code).where(Question.id == question_id))
    await db.rollback()
    if not round_code:
        raise admin_error(404, "QUESTION_NOT_FOUND", "Question not found")
    await lock_draft_round(db, round_code)
    q = await db.scalar(select(Question).where(Question.id == question_id).with_for_update())
    values = data.model_fields_set
    if "position" in values:
        rules = configured_rules()
        max_position = rules.r1a.question_count if round_code == "R1A" else rules.r1b.turn_count
        if data.position is None or data.position > max_position:
            raise admin_error(422, "POSITION_INVALID", f"{round_code} position must be 1..{max_position}")
        q.position = data.position
    for field in ("question_text", "question_image_url", "enabled"):
        if field in values:
            setattr(q, field, getattr(data, field))
    if round_code == "R1A" and "correct_option_id" in values:
        if data.correct_option_id is not None:
            owner = await db.scalar(select(QuestionOption.question_id).where(
                QuestionOption.id == data.correct_option_id))
            if owner != q.id:
                raise admin_error(422, "CORRECT_OPTION_OWNERSHIP",
                                  "Correct option must belong to this question")
        q.correct_option_id = data.correct_option_id
    if round_code == "R1B":
        if "correct_numeric" in values:
            if data.correct_numeric is None:
                raise admin_error(422, "INVALID_NUMERIC", "correct_numeric must be a finite decimal")
            q.correct_numeric = decimal_value(data.correct_numeric, "correct_numeric")
        if "tolerance_abs" in values:
            if data.tolerance_abs is None:
                raise admin_error(422, "INVALID_NUMERIC", "tolerance_abs must be a finite nonnegative decimal")
            q.tolerance_abs = decimal_value(data.tolerance_abs, "tolerance_abs", nonnegative=True)
        if "tolerance_rel" in values:
            if data.tolerance_rel is None:
                raise admin_error(422, "INVALID_NUMERIC", "tolerance_rel must be a finite nonnegative decimal")
            q.tolerance_rel = decimal_value(data.tolerance_rel, "tolerance_rel", nonnegative=True)
    await audit(db, user, "UPDATE_QUESTION", f"question_id={q.id};round={round_code}")
    await db.commit()
    return question_json(q)


@router.delete("/questions/{question_id}")
async def delete_question(question_id: int, user=Depends(require_admin), db: AsyncSession=Depends(get_db)):
    round_code = await db.scalar(select(Question.round_code).where(Question.id == question_id))
    await db.rollback()
    if not round_code:
        raise admin_error(404, "QUESTION_NOT_FOUND", "Question not found")
    await lock_draft_round(db, round_code)
    q = await db.scalar(select(Question).where(Question.id == question_id).with_for_update())
    await audit(db, user, "DELETE_QUESTION", f"question_id={q.id};round={round_code};position={q.position}")
    await db.delete(q); await db.commit()
    return {"deleted": True, "question_id": question_id}


@router.get("/questions/{question_id}/options")
async def list_options(question_id: int, user=Depends(require_admin), db: AsyncSession=Depends(get_db)):
    if not await db.scalar(select(Question.id).where(Question.id == question_id)):
        raise admin_error(404, "QUESTION_NOT_FOUND", "Question not found")
    rows = (await db.scalars(select(QuestionOption).where(
        QuestionOption.question_id == question_id).order_by(QuestionOption.position))).all()
    return {"items": [option_json(o) for o in rows]}


@router.get("/options/{option_id}")
async def get_option(option_id: int, user=Depends(require_admin), db: AsyncSession=Depends(get_db)):
    option = await db.scalar(select(QuestionOption).where(QuestionOption.id == option_id))
    if not option:
        raise admin_error(404, "OPTION_NOT_FOUND", "Option not found")
    return option_json(option)


@router.put("/options/{option_id}")
async def update_option(option_id: int, data: OptionUpdate,
                        user=Depends(require_admin), db: AsyncSession=Depends(get_db)):
    row = (await db.execute(select(QuestionOption.id, Question.round_code)
           .join(Question, Question.id == QuestionOption.question_id)
           .where(QuestionOption.id == option_id))).one_or_none()
    await db.rollback()
    if not row:
        raise admin_error(404, "OPTION_NOT_FOUND", "Option not found")
    await lock_draft_round(db, row.round_code)
    option = await db.scalar(select(QuestionOption).where(
        QuestionOption.id == option_id).with_for_update())
    question = await db.scalar(select(Question).where(Question.id == option.question_id).with_for_update())
    values = data.model_fields_set
    for field in ("position", "option_text", "image_url"):
        if field in values:
            setattr(option, field, getattr(data, field))
    if "is_correct" in values:
        if question.round_code != "R1A":
            raise admin_error(422, "OPTION_TYPE_INVALID", "Only R1A options can be correct")
        if data.is_correct:
            question.correct_option_id = option.id
        elif question.correct_option_id == option.id:
            question.correct_option_id = None
    await audit(db, user, "UPDATE_OPTION", f"option_id={option.id};question_id={question.id}")
    await db.commit()
    return option_json(option)


@router.delete("/options/{option_id}")
async def delete_option(option_id: int, user=Depends(require_admin), db: AsyncSession=Depends(get_db)):
    row = (await db.execute(select(QuestionOption.id, Question.round_code)
           .join(Question, Question.id == QuestionOption.question_id)
           .where(QuestionOption.id == option_id))).one_or_none()
    await db.rollback()
    if not row:
        raise admin_error(404, "OPTION_NOT_FOUND", "Option not found")
    await lock_draft_round(db, row.round_code)
    option = await db.scalar(select(QuestionOption).where(
        QuestionOption.id == option_id).with_for_update())
    await audit(db, user, "DELETE_OPTION", f"option_id={option.id};question_id={option.question_id}")
    await db.delete(option); await db.commit()
    return {"deleted": True, "option_id": option_id}

@router.post("/options/{option_id}/image")
async def option_image(option_id: int, image: UploadFile=File(...), user=Depends(require_admin), db: AsyncSession=Depends(get_db)):
    row = (await db.execute(select(Question.round_code)
           .join(QuestionOption, QuestionOption.question_id == Question.id)
           .where(QuestionOption.id == option_id))).scalar_one_or_none()
    await db.rollback()
    if not row:
        raise admin_error(404, "OPTION_NOT_FOUND", "Option not found")
    await lock_draft_round(db, row)
    opt = await db.scalar(select(QuestionOption).where(QuestionOption.id == option_id).with_for_update())
    try:
        url = await StorageService().save(image, f"options/{option_id}")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    opt.image_url = url
    await audit(db, user, "UPLOAD_OPTION_IMAGE", f"option_id={option_id}")
    await db.commit()
    return {"image_url": url}

@router.post("/questions/{question_id}/image")
async def question_image(question_id: int, image: UploadFile=File(...), user=Depends(require_admin), db: AsyncSession=Depends(get_db)):
    round_code = await db.scalar(select(Question.round_code).where(Question.id == question_id))
    await db.rollback()
    if not round_code: raise admin_error(404, "QUESTION_NOT_FOUND", "Question not found")
    await lock_draft_round(db, round_code)
    q = await db.scalar(select(Question).where(Question.id == question_id).with_for_update())
    try:
        url = await StorageService().save(image, f"questions/{question_id}")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    q.question_image_url = url
    await audit(db, user, "UPLOAD_QUESTION_IMAGE", f"question_id={question_id}")
    await db.commit()
    return {"image_url": url}

@router.post("/rounds/seed")
async def seed_rounds(user=Depends(require_admin), db: AsyncSession=Depends(get_db)):
    created = []
    for code in ("R1A", "R1B"):
        existing = (await db.execute(select(Round).where(Round.code==code))).scalar_one_or_none()
        if not existing:
            db.add(Round(code=code, r1b_timer_mode=configured_rules().r1b.default_timer_mode))
            created.append(code)
    await audit(db, user, "SEED_ROUNDS", f"created={','.join(created)}")
    await db.commit()
    return {"ok": True}


@router.post("/rounds/reset")
async def reset_round_one(data: ResetRoundOneIn, user=Depends(require_admin),
                          db: AsyncSession=Depends(get_db)):
    """Reset both Round 1 competitions in one serialized transaction."""
    if settings.environment != "development":
        raise HTTPException(404, "Development reset is disabled")
    reason = data.reason.strip()
    if not reason:
        raise admin_error(422, "RESET_REASON_REQUIRED", "An audited reset reason is required")
    if data.confirmation != "RESET ROUND 1":
        raise admin_error(422, "RESET_CONFIRMATION_INVALID",
                          "Type RESET ROUND 1 exactly to confirm")

    # Global mutation order is R1A Round -> R1B Round -> dependent state.
    # These exclusive locks wait for player mutations and both reconcilers, and
    # stop new ones from observing a partially reset competition.
    rounds = (await db.scalars(select(Round).where(
        Round.code.in_(("R1A", "R1B"))
    ).order_by(Round.code).with_for_update())).all()
    by_code = {round_obj.code: round_obj for round_obj in rounds}
    if set(by_code) != {"R1A", "R1B"}:
        raise admin_error(409, "ROUND_RECORDS_MISSING",
                          "Initialize both round records before resetting")
    live = [code for code in ("R1A", "R1B")
            if by_code[code].status == RoundStatus.LIVE.value]
    if live:
        raise admin_error(409, "ROUND_LIVE",
                          f"Pause or end the live rounds first: {', '.join(live)}")

    now = utc_now()
    admin_id = await acting_admin_id(db, user)
    session_result = await db.execute(update(PlayerDevice).where(
        PlayerDevice.active.is_(True)
    ).values(active=False, revoked_at=now, revoked_by_admin_id=admin_id,
             revocation_reason="Round 1 development reset"))
    counts = {"player_sessions_revoked": session_result.rowcount}
    for key, model in (
        ("score_events", ScoreEvent),
        ("r1a_receipts", R1AReceipt),
        ("submissions", Submission),
        ("numerical_drafts", R1BDraft),
        ("relay_team_states", TeamRoundState),
        ("enrolments", RoundEnrollment),
        ("leaderboard_snapshots", LeaderboardSnapshot),
    ):
        result = await db.execute(delete(model))
        counts[key] = result.rowcount

    for round_obj in rounds:
        round_obj.status = RoundStatus.DRAFT.value
        round_obj.started_at = None
        round_obj.ended_at = None
        round_obj.leaderboard_published_at = None
        round_obj.current_position = None
        round_obj.question_started_at = None
        round_obj.paused_at = None
        round_obj.r1a_result_due_at = None
        round_obj.rules_version = None
        round_obj.rules_snapshot = None

    details = json.dumps({
        "reason": reason,
        "reset_at": now.isoformat(),
        "rounds": ["R1A", "R1B"],
        "affected_records": counts,
    }, sort_keys=True, separators=(",", ":"))
    db.add(AdminAction(admin_id=admin_id, action="RESET_ROUND_ONE", details=details,
                       created_at=now))
    await db.commit()
    return {"ok": True, "reset_at": now, "affected_records": counts}


@router.get("/leaderboard/current")
async def current_leaderboard(round_code: str = Query("R1A", pattern="^R1[AB]$"),
                              user=Depends(require_admin), db: AsyncSession=Depends(get_db)):
    from ..question_reveal import read_leaderboard
    return await read_leaderboard(db, round_code)


@router.get("/audit")
async def list_audit(offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500),
                     user=Depends(require_admin), db: AsyncSession=Depends(get_db)):
    total = await db.scalar(select(func.count()).select_from(AdminAction))
    rows = (await db.execute(select(AdminAction, Admin.username)
            .outerjoin(Admin, Admin.id == AdminAction.admin_id)
            .order_by(AdminAction.created_at.desc(), AdminAction.id.desc())
            .offset(offset).limit(limit))).all()
    return {"total": total, "offset": offset, "limit": limit,
            "items": [{"id": row.id, "admin_id": row.admin_id,
                       "admin_username": username, "action": row.action,
                       "details": row.details, "created_at": row.created_at}
                      for row, username in rows]}


@router.get("/exports/results.xlsx")
async def export_results(scope: Literal["R1A", "R1B", "BOTH"] = "BOTH",
                         user=Depends(require_admin), db: AsyncSession=Depends(get_db)):
    # The first statement establishes one read-only snapshot for every workbook sheet.
    await db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
    content, exported_at, _ = await build_results_workbook(db, scope)
    await db.commit()
    filename = f"round1-results-{scope.lower()}-{exported_at.strftime('%Y%m%dT%H%M%SZ')}.xlsx"
    return StreamingResponse(
        BytesIO(content),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"',
                 "X-Content-Type-Options": "nosniff"},
    )


@router.get("/teams/{team_id}/sessions")
async def list_team_sessions(team_id: int, user=Depends(require_admin), db: AsyncSession=Depends(get_db)):
    now = utc_now()
    rows = (await db.scalars(select(PlayerDevice).where(
        PlayerDevice.team_id == team_id
    ).order_by(PlayerDevice.created_at.desc()))).all()
    return {"team_id": team_id, "items": [{
        "session_id": row.session_id, "round_code": row.round_code,
        "slot": row.player_number, "active": bool(row.active and row.expires_at > now),
        "created_at": row.created_at, "last_seen_at": row.last_seen_at,
        "lease_expires_at": row.expires_at, "revoked_at": row.revoked_at,
        "revocation_reason": row.revocation_reason,
    } for row in rows]}


async def revoke_rows(db, team_id, session_id, reason, user):
    # Match player mutations: Round(s), then Team, then Session row(s).
    query = select(PlayerDevice.round_code).where(PlayerDevice.team_id == team_id)
    if session_id:
        query = query.where(PlayerDevice.session_id == session_id)
    round_codes = sorted(set((await db.scalars(query)).all()))
    for code in round_codes:
        await lock_round(db, code, exclusive=True)
    await db.execute(select(Team).where(Team.id == team_id).with_for_update())
    rows_query = select(PlayerDevice).where(PlayerDevice.team_id == team_id)
    if session_id:
        rows_query = rows_query.where(PlayerDevice.session_id == session_id)
    rows = (await db.scalars(rows_query.with_for_update())).all()
    now = utc_now()
    admin_id = await acting_admin_id(db, user)
    for row in rows:
        if row.active:
            row.active = False
            row.revoked_at = now
            row.revoked_by_admin_id = admin_id
            row.revocation_reason = reason
    db.add(AdminAction(admin_id=admin_id, action="REVOKE_PLAYER_SESSION",
                       details=f"team_id={team_id};session_id={session_id or '*'};reason={reason}"))
    return len([row for row in rows if row.revoked_at == now])


@router.post("/teams/{team_id}/sessions/{session_id}/revoke")
async def revoke_team_session(team_id: int, session_id: str, data: RevokeSessionsIn,
                              user=Depends(require_admin), db: AsyncSession=Depends(get_db)):
    async with db.begin():
        count = await revoke_rows(db, team_id, session_id, data.reason, user)
    if not count:
        raise HTTPException(404, "Active session not found")
    return {"ok": True, "revoked": count}


@router.post("/teams/{team_id}/sessions/revoke-all")
async def revoke_all_team_sessions(team_id: int, data: RevokeSessionsIn,
                                   user=Depends(require_admin), db: AsyncSession=Depends(get_db)):
    async with db.begin():
        count = await revoke_rows(db, team_id, None, data.reason, user)
    return {"ok": True, "revoked": count}


@router.post("/teams/{team_id}/release-session")
async def release_team_session(team_id: int, user=Depends(require_admin), db: AsyncSession=Depends(get_db)):
    async with db.begin():
        count = await revoke_rows(db, team_id, None, "Released from legacy admin control", user)
    return {"ok": True, "revoked": count}
