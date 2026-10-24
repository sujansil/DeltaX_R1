from decimal import Decimal, InvalidOperation

def r1a_points(elapsed_ms: int) -> int:
    # Exact linear decay. 10s -> 1000, 25s -> 250.
    # At/after 25s the score is held at the minimum 250 until timeout.
    if elapsed_ms <= 10_000:
        return 1000
    if elapsed_ms >= 25_000:
        return 250
    # 50 points per second after 10s; use integer milliseconds to avoid float timing.
    points = Decimal(1000) - (Decimal(elapsed_ms - 10_000) * Decimal(50) / Decimal(1000))
    return max(250, int(points))

def r1a_result(correct: bool | None, elapsed_ms: int) -> int:
    if correct is None:
        return 0
    return r1a_points(elapsed_ms) if correct else -500

def numeric_correct(answer: str | None, correct: Decimal | None, abs_tol: Decimal, rel_tol: Decimal) -> bool:
    if answer is None or correct is None:
        return False
    try:
        value = Decimal(answer.strip())
    except (InvalidOperation, AttributeError):
        return False
    if not value.is_finite() or not correct.is_finite():
        return False
    abs_tol = max(Decimal(0), abs_tol)
    rel_tol = max(Decimal(0), rel_tol)
    diff = abs(value - correct)
    allowed = max(abs_tol, abs(correct) * rel_tol)
    return diff <= allowed
