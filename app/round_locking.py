"""Shared lock-order primitives for round state transitions.

Every mutating competition transaction locks in this order:
1. the Round row (FOR SHARE for player actions, FOR UPDATE for admin actions),
2. the team's TeamRoundState row when R1B needs one,
3. submission and score rows.

Shared round locks let different teams proceed concurrently. Admin transitions take
the exclusive form and therefore serialize with every in-flight player transition.
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Round


async def lock_round(db: AsyncSession, code: str, *, exclusive: bool) -> Round | None:
    statement = select(Round).where(Round.code == code).with_for_update(read=not exclusive)
    return (await db.execute(statement)).scalar_one_or_none()
