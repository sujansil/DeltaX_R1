"""Opt a fresh LOCAL disposable rehearsal into reset; never resets data.

Existing competition history is refused, including a restored competition dump.
Use environment overrides: this script deliberately does not load .env for its
connection. The migration creates an empty guard; it never opts in a database.
"""
import argparse
import asyncio
import hashlib
import os
import re
import sys
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def validate_target(environ, expected_database):
    url = make_url(environ.get("DATABASE_URL", ""))
    if (url.drivername != "postgresql+asyncpg" or url.host not in {"127.0.0.1", "localhost", "::1"}
            or url.query or not expected_database or url.database != expected_database
            or not re.fullmatch(r"[a-zA-Z0-9_]+", expected_database)
            or not any(s in expected_database.lower() for s in ("rehearsal", "disposable"))):
        raise ValueError("Only an explicitly named local disposable/rehearsal database is permitted")
    if environ.get("ENVIRONMENT") != "rehearsal" or environ.get("R1_RESET_ENABLED", "").lower() != "true":
        raise ValueError("Explicit ENVIRONMENT=rehearsal and R1_RESET_ENABLED=true are required")
    if environ.get("R1_RESET_DATABASE_NAME") != expected_database:
        raise ValueError("R1_RESET_DATABASE_NAME must match --database exactly")
    token = environ.get("R1_REHEARSAL_TOKEN", "")
    if len(token) < 32:
        raise ValueError("R1_REHEARSAL_TOKEN must contain at least 32 characters")
    return url, hashlib.sha256(token.encode()).hexdigest()


async def authorize(environ, expected_database):
    url, digest = validate_target(environ, expected_database)
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            # Matches the reset lock order; prevents startup/control races.
            rounds = (await conn.execute(text(
                "SELECT code, status, started_at FROM rounds ORDER BY code FOR UPDATE"
            ))).all()
            if {r.code for r in rounds} != {"R1A", "R1B"}:
                raise ValueError("Initialize both R1A and R1B before authorizing rehearsal resets")
            if any(r.status != "DRAFT" or r.started_at for r in rounds):
                raise ValueError("Authorization requires fresh, not-started rounds")
            identity = (await conn.execute(text(
                "SELECT current_database() AS name, oid FROM pg_database WHERE datname=current_database()"
            ))).one()
            if identity.name != expected_database:
                raise ValueError("Connected database identity mismatch")
            await conn.execute(text("LOCK TABLE round1_rehearsal_authorization IN EXCLUSIVE MODE"))
            exists = await conn.scalar(text("SELECT count(*) FROM round1_rehearsal_authorization"))
            if exists:
                raise ValueError("Database is already attested; authorization cannot be overwritten")
            # The schema has one current R1 run, with no history/run discriminator.
            # Refuse to label existing historical competition rows as rehearsal.
            for table in ("submissions", "score_events", "leaderboard_snapshots", "round_enrollments",
                          "team_round_states", "r1a_receipts", "r1b_receipts", "r1b_drafts"):
                if await conn.scalar(text(f"SELECT EXISTS (SELECT 1 FROM {table})")):
                    raise ValueError(f"Fresh disposable database required: {table} contains history")
            if await conn.scalar(text("SELECT EXISTS (SELECT 1 FROM admin_actions WHERE action = 'START_ROUND')")):
                raise ValueError("Database has competition history; refusing rehearsal authorization")
            await conn.execute(text(
                "INSERT INTO round1_rehearsal_authorization "
                "(id, database_name, database_oid, token_sha256, authorized_at) "
                "VALUES (1, :name, :oid, :digest, now())"
            ), {"name": identity.name, "oid": identity.oid, "digest": digest})
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, help="Exact fresh local rehearsal database name")
    args = parser.parse_args()
    try:
        asyncio.run(authorize(os.environ, args.database))
    except Exception as exc:
        # Do not print driver errors: connection URLs can contain secrets.
        message = str(exc) if isinstance(exc, ValueError) else "Database authorization failed; check local target and schema"
        print(message, file=sys.stderr)
        return 1
    print("Fresh rehearsal database authorized. No competition state was reset.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
