from app.scoring import r1a_points, numeric_correct
from decimal import Decimal

def test_r1a_linear_points():
    assert r1a_points(0) == 1000
    assert r1a_points(10_000) == 1000
    assert r1a_points(15_000) == 750
    assert r1a_points(20_000) == 500
    assert r1a_points(25_000) == 250
    assert r1a_points(29_999) == 250

def test_numeric_tolerance():
    assert numeric_correct("10.0001", Decimal("10"), Decimal("0.001"), Decimal("0"))
    assert not numeric_correct("10.01", Decimal("10"), Decimal("0.001"), Decimal("0"))
    assert not numeric_correct("NaN", Decimal("10"), Decimal("0.001"), Decimal("0"))
    assert not numeric_correct("Infinity", Decimal("10"), Decimal("0.001"), Decimal("0"))
