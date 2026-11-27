"""The safe expression evaluator for rubric ``when`` clauses. No eval/exec anywhere."""

import pytest

from svc_validators.services.expr import ExprError, compile_expr, evaluate


def ev(source: str, **features: object) -> object:
    return evaluate(compile_expr(source), features)


def test_true_and_false_literals() -> None:
    assert ev("true") is True
    assert ev("false") is False
    assert ev("True") is True


@pytest.mark.parametrize(
    "op,value,expected",
    [
        (">=", 5, True),
        (">=", 4.99, False),
        (">", 5, False),
        (">", 5.01, True),
        ("<=", 5, True),
        ("<=", 5.01, False),
        ("<", 5, False),
        ("<", 4.99, True),
        ("==", 5, True),
        ("==", 5.0, True),
        ("==", 4, False),
        ("!=", 4, True),
        ("!=", 5, False),
    ],
)
def test_comparison_boundaries(op: str, value: float, expected: bool) -> None:
    assert ev(f"n {op} 5", n=value) is expected


def test_and_or_not_precedence() -> None:
    assert ev("a and b or c", a=False, b=True, c=True) is True
    assert ev("a and (b or c)", a=False, b=True, c=True) is False
    assert ev("not a", a=False) is True
    assert ev("not a and b", a=False, b=True) is True


def test_chained_comparison() -> None:
    assert ev("1 < n <= 5", n=5) is True
    assert ev("1 < n <= 5", n=6) is False


def test_arithmetic_on_features() -> None:
    assert ev("effort <= 0.5*hours", effort=50, hours=100) is True
    assert ev("effort <= 0.5*hours", effort=50.1, hours=100) is False
    assert ev("a + b - 1 == 4", a=2, b=3) is True
    assert ev("a / b == 2", a=4, b=2) is True
    assert ev("-a == -3", a=3) is True


def test_membership_and_len() -> None:
    assert ev("'blocker' in flags", flags=["blocker", "pii"]) is True
    assert ev("'blocker' in flags", flags=[]) is False
    assert ev("'x' not in flags", flags=["y"]) is True
    assert ev("len(flags) == 2", flags=["a", "b"]) is True
    assert ev("len(flags) == 0", flags=[]) is True


def test_bare_boolean_feature() -> None:
    assert ev("compute_fit", compute_fit=True) is True
    assert ev("not compute_fit", compute_fit=False) is True


def test_unknown_name_raises() -> None:
    with pytest.raises(ExprError, match="unknown feature"):
        ev("missing > 1")


def test_none_feature_raises() -> None:
    with pytest.raises(ExprError, match="unknown feature"):
        ev("x > 1", x=None)


def test_division_by_zero_raises_expr_error() -> None:
    with pytest.raises(ExprError):
        ev("a / b > 1", a=1, b=0)


def test_incomparable_types_raise_expr_error() -> None:
    with pytest.raises(ExprError):
        ev("a > 1", a="text")


def test_len_of_non_sized_raises_expr_error() -> None:
    with pytest.raises(ExprError):
        ev("len(a) > 1", a=5)


@pytest.mark.parametrize(
    "source",
    [
        "__import__('os').system('id')",
        "open('/etc/passwd')",
        "eval('1')",
        "exec('x=1')",
        "a.__class__",
        "a.b",
        "a[0]",
        "[1, 2]",
        "{'a': 1}",
        "lambda: 1",
        "(x for x in y)",
        "a if b else c",
        "len(a, b)",
        "len(a=1)",
        "print(a)",
        "a ** 2",
        "a // 2",
        "a % 2",
        "f'{a}'",
        "a := 1",
        "1; 2",
        "",
        "   ",
        "a >",
        "len > 1",
        "(",
        "x" * 500,
        "a or " * 100 + "b",
    ],
)
def test_forbidden_or_malformed_syntax_is_rejected_at_compile_time(source: str) -> None:
    with pytest.raises(ExprError):
        compile_expr(source)


def test_excessive_nesting_is_rejected() -> None:
    with pytest.raises(ExprError, match="too deep"):
        compile_expr("not " * 30 + "a")


def test_compile_does_not_execute_anything() -> None:
    # Parsing alone must be side-effect free even for hostile source text.
    with pytest.raises(ExprError):
        compile_expr("__import__('os').system('touch /tmp/pwned_by_rubric')")
