import os

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError

from tests.db_guard import require_disposable_test_database


pytestmark = [
    pytest.mark.database,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(os.getenv("RUN_DB_TESTS") != "1", reason="requires disposable PostgreSQL"),
]


async def test_migrations_match_orm_metadata():
    require_disposable_test_database()
    from app.db import Base, engine
    from app import models  # noqa: F401 - registers all tables with Base.metadata

    async with engine.connect() as connection:
        table_names = await connection.run_sync(lambda conn: inspect(conn).get_table_names())
        current_revision = await connection.scalar(text("SELECT version_num FROM alembic_version"))
        differences = await connection.run_sync(
            lambda conn: compare_metadata(MigrationContext.configure(conn), Base.metadata)
        )

    assert "alembic_version" in table_names
    assert current_revision == "0016_rehearsal_reset_guard"
    assert set(Base.metadata.tables) <= set(table_names)
    assert differences == [], repr(differences)


async def test_submission_score_atomicity_duplicate_and_rollback():
    require_disposable_test_database()
    from app.db import SessionLocal, engine
    from app.models import ScoreEvent, Submission, Team

    async with SessionLocal() as session:
        team = Team(code="TEST-DB-SMOKE", name="Database smoke", login_username="test-db-smoke")
        session.add(team)
        await session.flush()
        submission = Submission(
            team_id=team.id,
            round_code="R1A",
            question_position=1,
            result="CORRECT",
            points=1000,
        )
        session.add(submission)
        await session.flush()
        session.add(ScoreEvent(
            team_id=team.id,
            round_code="R1A",
            submission_id=submission.id,
            reason="DATABASE_SMOKE",
            points=1000,
        ))
        await session.commit()
        team_id = team.id
        submission_id = submission.id

    async with SessionLocal() as session:
        assert await session.scalar(select(Submission.id).where(Submission.id == submission_id)) == submission_id
        assert await session.scalar(select(ScoreEvent.submission_id).where(
            ScoreEvent.submission_id == submission_id
        )) == submission_id

        session.add(Submission(
            team_id=team_id,
            round_code="R1A",
            question_position=1,
            result="WRONG",
            points=-500,
        ))
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

    async with SessionLocal() as session:
        rolled_back = Submission(
            team_id=team_id,
            round_code="R1A",
            question_position=2,
            result="CORRECT",
            points=1000,
        )
        session.add(rolled_back)
        await session.flush()
        session.add(ScoreEvent(
            team_id=team_id,
            round_code="R1A",
            submission_id=rolled_back.id,
            reason="DATABASE_SMOKE_ROLLBACK",
            points=1000,
        ))
        await session.flush()
        rolled_back_id = rolled_back.id
        await session.rollback()

    async with SessionLocal() as session:
        assert await session.scalar(select(Submission.id).where(Submission.id == rolled_back_id)) is None
        assert await session.scalar(select(ScoreEvent.submission_id).where(
            ScoreEvent.submission_id == rolled_back_id
        )) is None

    await engine.dispose()

