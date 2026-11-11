"""Database-specific, fail-closed authorization for rehearsal resets.

No HTTP route creates this attestation. Provision it with the operator script
before a fresh disposable rehearsal starts. PostgreSQL is the only durable R1
state store; the application currently never reads/writes gameplay in Redis.
"""
import hashlib
import hmac

from sqlalchemy import select, text

from .config import get_settings
from .models import RehearsalAuthorization


async def reset_authorization(db, *, lock=False):
    settings = get_settings()
    if (settings.environment != "rehearsal" or not settings.r1_reset_enabled
            or not settings.r1_reset_database_name or len(settings.r1_rehearsal_token) < 32):
        return False, "Reset requires an explicitly authorized rehearsal database."
    identity = (await db.execute(text(
        "SELECT current_database() AS name, oid FROM pg_database WHERE datname = current_database()"
    ))).one()
    if identity.name != settings.r1_reset_database_name:
        return False, "The connected database does not match the authorized rehearsal database."
    query = select(RehearsalAuthorization).where(RehearsalAuthorization.id == 1)
    if lock:
        query = query.with_for_update(read=True)
    marker = await db.scalar(query)
    digest = hashlib.sha256(settings.r1_rehearsal_token.encode()).hexdigest()
    if (marker is None or marker.database_name != identity.name
            or marker.database_oid != identity.oid
            or not hmac.compare_digest(marker.token_sha256, digest)):
        return False, "This database has no matching rehearsal authorization."
    return True, "Authorized disposable rehearsal database."


async def reset_control(db, rounds):
    authorized, reason = await reset_authorization(db)
    by_code = {r.code: r for r in rounds}
    available = authorized and {"R1A", "R1B"} <= by_code.keys()
    if authorized and not available:
        reason = "Initialize both R1 rounds before resetting."
    if available and any(by_code[c].status in ("LIVE", "PAUSED") for c in ("R1A", "R1B")):
        available = False
        reason = "End both active rounds before resetting. Live and paused rounds cannot be reset."
    return {"authorized": authorized, "available": available, "reason": reason}
