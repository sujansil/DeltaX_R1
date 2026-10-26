#!/usr/bin/env python3
"""Reset an existing admin password in the start.sh LOCAL PREVIEW database only.

Run from the project root:
    .venv/bin/python /path/to/reset_preview_admin.py
Or supply:
    /path/to/.venv/bin/python reset_preview_admin.py --project /path/to/round1_system
"""
import argparse
import asyncio
import getpass
import os
from pathlib import Path
import sys


def configure_preview(project):
    from dotenv import dotenv_values
    project = project.resolve()
    if not (project / 'app/main.py').is_file():
        raise ValueError('Choose the project root containing app/main.py.')
    env_file = project / '.preview/preview.env'
    if not env_file.is_file():
        raise ValueError('No start.sh preview configuration found. Start the preview with ./start.sh first.')
    values = dotenv_values(env_file)
    password = values.get('PREVIEW_DB_PASSWORD', '')
    secret = values.get('SECRET_KEY', '')
    # start.sh generates these as hexadecimal. Refuse arbitrary URL components.
    if not password or any(c not in '0123456789abcdefABCDEF' for c in password):
        raise ValueError('Unrecognized preview database configuration. No changes made.')
    if not secret:
        raise ValueError('Preview SECRET_KEY is missing. No changes made.')
    os.environ['DATABASE_URL'] = (
        f'postgresql+asyncpg://round1_preview:{password}@127.0.0.1:55432/round1_preview'
    )
    os.environ['SECRET_KEY'] = secret
    os.environ['ENVIRONMENT'] = 'development'
    sys.path.insert(0, str(project))
    os.chdir(project)


async def reset_password():
    from sqlalchemy import select
    from app.db import SessionLocal, engine
    from app.models import Admin
    from app.security import hash_password_async, verify_password_async

    try:
        async with SessionLocal() as db:
            # Do not leave a transaction open while waiting for user input.
            rows = (await db.execute(select(Admin.username, Admin.enabled)
                                     .order_by(Admin.username))).all()
        if not rows:
            print('No admins exist in this preview database. No changes made.')
            print('Start the preview and create a new development admin account.')
            return 1
        print('Local preview admin accounts:')
        for name, enabled in rows:
            print(f'  {name}' + ('' if enabled else ' [disabled]'))
        username = input('Existing admin username to reset: ').strip()
        selected = next((row for row in rows if row[0] == username), None)
        if selected is None:
            raise ValueError('That username is not listed. No changes made.')
        if not selected[1]:
            raise ValueError('That account is disabled. This script will not bypass account disablement.')
        password = getpass.getpass('New password (8–128 characters; hidden): ')
        confirmation = getpass.getpass('Repeat new password: ')
        if not 8 <= len(password) <= 128:
            raise ValueError('Password must contain 8–128 characters. No changes made.')
        if password != confirmation:
            raise ValueError('Passwords did not match. No changes made.')
        hashed = await hash_password_async(password)
        if not await verify_password_async(password, hashed):
            raise ValueError('Password hash verification failed. No changes made.')
        async with SessionLocal() as db:
            async with db.begin():
                admin = await db.scalar(select(Admin).where(Admin.username == username)
                                        .with_for_update())
                if admin is None or not admin.enabled:
                    raise ValueError('Account changed while prompting. No changes made.')
                admin.password_hash = hashed
        print('Password reset successfully in the local preview database.')
        print('Log in at http://127.0.0.1:8000/admin-ui/ using the selected username.')
        print('Teams, questions, scores and round progress were not changed.')
        print('Existing admin JWTs are not revoked by this password-only recovery.')
        return 0
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, default=Path.cwd())
    args = parser.parse_args()
    try:
        configure_preview(args.project)
        return asyncio.run(reset_password())
    except (KeyboardInterrupt, EOFError):
        print('\nCancelled. No password change was committed.', file=sys.stderr)
        return 1
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception:
        # SQLAlchemy connection exceptions may include connection parameters;
        # avoid printing them or any configuration/credentials.
        print('Recovery failed. Ensure the start.sh preview PostgreSQL service is running on port 55432.', file=sys.stderr)
        print('No reset was confirmed. If the database is not the start.sh preview, do not use this script.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
