"""LaTeX -> image rendering for PDF/DOCX, plus a plain-text fallback for expressions mathtext cannot parse."""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import List, Optional, Tuple

from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.font_manager import FontProperties

from app import languages

from app.diagrams.render import MPL_LOCK

_MATH_RE = re.compile(r"(?<!\\)\$\$(.+?)(?<!\\)\$\$|(?<!\\)\$(.+?)(?<!\\)\$", re.S)
_PAREN_MATH = re.compile(r"\\\((.+?)\\\)", re.S)
_BRACKET_MATH = re.compile(r"\\\[(.+?)\\\]", re.S)

Segment = Tuple[str, str]  # (kind, content) kind in text|math|display


def split_math(text: str) -> List[Segment]:
    text = _BRACKET_MATH.sub(lambda m: "$$" + m.group(1) + "$$", _PAREN_MATH.sub(lambda m: "$" + m.group(1) + "$", text))
    out: List[Segment] = []
    pos = 0
    for m in _MATH_RE.finditer(text):
        if m.start() > pos:
            out.append(("text", text[pos:m.start()].replace("\\$", "$")))
        if m.group(1) is not None:
            out.append(("display", m.group(1).strip()))
        else:
            out.append(("math", m.group(2).strip()))
        pos = m.end()
    if pos < len(text):
        out.append(("text", text[pos:].replace("\\$", "$")))
    return out


