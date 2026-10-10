"""Safe evaluator for LLM-supplied function expressions such as ``x**2 - 4`` or ``sin(x)/x``.

Expressions are parsed with ``ast`` and evaluated by an explicit whitelist walker, never
with ``eval``; document-derived text therefore cannot execute code.
"""
from __future__ import annotations

import ast
import math
import re
from typing import Callable

import numpy as np

MAX_LENGTH = 200
MAX_NODES = 120
MAX_POWER = 30


class ExpressionError(ValueError):
    pass


_FUNCS = {
    "sin": np.sin, "cos": np.cos, "tan": np.tan,
    "asin": np.arcsin, "acos": np.arccos, "atan": np.arctan,
    "sinh": np.sinh, "cosh": np.cosh, "tanh": np.tanh,
    "exp": np.exp, "log": np.log, "ln": np.log, "log10": np.log10, "log2": np.log2,
    "sqrt": np.sqrt, "cbrt": np.cbrt, "abs": np.abs, "floor": np.floor, "ceil": np.ceil,
    "sign": np.sign,
}
_CONSTS = {"pi": math.pi, "e": math.e}
_TOKEN = re.compile(r"\s*(\d+\.?\d*(?:[eE][+-]?\d+)?|\.\d+|[A-Za-z_]\w*|\*\*|[-+*/^()%,|])")


def _tokenize(src: str) -> list:
    pos, out = 0, []
    while pos < len(src):
        m = _TOKEN.match(src, pos)
        if not m:
            if src[pos:].strip() == "":
                break
            raise ExpressionError(f"Unexpected character {src[pos:pos + 1]!r} in expression.")
        out.append(m.group(1))
        pos = m.end()
    return out


def _is_number(tok: str) -> bool:
    return bool(tok) and (tok[0].isdigit() or tok[0] == ".")


def _is_name(tok: str) -> bool:
    return bool(tok) and (tok[0].isalpha() or tok[0] == "_")


def normalize(expr: str) -> str:
    """Accept school notation: ``^`` for powers, implicit multiplication (``2x``, ``3(x+1)``)."""
    if not isinstance(expr, str):
        raise ExpressionError("Expression must be a string.")
    src = expr.strip().replace("^", "**").replace("×", "*").replace("÷", "/").replace("−", "-")
    if src.lower().startswith("y="):
        src = src[2:]
    src = src.replace("\u00b2", "**2").replace("\u00b3", "**3")
    if not src or len(src) > MAX_LENGTH:
        raise ExpressionError("Expression is empty or too long.")
    tokens = _tokenize(src)
    out = []
    for i, tok in enumerate(tokens):
        if i:
            prev = tokens[i - 1]
            prev_value = _is_number(prev) or prev == ")" or (_is_name(prev) and prev not in _FUNCS)
            next_value = _is_number(tok) or _is_name(tok) or tok == "("
            func_call = prev in _FUNCS and tok == "("
            if prev_value and next_value and not func_call:
                out.append("*")
        out.append(tok)
    return " ".join(out)


def _walk(node: ast.AST, env: dict):
    if isinstance(node, ast.Expression):
        return _walk(node.body, env)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return float(node.value)
        raise ExpressionError("Only numeric constants are allowed.")
    if isinstance(node, ast.Name):
        if node.id in env:
            return env[node.id]
        if node.id in _CONSTS:
            return _CONSTS[node.id]
        raise ExpressionError(f"Unknown name {node.id!r}; use x, pi or e.")
    if isinstance(node, ast.UnaryOp):
        val = _walk(node.operand, env)
        if isinstance(node.op, ast.USub):
            return -val
        if isinstance(node.op, ast.UAdd):
            return +val
        raise ExpressionError("Unsupported unary operator.")
    if isinstance(node, ast.BinOp):
        left, right = _walk(node.left, env), _walk(node.right, env)
        op = node.op
        if isinstance(op, ast.Add):
            return left + right
        if isinstance(op, ast.Sub):
            return left - right
        if isinstance(op, ast.Mult):
            return left * right
        if isinstance(op, ast.Div):
            return left / right
        if isinstance(op, ast.Mod):
            return np.mod(left, right)
        if isinstance(op, ast.Pow):
            if isinstance(node.right, (ast.Constant, ast.UnaryOp)) and np.all(np.abs(right) > MAX_POWER):
                raise ExpressionError("Exponent too large.")
            return np.power(left, right)
        raise ExpressionError("Unsupported operator.")
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCS or node.keywords:
            raise ExpressionError("Unsupported function call.")
        if len(node.args) != 1:
            raise ExpressionError(f"{node.func.id}() takes one argument.")
        return _FUNCS[node.func.id](_walk(node.args[0], env))
    raise ExpressionError("Unsupported syntax in expression.")


def compile_expr(expr: str) -> Callable[[np.ndarray], np.ndarray]:
    """Return ``f(x)`` evaluating *expr* over a numpy array."""
    src = normalize(expr)
    # '|x|' absolute-value bars are not valid Python.
    if "|" in src:
        raise ExpressionError("Use abs(x) instead of |x|.")
    try:
        tree = ast.parse(src, mode="eval")
    except SyntaxError as exc:
        raise ExpressionError(f"Cannot parse expression {expr!r}.") from exc
    if sum(1 for _ in ast.walk(tree)) > MAX_NODES:
        raise ExpressionError("Expression is too complex.")
    _walk(tree, {"x": np.array([0.5])})  # dry-run so errors surface at validation time

    def f(x: np.ndarray) -> np.ndarray:
        with np.errstate(all="ignore"):
            y = _walk(tree, {"x": x})
        y = np.asarray(y, dtype=float)
        if y.shape != x.shape:
            y = np.full_like(x, float(y)) if y.ndim == 0 else np.broadcast_to(y, x.shape).copy()
        y[~np.isfinite(y)] = np.nan
        return y

    return f
