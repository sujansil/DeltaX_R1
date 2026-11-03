from decimal import Decimal, InvalidOperation
from .competition_rules import R1ARules, configured_rules

def r1a_points(elapsed_ms: int, rules: R1ARules | None = None) -> int:
    rules = rules or configured_rules().r1a
    if not rules.decay_enabled:
        return rules.maximum_correct_score
    if elapsed_ms <= rules.full_score_until_ms:
        return rules.maximum_correct_score
    if elapsed_ms >= rules.decay_end_ms:
        return rules.minimum_correct_score
    # Derive the slope from endpoints. Decimal + int preserves truncation and ms handling.
    score_range = Decimal(rules.maximum_correct_score - rules.minimum_correct_score)
    decay_duration = Decimal(rules.decay_end_ms - rules.full_score_until_ms)
    points = Decimal(rules.maximum_correct_score) - (
        Decimal(elapsed_ms - rules.full_score_until_ms) * score_range / decay_duration
    )
    return max(rules.minimum_correct_score, int(points))

def r1a_result(correct: bool | None, elapsed_ms: int, rules: R1ARules | None = None) -> int:
    rules = rules or configured_rules().r1a
    if correct is None:
        return rules.unanswered_score
    return r1a_points(elapsed_ms, rules) if correct else rules.wrong_answer_score

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