_UNICODE = [
    (r"\\times", "×"), (r"\\div", "÷"), (r"\\pm", "±"), (r"\\cdot", "·"), (r"\\leq?", "≤"), (r"\\geq?", "≥"),
    (r"\\neq", "≠"), (r"\\approx", "≈"), (r"\\infty", "∞"), (r"\\pi", "π"), (r"\\theta", "θ"), (r"\\alpha", "α"),
    (r"\\beta", "β"), (r"\\gamma", "γ"), (r"\\delta", "δ"), (r"\\lambda", "λ"), (r"\\mu", "μ"), (r"\\sigma", "σ"),
    (r"\\omega", "ω"), (r"\\Delta", "Δ"), (r"\\Omega", "Ω"), (r"\\circ", "°"), (r"\\angle", "∠"), (r"\\sum", "Σ"),
    (r"\\int", "∫"), (r"\\rightarrow|\\to", "→"), (r"\\Rightarrow|\\implies", "⇒"), (r"\\in", "∈"), (r"\\cup", "∪"),
    (r"\\cap", "∩"), (r"\\therefore", "∴"), (r"\\dots|\\ldots|\\cdots", "…"), (r"\\setminus", "∖"),
    (r"\\left|\\right", ""), (r"\\,|\\;|\\!|\\quad", " "),
]
_SUP = str.maketrans("0123456789+-n", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻ⁿ")
_SUB = str.maketrans("0123456789+-", "₀₁₂₃₄₅₆₇₈₉₊₋")


def _flatten_environments(src: str) -> str:
    def rows(m):
        body = re.sub(r"\\\\\s*$", "", m.group(2).strip())
        lines = [" ".join(c.strip() for c in r.split("&")) for r in re.split(r"\\\\", body)]
        return "[" + "; ".join(lines) + "]"

    return _ENV.sub(rows, src)


def latex_to_text(src: str) -> str:
    s = _flatten_environments(src)
    for _ in range(4):
        s = re.sub(r"\\d?frac\{([^{}]*)\}\{([^{}]*)\}", r"(\1)/(\2)", s)
        s = re.sub(r"\\sqrt\[(\d+)\]\{([^{}]*)\}", r"\1√(\2)", s)
        s = re.sub(r"\\sqrt\{([^{}]*)\}", r"√(\1)", s)
        s = re.sub(r"\\(?:text|mathrm|mathbf|operatorname)\{([^{}]*)\}", r"\1", s)
    for pat, rep in _UNICODE:
        s = re.sub(pat, rep, s)
    s = re.sub(r"\^\{([^{}]*)\}", lambda m: m.group(1).translate(_SUP) if m.group(1).translate(_SUP) != m.group(1) or len(m.group(1)) == 1 else "^(" + m.group(1) + ")", s)
    s = re.sub(r"\^(\w)", lambda m: m.group(1).translate(_SUP), s)
    s = re.sub(r"_\{([^{}]*)\}", lambda m: m.group(1).translate(_SUB), s)
    s = re.sub(r"_(\w)", lambda m: m.group(1).translate(_SUB), s)
    s = re.sub(r"\\([A-Za-z]+)", r"\1", s).replace("{", "").replace("}", "")
    return re.sub(r"\s+", " ", s).strip()


def plain_text(text: str) -> str:
    return "".join(c if k == "text" else latex_to_text(c) for k, c in split_math(text))


def _prepare(latex: str) -> str:
    s = latex.strip()
    s = re.sub(r"\\displaystyle|\\limits", "", s)
    s = re.sub(r"\\operatorname\{([^{}]*)\}", r"\\mathrm{\1}", s)
    s = re.sub(r"\\text(?:bf|rm|it)?\{([^{}]*)\}", lambda m: "\\mathrm{" + m.group(1).replace(" ", "\\ ") + "}", s)
    s = re.sub(r"\\(?:,|;|:|!|quad|qquad)", r"\\ ", s)
    s = s.replace("\\%", "%")
    s = re.sub(r"\\le(?![A-Za-z])", r"\\leq", s)
    s = re.sub(r"\\ge(?![A-Za-z])", r"\\geq", s)
    return s


_ENV = re.compile(r"\\begin\{(pmatrix|bmatrix|vmatrix|Vmatrix|Bmatrix|matrix|cases|aligned|array)\}(?:\{[^}]*\})?(.*?)\\end\{\1\}",
                  re.S)
_BRACKETS = {"pmatrix": "paren", "bmatrix": "square", "vmatrix": "bar", "Vmatrix": "dbar", "Bmatrix": "brace",
             "cases": "brace-left"}


def _cell(text: str) -> str:
    t = _prepare(text)
    return "$" + t + "$" if t else ""


def _draw_bracket(fig, kind: str, x: float, y0: float, y1: float, left: bool, W: float, H: float, size_px: float,
                  lw: float) -> None:
    """Draw a stretchy delimiter (pixel coordinates, origin bottom-left) spanning y0..y1."""
    from matplotlib.lines import Line2D

    depth = max(size_px * 0.28, 3.0)
    sign = 1 if left else -1
    pts = []
    if kind == "paren":
        n = 12
        for i in range(n + 1):
            t = i / n
            bulge = depth * (1 - (2 * t - 1) ** 2) * 0.9
            pts.append((x + sign * (depth * 0.9 - bulge), y0 + (y1 - y0) * t))
    elif kind == "square":
        pts = [(x + sign * depth, y0), (x, y0), (x, y1), (x + sign * depth, y1)]
    elif kind in ("bar", "dbar"):
        pts = [(x, y0), (x, y1)]
    else:  # brace
        shape = [(1, 0), (0.6, 0.02), (0.5, 0.1), (0.5, 0.4), (0.5, 0.45), (0.15, 0.5), (0.5, 0.55), (0.5, 0.6),
                 (0.5, 0.9), (0.6, 0.98), (1, 1)]
        pts = [(x + sign * depth * 1.4 * px, y0 + (y1 - y0) * py) for px, py in shape]
    lines = [pts]
    if kind == "dbar":
        lines.append([(x + sign * depth * 0.5, y) for _, y in pts])
    for line in lines:
        fig.add_artist(Line2D([px / W for px, _ in line], [py / H for _, py in line], transform=fig.transFigure,
                              color="#111111", linewidth=lw, solid_capstyle="round"))


def _render_composite(latex: str, size_pt: float, dpi: int) -> Optional["MathImage"]:
    """Matrices, cases and aligned blocks, which mathtext cannot typeset: each cell is a mathtext image on a grid."""
    import io

    pad = 2
    with MPL_LOCK:
        fig = Figure(dpi=dpi)
        canvas = FigureCanvasAgg(fig)
        fp = FontProperties(size=size_pt)
        renderer = canvas.get_renderer()
        size_px = size_pt * dpi / 72.0
        axis = 0.28 * size_px  # height of the maths axis above the baseline

        def measure(expr: str):
            if not expr:
                return 0.0, 0.0, 0.0
            return renderer.get_text_width_height_descent(expr, fp, ismath=True)

        parts = []  # (kind, payload, width, up, down)
        pos = 0
        for m in _ENV.finditer(latex):
            if latex[pos:m.start()].strip():
                expr = _cell(latex[pos:m.start()])
                w, h, d = measure(expr)
                parts.append(("tex", expr, w, h - d, d))
            name, body = m.group(1), m.group(2)
            raw_rows = [r for r in re.split(r"\\\\", body) if r.strip()]
            grid = [[_cell(c) for c in r.split("&")] for r in raw_rows]
            ncol = max(len(r) for r in grid)
            grid = [r + [""] * (ncol - len(r)) for r in grid]
            metrics = [[measure(c) for c in r] for r in grid]
            col_w = [max(metrics[r][c][0] for r in range(len(grid))) for c in range(ncol)]
            asc = [max(mt[1] - mt[2] for mt in row) for row in metrics]
            desc = [max(mt[2] for mt in row) for row in metrics]
            row_gap, col_gap = 0.45 * size_px, 1.1 * size_px
            height = sum(a + d for a, d in zip(asc, desc)) + row_gap * (len(grid) - 1)
            inner = sum(col_w) + col_gap * (ncol - 1)
            kind = _BRACKETS.get(name)
            margin = 0.8 * size_px if kind else 0.0
            width = inner + 2 * margin if kind != "brace-left" else inner + 1.2 * margin
            align = ["right" if name == "aligned" and c == 0 else ("left" if name in ("cases", "aligned") else "center")
                     for c in range(ncol)]
            parts.append(("env", (grid, metrics, col_w, asc, desc, row_gap, col_gap, kind, margin, align, name),
                          width, axis + height / 2 + 0.12 * size_px, height / 2 - axis + 0.12 * size_px))
            pos = m.end()
        if latex[pos:].strip():
            expr = _cell(latex[pos:])
            w, h, d = measure(expr)
            parts.append(("tex", expr, w, h - d, d))
        if not parts:
            return None
        gap = 0.18 * size_px
        total_w = sum(p[2] for p in parts) + gap * (len(parts) - 1)
        up, down = max(p[3] for p in parts), max(max(p[4] for p in parts), 0.0)
        W, H = int(total_w + 2 * pad + 2), int(up + down + 2 * pad + 2)
        fig.set_size_inches(W / dpi, H / dpi)
        fig.patch.set_alpha(0)
        base = pad + down  # baseline height from the bottom edge
        x = float(pad)
        for kind, payload, width, _up, _down in parts:
            if kind == "tex":
                fig.text(x / W, base / H, payload, fontproperties=fp, va="baseline", ha="left", color="#111111")
            else:
                grid, metrics, col_w, asc, desc, row_gap, col_gap, bracket, margin, align, name = payload
                block_h = sum(a + d for a, d in zip(asc, desc)) + row_gap * (len(grid) - 1)
                top = base + axis + block_h / 2
                left_edge = x + (margin if bracket and bracket != "brace-left" else (1.2 * margin if bracket else 0))
                y = top
                for r, row in enumerate(grid):
                    baseline = y - asc[r]
                    cx = left_edge
                    for c, expr in enumerate(row):
                        if expr:
                            ax = {"left": cx, "center": cx + col_w[c] / 2, "right": cx + col_w[c]}[align[c]]
                            fig.text(ax / W, baseline / H, expr, fontproperties=fp, va="baseline", ha=align[c],
                                     color="#111111")
                        cx += col_w[c] + col_gap
                    y = baseline - desc[r] - row_gap
                if bracket:
                    y0, y1 = top - block_h - 0.1 * size_px, top + 0.1 * size_px
                    lw = max(0.6, size_pt * 0.06)
                    _draw_bracket(fig, "brace" if bracket == "brace-left" else bracket, x + 0.3 * size_px, y0, y1,
                                  True, W, H, size_px, lw)
                    if bracket not in ("brace-left",):
                        rkind = "brace" if bracket == "brace" else bracket
                        _draw_bracket(fig, rkind, x + width - 0.3 * size_px, y0, y1, False, W, H, size_px, lw)
            x += width + gap
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=dpi, transparent=True)
    to_pt = 72.0 / dpi
    return MathImage(buf.getvalue(), W * to_pt, H * to_pt, base * to_pt)


