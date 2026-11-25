from __future__ import annotations

import pytest

from stella.extra_commands import CalculationError, calculate, datetime_text


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("2 + 2 * 3", "8"),
        ("sqrt(81)", "9"),
        ("round(pi, 2)", "3.14"),
        ("min(8, 3, 5)", "3"),
        ("-4 ** 2", "-16"),
    ],
)
def test_safe_calculator(expression, expected):
    assert calculate(expression) == expected


@pytest.mark.parametrize(
    "expression",
    [
        "__import__('os').system('id')",
        "open('/etc/passwd')",
        "2 ** 100",
        "1 / 0",
        "True + 1",
    ],
)
def test_calculator_rejects_unsafe_or_invalid_expressions(expression):
    with pytest.raises(CalculationError):
        calculate(expression)


def test_datetime_uses_requested_iana_timezone():
    result = datetime_text("America/Santiago")
    assert "America/Santiago" in result
    assert "UTC" in result


def test_datetime_rejects_invalid_timezone():
    with pytest.raises(ValueError):
        datetime_text("../../etc/passwd")
