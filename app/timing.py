from datetime import datetime, timezone

def utc_now() -> datetime:
    return datetime.now(timezone.utc)

def elapsed_ms(start: datetime, end: datetime) -> int:
    return max(0, int((end - start).total_seconds() * 1000))

def remaining_ms(deadline: datetime, now: datetime | None = None) -> int:
    now = now or utc_now()
    return max(0, int((deadline - now).total_seconds() * 1000))
