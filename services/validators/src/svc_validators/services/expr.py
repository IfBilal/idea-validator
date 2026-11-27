"""Safe evaluator for rubric ``feature_prior`` ``when`` expressions (docs/harness/09 §6).

The source is parsed to an AST (``ast.parse`` never runs code) and validated against a small
whitelist: comparisons, ``and``/``or``/``not``, ``+ - * /``, ``in``/``not in``, the literals
``true``/``false``, numbers, strings, feature names, and ``len(x)``. Anything else is rejected at
compile time. Evaluation is a hand-written tree walk: there is no ``eval``/``exec`` anywhere.
"""

import ast
import operator
from collections.abc import Callable, Mapping
from typing import Any

MAX_SOURCE_LEN = 200
MAX_DEPTH = 20


class ExprError(ValueError):
    """The expression is not allowed, or cannot be evaluated against the given features."""


Compiled = ast.expr

_BIN: dict[type[ast.operator], Callable[[Any, Any], Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
}
_CMP: dict[type[ast.cmpop], Callable[[Any, Any], bool]] = {
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.In: lambda a, b: a in b,
    ast.NotIn: lambda a, b: a not in b,
}
_LITERAL_NAMES = {"true": True, "false": False}


def _check(node: ast.AST, depth: int = 0) -> None:
    if depth > MAX_DEPTH:
        raise ExprError("expression is too deep")
    match node:
        case ast.Constant(value=value) if isinstance(value, bool | int | float | str):
            return
        case ast.Name(id=name):
            if name == "len":
                raise ExprError("len may only be called")
            return
        case ast.BoolOp(values=values):
            for v in values:
                _check(v, depth + 1)
        case ast.UnaryOp(op=ast.Not() | ast.USub(), operand=operand):
            _check(operand, depth + 1)
        case ast.BinOp(op=op, left=left, right=right) if type(op) in _BIN:
            _check(left, depth + 1)
            _check(right, depth + 1)
        case ast.Compare(left=left, ops=ops, comparators=comparators) if all(
            type(o) in _CMP for o in ops
        ):
            _check(left, depth + 1)
            for c in comparators:
                _check(c, depth + 1)
        case ast.Call(func=ast.Name(id="len"), args=[arg], keywords=[]):
            _check(arg, depth + 1)
        case _:
            raise ExprError(f"unsupported syntax: {type(node).__name__}")


def compile_expr(source: str) -> Compiled:
    if not source.strip():
        raise ExprError("empty expression")
    if len(source) > MAX_SOURCE_LEN:
        raise ExprError("expression is too long")
    try:
        tree = ast.parse(source.strip(), mode="eval")
    except (SyntaxError, ValueError, RecursionError) as exc:
        raise ExprError(f"cannot parse expression: {exc}") from exc
    _check(tree.body)
    return tree.body


def _eval(node: ast.expr, features: Mapping[str, Any]) -> Any:
    match node:
        case ast.Constant(value=value):
            return value
        case ast.Name(id=name):
            if name in _LITERAL_NAMES:
                return _LITERAL_NAMES[name]
            if features.get(name) is None:
                raise ExprError(f"unknown feature {name!r}")
            return features[name]
        case ast.BoolOp(op=op, values=values):
            result: Any = None
            for v in values:
                result = _eval(v, features)
                if isinstance(op, ast.And) != bool(result):
                    return result
            return result
        case ast.UnaryOp(op=op, operand=operand):
            value = _eval(operand, features)
            return (not value) if isinstance(op, ast.Not) else -value
        case ast.BinOp(op=op, left=left, right=right):
            return _BIN[type(op)](_eval(left, features), _eval(right, features))
        case ast.Compare(left=left, ops=ops, comparators=comparators):
            current = _eval(left, features)
            for cmp_op, comparator in zip(ops, comparators, strict=True):
                right_value = _eval(comparator, features)
                if not _CMP[type(cmp_op)](current, right_value):
                    return False
                current = right_value
            return True
        case ast.Call(args=[arg]):
            return len(_eval(arg, features))
    raise ExprError("unsupported syntax")  # pragma: no cover - rejected by compile_expr


def names(compiled: Compiled) -> set[str]:
    """Feature names referenced by a compiled expression (excludes ``true``/``false``/``len``)."""
    found = {n.id for n in ast.walk(compiled) if isinstance(n, ast.Name)}
    return found - set(_LITERAL_NAMES) - {"len"}


def evaluate(compiled: Compiled, features: Mapping[str, Any]) -> bool:
    """Evaluate a compiled condition; wrong types or division by zero become ``ExprError``."""
    try:
        return bool(_eval(compiled, features))
    except ExprError:
        raise
    except (TypeError, ZeroDivisionError, ValueError, OverflowError) as exc:
        raise ExprError(f"cannot evaluate expression: {exc}") from exc