@dataclass(frozen=True)
class MathImage:
    png: bytes
    width_pt: float
    height_pt: float
    drop_pt: float  # how far the image extends below the text baseline


@lru_cache(maxsize=4096)
def render_math(latex: str, size_pt: float = 11.0, dpi: int = 300) -> Optional[MathImage]:
    if languages.has_indic(latex):  # mathtext has no Indic fonts or shaping; the caller falls back to plain text
        return None
    if _ENV.search(latex):
        try:
            return _render_composite(latex, size_pt, dpi)
        except Exception:
            return None
    expr = "$" + _prepare(latex) + "$"
    pad = 2
    try:
        with MPL_LOCK:
            fig = Figure(dpi=dpi)
            canvas = FigureCanvasAgg(fig)
            fp = FontProperties(size=size_pt)
            renderer = canvas.get_renderer()
            w, h, d = renderer.get_text_width_height_descent(expr, fp, ismath=True)
            W, H = int(w + 2 * pad + 2), int(h + 2 * pad + 2)
            fig.set_size_inches(W / dpi, H / dpi)
            fig.patch.set_alpha(0)
            fig.text(pad / W, (pad + d) / H, expr, fontproperties=fp, va="baseline", ha="left", color="#111111")
            import io

            buf = io.BytesIO()
            fig.savefig(buf, format="png", dpi=dpi, transparent=True)
    except Exception:
        return None
    to_pt = 72.0 / dpi
    return MathImage(buf.getvalue(), W * to_pt, H * to_pt, (pad + d) * to_pt)
