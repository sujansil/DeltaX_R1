from dataclasses import replace
from app.competition_rules import configured_rules
from app.scoring import r1a_points, numeric_correct
from decimal import Decimal

def test_r1a_linear_points():
    rules = replace(configured_rules().r1a, decay_enabled=True,
                    full_score_until_ms=10_000, decay_end_ms=25_000)
    assert r1a_points(0, rules) == 1000
    assert r1a_points(10_000, rules) == 1000
    assert r1a_points(15_000, rules) == 750
    assert r1a_points(20_000, rules) == 500
    assert r1a_points(25_000, rules) == 250
    assert r1a_points(29_999, rules) == 250

def test_numeric_tolerance():
    assert numeric_correct("10.0001", Decimal("10"), Decimal("0.001"), Decimal("0"))
    assert not numeric_correct("10.01", Decimal("10"), Decimal("0.001"), Decimal("0"))
    assert not numeric_correct("NaN", Decimal("10"), Decimal("0.001"), Decimal("0"))
    assert not numeric_correct("Infinity", Decimal("10"), Decimal("0.001"), Decimal("0"))
