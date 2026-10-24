from datetime import timedelta
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
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

router = APIRouter(prefix="/api/admin", tags=["admin"])
settings = get_settings()

class TeamCreate(BaseModel):
    code: str
    name: str
    username: str
    password: str | None = Field(default=None, min_length=8, max_length=128)

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
    db.add(team); await db.commit(); await db.refresh(team)
    return {"id": team.id, "code": team.code, "name": team.name,
            "username": team.login_username, "activation_token": activation_token,
            "activation_expires_at": activation_expires}

@router.post("/rounds/start")
async def start_round(data: RoundAction, user=Depends(require_admin), db: AsyncSession=Depends(get_db)):
    if data.round_code not in ("R1A", "R1B"):
        raise HTTPException(400, "Unsupported round code")
    r = await lock_round(db, data.round_code, exclusive=True)
    if not r:
        raise HTTPException(404, "Round not found")
    if r.status != RoundStatus.DRAFT.value:
        raise HTTPException(409, "A round can only be started once")
    required = 23 if data.round_code == "R1A" else 4
    questions = (await db.execute(select(Question).where(
        Question.round_code == data.round_code, Question.enabled.is_(True)
    ).order_by(Question.position))).scalars().all()
    if [q.position for q in questions] != list(range(1, required + 1)):
        raise HTTPException(409, f"{data.round_code} needs enabled questions numbered 1 through {required}")
    if data.round_code == "R1A" and any(q.answer_type != "MCQ" or q.correct_option_id is None for q in questions):
        raise HTTPException(409, "Every R1A question needs type MCQ and a correct option")
    if data.round_code == "R1B" and any(q.answer_type != "NUMERIC" or q.correct_numeric is None for q in questions):
        raise HTTPException(409, "Every R1B question needs type NUMERIC and a correct numeric answer")
    r.status = RoundStatus.LIVE.value
    r.started_at = utc_now()
    if r.code == "R1A":
        r.current_position = 1
        r.question_started_at = r.started_at
    r.ended_at = None
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
    if r.status != RoundStatus.LIVE.value:
        raise HTTPException(409, "Only a live round can be paused")
    now = utc_now()
    r.status = RoundStatus.PAUSED.value
    r.paused_at = now
    db.add(AdminAction(admin_id=None, action="PAUSE_ROUND", details=r.code))
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
    # Shift R1A only if it was still active when pause acquired its lock.
    # A pause after the deadline must not resurrect an expired question.
    if (r.code == "R1A" and r.question_started_at
            and r.paused_at < r.question_started_at + timedelta(seconds=30)):
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
    db.add(AdminAction(admin_id=None, action="RESUME_ROUND", details=r.code))
    await db.commit()
    return {"round_code": r.code, "status": r.status, "resumed_at": now}


class NextQuestion(BaseModel):
    expected_position: int = Field(ge=0, le=23)


@router.post("/r1a/next")
async def next_question(data: NextQuestion, user=Depends(require_admin), db: AsyncSession=Depends(get_db)):
    r = await lock_round(db, "R1A", exclusive=True)
    if not r or r.status != "LIVE":
        raise HTTPException(409, "Start Round 1(a) first")
    if (r.current_position or 0) != data.expected_position:
        raise HTTPException(409, "Another admin advanced the question. Refresh the dashboard.")
    if r.current_position == 23:
        raise HTTPException(409, "All 23 questions have been revealed")
    now = utc_now()
    if r.question_started_at and now < r.question_started_at + timedelta(seconds=30):
        raise HTTPException(409, "Wait for the current question's 30 seconds to finish")
    position = (r.current_position or 0) + 1
    q = await db.scalar(select(Question).where(Question.round_code == "R1A",
                       Question.position == position, Question.enabled.is_(True)))
    if not q or q.correct_option_id is None:
        raise HTTPException(409, "Next question is not configured")
    r.current_position = position
    r.question_started_at = now
    await db.commit()
    return {"position": position, "question_deadline": now + timedelta(seconds=30)}


@router.get("/dashboard")
async def dashboard(user=Depends(require_admin), db: AsyncSession=Depends(get_db)):
    from .r1a import position_at
    now = utc_now()
    rounds = (await db.scalars(select(Round).order_by(Round.code))).all()
    return {"server_now": now, "rounds": [
        {"code": r.code, "status": r.status, "position": r.current_position,
         "timer_mode": r.r1b_timer_mode if r.code == "R1B" else None,
         "question_active": position_at(r, now) is not None if r.code == "R1A" else False,
         "question_deadline": r.question_started_at + timedelta(seconds=30) if r.question_started_at else None,
         "paused_at": r.paused_at}
        for r in rounds]}


@router.get("/submissions")
async def submissions(round_code: str | None = Query(None, pattern="^R1[AB]$"),
                      position: int | None = Query(None, ge=1, le=23),
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
         "result": s.result, "points": s.points}
        for s, t, o in rows]}


