"""Code-side accuracy checks for model-written mathematics.

A wrong answer key is the worst failure an exam generator can have, and arithmetic is exactly where
language models slip. These checks use sympy (never `eval`) to re-do what can be re-done mechanically:

* every purely numeric equality written in a worked solution (``5^2 + 12^2 = 169``) is re-computed;
* the last value of the worked solution must agree with the stated final answer;
* a question that says "solve ... $equation$" is solved independently and compared with the answer
  (and, for multiple choice, with the option marked correct).

Anything the checker cannot parse is skipped, not guessed at, so it only ever reports a real
contradiction. It never claims a question is *correct*, only that no contradiction was found.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

import sympy

from app.diagrams.safe_expr import ExpressionError, normalize
from app.exam.schema import Answer, Question
from app.export.mathtext import split_math

_UNSUPPORTED = re.compile(
    r"\\(sin|cos|tan|cot|sec|csc|log|ln|lim|sum|int|prod|angle|circ|infty|ne|neq|leq|le|geq|ge|approx|in|cup|cap|vec|"
    r"overline|binom|begin|mathbb|dots|ldots|cdots|pm|mp|therefore|because|implies|rightarrow|Rightarrow)\b|[%<>]")
_SYMBOLS = set("xyztnabcmk")
_MAX_POWER = 20
_SOLVE_CUE = re.compile(r"\b(solve|find the roots?|roots? of|find the value of [a-z]\b|find [a-z]\b|find the solutions?)", re.I)
# "Solve" in the Indian languages; only the plain imperative, so a question that merely mentions an equation is not checked.
_SOLVE_CUE_INDIC = re.compile(r"हल\s*(?:कीजिए|करें|करो)|सोडवा|সমাধান\s*কর|தீர்க்க|సాధించండి|ಬಿಡಿಸಿ|ઉકેલો|ਹੱਲ\s*ਕਰੋ|ସମାଧାନ\s*କର")


@dataclass
class VerifyResult:
    checked: bool = False  # at least one mechanical check applied
    issues: List[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.issues:
            return "flagged"
        return "checked" if self.checked else ""


# ------------------------------------------------------------------ LaTeX -> sympy

def _latex_to_py(src: str) -> Optional[str]:
    s = src.strip()
    if not s or _UNSUPPORTED.search(s):
        return None
    s = re.sub(r"\\(left|right|displaystyle)\b", "", s)
    s = re.sub(r"\\[,;:! ]", " ", s)
    s = re.sub(r"\\(?:text|mathrm|mathbf|textbf|operatorname)\{[^{}]*\}", "", s)  # units such as \text{ cm}
    s = s.replace("\\times", "*").replace("\\cdot", "*").replace("\\div", "/").replace("\\pi", " pi ")
    for _ in range(6):
        s = re.sub(r"\\[dt]?frac\{([^{}]*)\}\{([^{}]*)\}", r"((\1)/(\2))", s)
        s = re.sub(r"\\sqrt\[(\d+)\]\{([^{}]*)\}", r"((\2)**(1/\1))", s)
        s = re.sub(r"\\sqrt\{([^{}]*)\}", r"sqrt(\1)", s)
        s = re.sub(r"\^\{([^{}]*)\}", r"**(\1)", s)
    s = re.sub(r"\^(\w+)", r"**\1", s).replace("{", "(").replace("}", ")")
    return None if "\\" in s else s


def _walk(node: ast.AST) -> sympy.Expr:
    if isinstance(node, ast.Expression):
        return _walk(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return sympy.Rational(str(node.value))
    if isinstance(node, ast.Name):
        if node.id == "pi":
            return sympy.pi
        if node.id == "e":
            return sympy.E
        if node.id in _SYMBOLS:
            return sympy.Symbol(node.id, real=True)
        raise ValueError(node.id)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        v = _walk(node.operand)
        return -v if isinstance(node.op, ast.USub) else v
    if isinstance(node, ast.BinOp):
        a, b = _walk(node.left), _walk(node.right)
        if isinstance(node.op, ast.Add):
            return a + b
        if isinstance(node.op, ast.Sub):
            return a - b
        if isinstance(node.op, ast.Mult):
            return a * b
        if isinstance(node.op, ast.Div):
            if b == 0:
                raise ValueError("division by zero")
            return a / b
        if isinstance(node.op, ast.Pow):
            if not b.is_number or abs(b) > _MAX_POWER:
                raise ValueError("power")
            return a ** b
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and len(node.args) == 1 and not node.keywords:
        arg = _walk(node.args[0])
        if node.func.id == "sqrt":
            return sympy.sqrt(arg)
        if node.func.id == "abs":
            return sympy.Abs(arg)
    raise ValueError("unsupported")


def parse_latex(src: str) -> Optional[sympy.Expr]:
    py = _latex_to_py(src)
    if not py or len(py) > 160:
        return None
    try:
        return _walk(ast.parse(normalize(py), mode="eval"))
    except (ValueError, SyntaxError, ExpressionError, RecursionError, ZeroDivisionError, TypeError):
        return None


def _value(expr: Optional[sympy.Expr]) -> Optional[float]:
    if expr is None or expr.free_symbols:
        return None
    try:
        v = complex(expr.evalf())
    except (TypeError, ValueError):
        return None
    return v.real if abs(v.imag) < 1e-12 else None


def _close(a: float, b: float) -> bool:
    # Loose enough to forgive "= 12.57" after rounding pi, tight enough to catch 25 vs 24.
    return abs(a - b) <= max(0.005 * max(abs(a), abs(b)), 0.011)


# ------------------------------------------------------------------ checks

def _chains(text: str) -> List[List[Optional[float]]]:
    out = []
    for kind, content in split_math(text or ""):
        if kind == "text" or "=" not in content:
            continue
        out.append([_value(parse_latex(p)) for p in content.split("=")])
    return out


def _equality_issues(text: str) -> Tuple[List[str], int]:
    issues, checked = [], 0
    for kind, content in split_math(text or ""):
        if kind == "text" or "=" not in content:
            continue
        vals = [_value(parse_latex(p)) for p in content.split("=")]
        for a, b in zip(vals, vals[1:]):
            if a is None or b is None:
                continue
            checked += 1
            if not _close(a, b):
                issues.append(f"The statement ${content.strip()}$ is arithmetically false ({a:g} is not {b:g}).")
                break
    return issues, checked


_FRACTION = re.compile(r"\\[dt]?frac\{(-?\d+)\}\{(\d+)\}")
_NUMBER = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?")


def _numbers(text: str) -> List[float]:
    text = _FRACTION.sub(lambda m: str(int(m.group(1)) / int(m.group(2))) if int(m.group(2)) else "", text or "")
    return [float(n) for n in _NUMBER.findall(text)]


def _solve_issues(question: str, claimed: str) -> Tuple[List[str], bool]:
    """Solve a single stated equation and compare real roots with the claimed answer."""
    if not (_SOLVE_CUE.search(question) or _SOLVE_CUE_INDIC.search(question)):
        return [], False
    equations = [c for k, c in split_math(question) if k != "text" and c.count("=") == 1]
    if len(equations) != 1 or len(equations[0]) > 120:
        return [], False
    lhs, rhs = (parse_latex(p) for p in equations[0].split("="))
    if lhs is None or rhs is None:
        return [], False
    symbols = (lhs - rhs).free_symbols
    if len(symbols) != 1:
        return [], False
    var = next(iter(symbols))
    poly = sympy.Poly(sympy.together(lhs - rhs), var) if (lhs - rhs).is_polynomial(var) else None
    if poly is None or not 1 <= poly.degree() <= 4:
        return [], False
    try:
        roots = [r for r in sympy.solve(sympy.Eq(lhs, rhs), var) if r.is_real]
    except (NotImplementedError, ValueError, TypeError):
        return [], False
    if not roots or any(not r.is_rational for r in roots):
        return [], False  # irrational roots are written many ways; do not guess
    expected = sorted(float(r) for r in roots)
    claimed_numbers = _numbers(claimed)
    missing = [r for r in expected if not any(_close(r, c) for c in claimed_numbers)]
    if missing:
        shown = ", ".join(f"{r:g}" for r in expected)
        return [f"Solving ${equations[0].strip()}$ independently gives {var} = {shown}, which does not match the "
                f"stated answer."], True
    return [], True


def _verify_block(text: str, options: Sequence[str], answer: Answer, qtype: str, include_solve: bool) -> VerifyResult:
    res = VerifyResult()
    blobs = [answer.text] + [s.text for s in answer.steps]
    for blob in blobs:
        issues, n = _equality_issues(blob)
        res.issues += issues
        res.checked = res.checked or n > 0
    # the last value reached by the worked solution must be the final answer
    last = None
    for s in reversed([x.text for x in answer.steps]):
        chains = [c for c in _chains(s) if c and c[-1] is not None]
        if chains:
            last = chains[-1][-1]
            break
    stated = _numbers(answer.text)
    if last is not None and len(stated) == 1 and not _close(last, stated[0]) and not res.issues:
        res.issues.append(f"The worked solution ends at {last:g} but the stated answer is {stated[0]:g}.")
    if include_solve:
        claimed = answer.text
        if qtype in ("mcq",) and answer.correct_option is not None and 0 <= answer.correct_option < len(options):
            claimed = options[answer.correct_option]
        issues, ran = _solve_issues(text, claimed)
        res.issues += issues
        res.checked = res.checked or ran
    return res


def verify_question(q: Question) -> VerifyResult:
    total = _verify_block(q.text, q.options, q.answer, q.type, include_solve=True)
    for sub in q.sub_questions:
        r = _verify_block(sub.text, sub.options, sub.answer, "mcq" if sub.options else "short", include_solve=True)
        total.issues += r.issues
        total.checked = total.checked or r.checked
    if q.alternative:
        alt = q.alternative
        r = _verify_block(alt.text, alt.options, alt.answer, q.type, include_solve=True)
        total.issues += [f"(OR choice) {i}" for i in r.issues]
        total.checked = total.checked or r.checked
    return total