@router.get("/teams")
async def list_teams(user=Depends(require_admin), db: AsyncSession=Depends(get_db)):
    """Return the team directory with live activity and scoring totals."""
    from sqlalchemy import func
    rows = (await db.execute(
        select(Team, func.count(Submission.id),
               func.coalesce(func.sum(Submission.points), 0),
               func.max(Submission.submitted_at), PlayerDevice)
        .outerjoin(Submission, Submission.team_id == Team.id)
        .outerjoin(PlayerDevice, PlayerDevice.team_id == Team.id)
        .group_by(Team.id, PlayerDevice.id).order_by(Team.code)
    )).all()
    now = utc_now()
    return {"items": [
        {"id": team.id, "code": team.code, "name": team.name,
         "username": team.login_username, "enabled": team.enabled,
         "submission_count": count, "points": points,
         "last_submission_at": last_submission,
         "session_active": bool(device and device.active and device.expires_at > now),
         "last_seen_at": device.last_seen_at if device else None}
        for team, count, points, last_submission, device in rows
    ]}

@router.post("/rounds/end")
async def end_round(data: RoundAction, user=Depends(require_admin), db: AsyncSession=Depends(get_db)):
    r = await lock_round(db, data.round_code, exclusive=True)
    if not r:
        raise HTTPException(404, "Round not found")
    if r.status not in (RoundStatus.LIVE.value, RoundStatus.PAUSED.value):
        raise HTTPException(409, "Round is not live")
    r.status = RoundStatus.ENDED.value
    r.ended_at = utc_now()
    await db.commit()
    return {"round_code": r.code, "status": r.status, "ended_at": r.ended_at}

@router.post("/leaderboard/publish")
async def publish(data: RoundAction, user=Depends(require_admin), db: AsyncSession=Depends(get_db)):
    service = LeaderboardService()
    data_out = await service.publish(db, data.round_code)
    r = (await db.execute(select(Round).where(Round.code==data.round_code))).scalar_one()
    r.leaderboard_published_at = utc_now()
    await db.commit()
    return {"published": True, "data": data_out}

@router.post("/questions")
async def create_question(data: QuestionCreate, user=Depends(require_admin), db: AsyncSession=Depends(get_db)):
    round_code = data.round_code.upper()
    answer_type = data.answer_type.upper()
    if (round_code, answer_type) not in (("R1A", "MCQ"), ("R1B", "NUMERIC")):
        raise HTTPException(400, "R1A questions must be MCQ; R1B questions must be NUMERIC")
    max_position = 23 if round_code == "R1A" else 4
    if data.position > max_position:
        raise HTTPException(400, f"{round_code} position must be 1..{max_position}")
    q = Question(round_code=round_code, position=data.position, question_text=data.question_text,
                 answer_type=answer_type, correct_option_id=data.correct_option_id,
                 correct_numeric=data.correct_numeric, tolerance_abs=data.tolerance_abs, tolerance_rel=data.tolerance_rel)
    db.add(q); await db.commit(); await db.refresh(q)
    return {"id": q.id}


class OptionCreate(BaseModel):
    position: int = Field(ge=1, le=20)
    option_text: str | None = None
    is_correct: bool = False

@router.post("/questions/{question_id}/options")
async def create_option(question_id: int, data: OptionCreate, user=Depends(require_admin), db: AsyncSession=Depends(get_db)):
    q = (await db.execute(select(Question).where(Question.id == question_id))).scalar_one_or_none()
    if not q:
        raise HTTPException(404, "Question not found")
    opt = QuestionOption(question_id=question_id, position=data.position, option_text=data.option_text)
    db.add(opt)
    await db.flush()
    if data.is_correct:
        if q.round_code != "R1A":
            raise HTTPException(400, "Only R1A MCQ options can be marked correct")
        q.correct_option_id = opt.id
    await db.commit()
    return {"id": opt.id, "position": opt.position}

@router.post("/options/{option_id}/image")
async def option_image(option_id: int, image: UploadFile=File(...), user=Depends(require_admin), db: AsyncSession=Depends(get_db)):
    opt = (await db.execute(select(QuestionOption).where(QuestionOption.id == option_id))).scalar_one_or_none()
    if not opt:
        raise HTTPException(404, "Option not found")
    try:
        url = await StorageService().save(image, f"options/{option_id}")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    opt.image_url = url
    await db.commit()
    return {"image_url": url}

@router.post("/questions/{question_id}/image")
async def question_image(question_id: int, image: UploadFile=File(...), user=Depends(require_admin), db: AsyncSession=Depends(get_db)):
    q = (await db.execute(select(Question).where(Question.id==question_id))).scalar_one_or_none()
    if not q: raise HTTPException(404, "Question not found")
    try:
        url = await StorageService().save(image, f"questions/{question_id}")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    q.question_image_url = url
    await db.commit()
    return {"image_url": url}

@router.post("/rounds/seed")
async def seed_rounds(user=Depends(require_admin), db: AsyncSession=Depends(get_db)):
    for code in ("R1A", "R1B"):
        existing = (await db.execute(select(Round).where(Round.code==code))).scalar_one_or_none()
        if not existing: db.add(Round(code=code))
    await db.commit()
    return {"ok": True}


@router.post("/teams/{team_id}/release-session")
async def release_team_session(team_id: int, user=Depends(require_admin), db: AsyncSession=Depends(get_db)):
    device = (await db.execute(select(PlayerDevice).where(
        PlayerDevice.team_id == team_id
    ).with_for_update())).scalar_one_or_none()
    if device:
        device.active = False
    db.add(AdminAction(admin_id=None, action="RELEASE_TEAM_SESSION", details=str(team_id)))
    await db.commit()
    return {"ok": True}
