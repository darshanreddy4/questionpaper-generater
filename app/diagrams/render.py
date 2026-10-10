"""Deterministic diagram renderer.

Models (or teachers) describe a figure as a small JSON document; this module draws it with
matplotlib. A declarative spec is used instead of model-written drawing code so that output is
reproducible, validated, and safe to render from untrusted content.

Supported ``type`` values: ``geometry``, ``plot``, ``chart``, ``venn``, ``number_line``.
"""
from __future__ import annotations

import io
import json
import math
import re
import threading
from typing import Any, Dict, List, NamedTuple, Optional, Sequence, Tuple

import matplotlib

matplotlib.use("Agg")
import numpy as np  # noqa: E402
from matplotlib.backends.backend_agg import FigureCanvasAgg  # noqa: E402
from matplotlib.colors import is_color_like  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402
from matplotlib.font_manager import FontProperties  # noqa: E402
from matplotlib.mathtext import MathTextParser  # noqa: E402
from matplotlib.patches import Arc, Circle, Ellipse, FancyArrowPatch, Polygon, Rectangle  # noqa: E402
from matplotlib.ticker import MaxNLocator  # noqa: E402

from app.diagrams.safe_expr import ExpressionError, compile_expr
from app.languages import INDIC_CHAR  # noqa: E402

matplotlib.rcParams.update({"mathtext.fontset": "stix", "font.family": "STIXGeneral", "font.size": 10,
                           "svg.hashsalt": "examforge"})  # fixed salt keeps SVG ids stable between renders

# pyparsing grammars inside matplotlib's mathtext are shared state, so all drawing is serialised.
MPL_LOCK = threading.RLock()

DIAGRAM_TYPES = ("geometry", "plot", "chart", "venn", "number_line")
MAX_SPEC_BYTES = 40_000
MAX_ITEMS = 200
INK = "#111111"
PALETTE = ["#111111", "#1f4e9c", "#c0392b", "#2e7d32", "#8e44ad", "#d35400"]
_LINESTYLES = {"solid": "-", "dashed": "--", "dotted": ":", "dashdot": "-."}


class DiagramError(ValueError):
    """The diagram spec is malformed or cannot be drawn."""


class RenderedImage(NamedTuple):
    data: bytes
    width_in: float
    height_in: float


# ------------------------------------------------------------------ helpers

def _num(value: Any, what: str) -> float:
    try:
        f = float(value)
    except (TypeError, ValueError):
        raise DiagramError(f"{what} must be a number, got {value!r}.")
    if not math.isfinite(f):
        raise DiagramError(f"{what} must be finite.")
    return f


def _xy(value: Any, what: str) -> Tuple[float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise DiagramError(f"{what} must be an [x, y] pair, got {value!r}.")
    return _num(value[0], what), _num(value[1], what)


def _color(value: Any, default: str = INK) -> str:
    return value if isinstance(value, str) and is_color_like(value) else default


def _linestyle(value: Any) -> str:
    return _LINESTYLES.get(str(value or "solid").lower(), "-")


def _lim(spec: Dict[str, Any], key: str, default: Optional[List[float]]) -> Optional[List[float]]:
    val = spec.get(key)
    if val is None:
        return default
    lo, hi = _xy(val, key)
    if lo >= hi:
        raise DiagramError(f"{key} must be increasing.")
    return [lo, hi]


_MT_PARSER: Optional[MathTextParser] = None


def safe_label(text: Any) -> str:
    """Return *text* with unparseable ``$...$`` math degraded to plain text instead of crashing."""
    s = "" if text is None else str(text)
    if "$" not in s:
        return s
    global _MT_PARSER
    if len(re.findall(r"(?<!\\)\$", s)) % 2:
        return s.replace("$", "")
    try:
        with MPL_LOCK:
            if _MT_PARSER is None:
                _MT_PARSER = MathTextParser("agg")
            _MT_PARSER.parse(s, dpi=72, prop=FontProperties())
        return s
    except Exception:
        return s.replace("$", "")


class _Bounds:
    def __init__(self) -> None:
        self.x0 = self.y0 = math.inf
        self.x1 = self.y1 = -math.inf

    def add(self, x: float, y: float) -> None:
        self.x0, self.x1 = min(self.x0, x), max(self.x1, x)
        self.y0, self.y1 = min(self.y0, y), max(self.y1, y)

    @property
    def empty(self) -> bool:
        return self.x0 > self.x1

    @property
    def span(self) -> float:
        return max(self.x1 - self.x0, self.y1 - self.y0, 1e-6)


def _unit(dx: float, dy: float) -> Tuple[float, float]:
    n = math.hypot(dx, dy)
    return (dx / n, dy / n) if n > 1e-12 else (0.0, 1.0)


def _tick_marks(ax, a, b, count: int, size: float) -> None:
    ux, uy = _unit(b[0] - a[0], b[1] - a[1])
    nx, ny = -uy, ux
    mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
    for i in range(count):
        off = (i - (count - 1) / 2) * size * 0.8
        cx, cy = mx + ux * off, my + uy * off
        ax.plot([cx - nx * size / 2, cx + nx * size / 2], [cy - ny * size / 2, cy + ny * size / 2], color=INK, lw=1.0)


# ------------------------------------------------------------------ geometry

def _draw_geometry(fig: Figure, spec: Dict[str, Any]) -> float:
    ax = fig.add_subplot(111)
    ax.set_aspect("equal")
    ax.axis("off")
    pts: Dict[str, Tuple[float, float]] = {}
    raw_points = spec.get("points") or {}
    if not isinstance(raw_points, dict):
        raise DiagramError("geometry.points must be an object mapping names to [x, y].")
    for name, xy in raw_points.items():
        pts[str(name)] = _xy(xy, f"point {name}")
    shapes = spec.get("shapes") or []
    if not isinstance(shapes, list) or len(shapes) > MAX_ITEMS:
        raise DiagramError("geometry.shapes must be a list with at most %d items." % MAX_ITEMS)

    def P(ref: Any) -> Tuple[float, float]:
        if isinstance(ref, str):
            if ref not in pts:
                raise DiagramError(f"Unknown point {ref!r}; define it under 'points'.")
            return pts[ref]
        return _xy(ref, "coordinate")

    bounds = _Bounds()
    for x, y in pts.values():
        bounds.add(x, y)
    for sh in shapes:
        if not isinstance(sh, dict):
            raise DiagramError("Each shape must be an object.")
        if sh.get("kind") == "circle":
            cx, cy = P(sh.get("center"))
            r = _num(sh.get("radius"), "circle radius")
            if r <= 0:
                raise DiagramError("circle radius must be positive.")
            for dx, dy in ((r, 0), (-r, 0), (0, r), (0, -r)):
                bounds.add(cx + dx, cy + dy)
        elif sh.get("kind") == "ellipse":
            cx, cy = P(sh.get("center"))
            w, h = _num(sh.get("width"), "ellipse width"), _num(sh.get("height"), "ellipse height")
            m = max(w, h) / 2
            bounds.add(cx - m, cy - m)
            bounds.add(cx + m, cy + m)
        elif sh.get("kind") in ("text", "polygon", "rect", "segment", "arrow"):
            for key in ("at", "from", "to"):
                if key in sh:
                    bounds.add(*P(sh[key]))
            for ref in sh.get("points", []) if sh.get("kind") == "polygon" else []:
                bounds.add(*P(ref))
    if bounds.empty:
        raise DiagramError("Geometry diagram contains no points or shapes.")
    span = bounds.span
    centroid = ((bounds.x0 + bounds.x1) / 2, (bounds.y0 + bounds.y1) / 2)
    if pts:
        centroid = (sum(p[0] for p in pts.values()) / len(pts), sum(p[1] for p in pts.values()) / len(pts))
    pad = 0.18 * span
    ax.set_xlim(bounds.x0 - pad, bounds.x1 + pad)
    ax.set_ylim(bounds.y0 - pad, bounds.y1 + pad)
    label_gap = 0.045 * span

    def outward(p: Tuple[float, float], dist: float) -> Tuple[float, float]:
        dx, dy = p[0] - centroid[0], p[1] - centroid[1]
        if math.hypot(dx, dy) < 1e-9:
            dx, dy = -1.0, -1.0  # a point at the centroid (e.g. a circle's centre) is labelled below-left
        ux, uy = _unit(dx, dy)
        return p[0] + ux * dist, p[1] + uy * dist

    lw = 1.4
    for sh in shapes:
        kind = sh.get("kind")
        style = _linestyle(sh.get("style"))
        color = _color(sh.get("color"))
        label = safe_label(sh.get("label"))
        if kind == "polygon":
            ring = [P(r) for r in sh.get("points", [])]
            if len(ring) < 3:
                raise DiagramError("polygon needs at least 3 points.")
            fill = sh.get("fill")
            face = _color(fill, "#e6edf7") if fill else "none"
            ax.add_patch(Polygon(ring, closed=True, fill=bool(fill), facecolor=face, edgecolor=color,
                                 linewidth=lw, linestyle=style))
            if label:
                cx = sum(p[0] for p in ring) / len(ring)
                cy = sum(p[1] for p in ring) / len(ring)
                ax.text(cx, cy, label, ha="center", va="center")
        elif kind in ("segment", "arrow"):
            a, b = P(sh.get("from")), P(sh.get("to"))
            if kind == "arrow":
                ax.add_patch(FancyArrowPatch(a, b, arrowstyle="-|>", mutation_scale=11, color=color,
                                             linewidth=lw, linestyle=style, shrinkA=0, shrinkB=0))
            else:
                ax.plot([a[0], b[0]], [a[1], b[1]], color=color, lw=lw, ls=style, solid_capstyle="round")
            if sh.get("ticks"):
                _tick_marks(ax, a, b, int(_num(sh["ticks"], "ticks")), 0.035 * span)
            if label:
                mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
                ux, uy = _unit(b[0] - a[0], b[1] - a[1])
                nx, ny = -uy, ux
                if nx * (mid[0] - centroid[0]) + ny * (mid[1] - centroid[1]) < 0:
                    nx, ny = -nx, -ny
                ax.text(mid[0] + nx * label_gap * 1.3, mid[1] + ny * label_gap * 1.3, label,
                        ha="center", va="center")
        elif kind == "line":
            through = sh.get("through")
            if not isinstance(through, list) or len(through) != 2:
                raise DiagramError("line needs 'through': [pointA, pointB].")
            a, b = P(through[0]), P(through[1])
            ux, uy = _unit(b[0] - a[0], b[1] - a[1])
            big = 4 * span
            ax.plot([a[0] - ux * big, a[0] + ux * big], [a[1] - uy * big, a[1] + uy * big],
                    color=color, lw=lw, ls=style)
            if label:
                ax.text(*outward(b, label_gap * 1.5), label, ha="center", va="center")
        elif kind == "circle":
            c = P(sh.get("center"))
            r = _num(sh.get("radius"), "circle radius")
            fill = sh.get("fill")
            ax.add_patch(Circle(c, r, fill=bool(fill), facecolor=_color(fill, "#e6edf7") if fill else "none",
                                edgecolor=color, linewidth=lw, linestyle=style))
            if label:
                at = P(sh["label_at"]) if "label_at" in sh else (c[0], c[1] + r * 0.25)
                ax.text(at[0], at[1], label, ha="center", va="center")
        elif kind == "ellipse":
            c = P(sh.get("center"))
            ax.add_patch(Ellipse(c, _num(sh.get("width"), "width"), _num(sh.get("height"), "height"),
                                 angle=_num(sh.get("angle", 0), "angle"), fill=False, edgecolor=color,
                                 linewidth=lw, linestyle=style))
            if label:
                ax.text(c[0], c[1], label, ha="center", va="center")
        elif kind == "rect":
            a, b = P(sh.get("from")), P(sh.get("to"))
            fill = sh.get("fill")
            ax.add_patch(Rectangle((min(a[0], b[0]), min(a[1], b[1])), abs(b[0] - a[0]), abs(b[1] - a[1]),
                                   fill=bool(fill), facecolor=_color(fill, "#e6edf7") if fill else "none",
                                   edgecolor=color, linewidth=lw, linestyle=style))
            if label:
                ax.text((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, label, ha="center", va="center")
        elif kind == "arc":
            c = P(sh.get("center"))
            r = _num(sh.get("radius"), "arc radius")
            t1, t2 = _num(sh.get("from_deg"), "from_deg"), _num(sh.get("to_deg"), "to_deg")
            ax.add_patch(Arc(c, 2 * r, 2 * r, theta1=t1, theta2=t2, edgecolor=color, linewidth=lw, linestyle=style))
            if label:
                mid = math.radians((t1 + t2) / 2)
                ax.text(c[0] + 1.18 * r * math.cos(mid), c[1] + 1.18 * r * math.sin(mid), label,
                        ha="center", va="center")
        elif kind == "angle":
            v, a, b = P(sh.get("at")), P(sh.get("from")), P(sh.get("to"))
            a1 = math.degrees(math.atan2(a[1] - v[1], a[0] - v[0]))
            a2 = math.degrees(math.atan2(b[1] - v[1], b[0] - v[0]))
            d = (a2 - a1 + 180) % 360 - 180
            t1, t2 = (a1, a1 + d) if d >= 0 else (a1 + d, a1)
            r = _num(sh.get("radius", 0.11 * span), "angle radius")
            ax.add_patch(Arc(v, 2 * r, 2 * r, theta1=t1, theta2=t2, edgecolor=color, linewidth=1.1))
            if label:
                mid = math.radians((t1 + t2) / 2)
                ax.text(v[0] + 1.7 * r * math.cos(mid), v[1] + 1.7 * r * math.sin(mid), label,
                        ha="center", va="center", fontsize=9)
        elif kind == "right_angle":
            v, a, b = P(sh.get("at")), P(sh.get("from")), P(sh.get("to"))
            s = 0.06 * span
            ux, uy = _unit(a[0] - v[0], a[1] - v[1])
            wx, wy = _unit(b[0] - v[0], b[1] - v[1])
            ax.plot([v[0] + ux * s, v[0] + (ux + wx) * s, v[0] + wx * s],
                    [v[1] + uy * s, v[1] + (uy + wy) * s, v[1] + wy * s], color=color, lw=1.1)
        elif kind == "text":
            at = P(sh.get("at"))
            ax.text(at[0], at[1], safe_label(sh.get("text") or label), ha="center", va="center")
        else:
            raise DiagramError(f"Unknown geometry shape kind {kind!r}.")

    if spec.get("show_points", True):
        for name, p in pts.items():
            if name.startswith("_"):
                continue
            ax.plot([p[0]], [p[1]], "o", color=INK, ms=3.2, zorder=5)
            if spec.get("label_points", True):
                lx, ly = outward(p, label_gap)
                ax.text(lx, ly, safe_label(name), ha="center", va="center", fontsize=11, zorder=6)
    return (bounds.y1 - bounds.y0 + 2 * pad) / max(bounds.x1 - bounds.x0 + 2 * pad, 1e-6)


# ------------------------------------------------------------------ plot

def _style_axes(ax, xlim: List[float], ylim: List[float], spec: Dict[str, Any]) -> None:
    labels = spec.get("axis_labels") or ["x", "y"]
    xl, yl = (safe_label(labels[0]), safe_label(labels[1])) if len(labels) >= 2 else ("x", "y")
    ax.set_xlim(*xlim)
    ax.set_ylim(*ylim)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    zero_x = xlim[0] < 0 < xlim[1]
    zero_y = ylim[0] < 0 < ylim[1]
    if zero_x:
        ax.spines["left"].set_position("zero")
    if zero_y:
        ax.spines["bottom"].set_position("zero")
    for side in ("left", "bottom"):
        ax.spines[side].set_linewidth(1.1)
    x_axis_y = 0 if zero_y else ylim[0]
    y_axis_x = 0 if zero_x else xlim[0]
    ax.plot([xlim[1]], [x_axis_y], ">", color=INK, ms=4, clip_on=False, zorder=10)
    ax.plot([y_axis_x], [ylim[1]], "^", color=INK, ms=4, clip_on=False, zorder=10)
    ax.annotate(xl, (xlim[1], x_axis_y), xytext=(5, -3), textcoords="offset points", va="top", fontsize=11)
    ax.annotate(yl, (y_axis_x, ylim[1]), xytext=(6, 0), textcoords="offset points", va="center", fontsize=11)
    ax.xaxis.set_major_locator(MaxNLocator(integer=(xlim[1] - xlim[0]) <= 24, nbins=10))
    ax.yaxis.set_major_locator(MaxNLocator(integer=(ylim[1] - ylim[0]) <= 24, nbins=10))
    ax.tick_params(labelsize=8, length=3)
    if spec.get("grid", True):
        ax.grid(True, color="#cfcfcf", linewidth=0.5, linestyle=":")
        ax.set_axisbelow(True)


def _draw_plot(fig: Figure, spec: Dict[str, Any]) -> float:
    ax = fig.add_subplot(111)
    xlim = _lim(spec, "xlim", [-5.0, 5.0])
    funcs = spec.get("functions") or []
    if not isinstance(funcs, list) or len(funcs) > 8:
        raise DiagramError("plot.functions must be a list of at most 8 items.")
    compiled = []
    for f in funcs:
        if not isinstance(f, dict) or "expr" not in f:
            raise DiagramError("Each function needs an 'expr'.")
        try:
            fn = compile_expr(str(f["expr"]))
        except ExpressionError as exc:
            raise DiagramError(str(exc))
        dom = _lim(f, "domain", xlim)
        xs = np.linspace(dom[0], dom[1], 1500)
        compiled.append((f, xs, fn(xs)))
    ylim = _lim(spec, "ylim", None)
    if ylim is None:
        vals = np.concatenate([y[np.isfinite(y)] for _, _, y in compiled]) if compiled else np.array([])
        extra = [p["y"] for p in spec.get("points", []) if isinstance(p, dict) and "y" in p]
        if vals.size:
            lo, hi = np.percentile(vals, 2), np.percentile(vals, 98)
            pad = max((hi - lo) * 0.1, 0.5)
            ylim = [float(lo - pad), float(hi + pad)]
            if extra:
                ylim = [min(ylim[0], min(extra) - 1), max(ylim[1], max(extra) + 1)]
        else:
            ylim = [min([-5.0] + extra), max([5.0] + extra)] if extra else [-5.0, 5.0]
        if ylim[1] - ylim[0] < 1e-6:
            ylim = [ylim[0] - 1, ylim[1] + 1]
    _style_axes(ax, xlim, ylim, spec)
    if spec.get("equal_aspect"):
        ax.set_aspect("equal", adjustable="box")
    yrange = ylim[1] - ylim[0]

    for fill in spec.get("fills") or []:
        try:
            f1 = compile_expr(str(fill.get("expr1", "0")))
            f2 = compile_expr(str(fill.get("expr2", "0")))
        except (ExpressionError, AttributeError) as exc:
            raise DiagramError(str(exc))
        dom = _lim(fill, "domain", xlim)
        xs = np.linspace(dom[0], dom[1], 800)
        ax.fill_between(xs, f1(xs), f2(xs), color=_color(fill.get("color"), "#9db7e0"), alpha=0.45, linewidth=0)
    for poly in spec.get("polygons") or []:
        ring = [_xy(p, "polygon point") for p in poly.get("points", [])]
        if len(ring) < 3:
            raise DiagramError("plot polygon needs at least 3 points.")
        fill = poly.get("fill", True)
        ax.add_patch(Polygon(ring, closed=True, fill=bool(fill), facecolor=_color(poly.get("color"), "#9db7e0"),
                             alpha=0.45 if fill else 1, edgecolor=INK, linewidth=1.2))
    handles = False
    for i, (f, xs, ys) in enumerate(compiled):
        ys = ys.copy()
        ys[np.abs(ys) > 1e4] = np.nan
        jump = np.abs(np.diff(ys)) > 4 * yrange
        ys[1:][jump] = np.nan
        ax.plot(xs, ys, color=_color(f.get("color"), PALETTE[i % len(PALETTE)]), lw=1.6,
                ls=_linestyle(f.get("style")), label=safe_label(f.get("label")) or None)
        handles = handles or bool(f.get("label"))
    for ln in spec.get("lines") or []:
        a, b = _xy(ln.get("from"), "line start"), _xy(ln.get("to"), "line end")
        style = _linestyle(ln.get("style"))
        if ln.get("arrow"):
            ax.add_patch(FancyArrowPatch(a, b, arrowstyle="-|>", mutation_scale=10, color=INK, lw=1.3, ls=style))
        else:
            ax.plot([a[0], b[0]], [a[1], b[1]], color=_color(ln.get("color")), lw=1.4, ls=style,
                    label=safe_label(ln.get("label")) or None)
            handles = handles or bool(ln.get("label"))
    for p in spec.get("points") or []:
        x, y = _num(p.get("x"), "point x"), _num(p.get("y"), "point y")
        ax.plot([x], [y], "o", ms=4.5, mfc="white" if p.get("open") else INK, mec=INK, zorder=6)
        if p.get("label"):
            ax.annotate(safe_label(p["label"]), (x, y), xytext=(5, 5), textcoords="offset points", fontsize=10)
    for t in spec.get("texts") or []:
        ax.text(_num(t.get("x"), "text x"), _num(t.get("y"), "text y"), safe_label(t.get("text")), fontsize=10)
    if handles:
        ax.legend(frameon=True, framealpha=0.92, edgecolor="none", fontsize=9, loc="best")
    return 0.8


# ------------------------------------------------------------------ chart

def _draw_chart(fig: Figure, spec: Dict[str, Any]) -> float:
    ax = fig.add_subplot(111)
    kind = str(spec.get("kind", "bar")).lower()
    cats = [safe_label(c) for c in spec.get("categories", [])]
    series = spec.get("series") or []
    if kind == "scatter":
        pts = [_xy(p, "scatter point") for p in spec.get("points", [])]
        if not pts:
            raise DiagramError("scatter chart needs 'points'.")
        xs, ys = np.array([p[0] for p in pts]), np.array([p[1] for p in pts])
        ax.scatter(xs, ys, color=INK, s=14, zorder=5)
        if spec.get("trendline") and len(pts) > 1:
            m, c = np.polyfit(xs, ys, 1)
            gx = np.array([xs.min(), xs.max()])
            ax.plot(gx, m * gx + c, color=PALETTE[2], lw=1.3)
    else:
        if not isinstance(series, list) or not series or len(series) > 6:
            raise DiagramError("chart.series must be a list of 1-6 series.")
        data = []
        for s in series:
            vals = [_num(v, "series value") for v in s.get("values", [])]
            data.append((safe_label(s.get("name", "")), vals))
        n = len(data[0][1])
        if n == 0 or any(len(v) != n for _, v in data):
            raise DiagramError("All series must have the same non-zero length.")
        if kind == "histogram":
            edges = [_num(e, "bin edge") for e in spec.get("bins", [])]
            if len(edges) != n + 1 or any(b <= a for a, b in zip(edges, edges[1:])):
                raise DiagramError("histogram needs increasing 'bins' with len(values)+1 edges.")
            widths = np.diff(edges)
            ax.bar(edges[:-1], data[0][1], width=widths, align="edge", color="#cfd9ea", edgecolor=INK, lw=1)
            if spec.get("polygon"):
                mids = [(a + b) / 2 for a, b in zip(edges, edges[1:])]
                ax.plot([edges[0] - widths[0] / 2] + mids + [edges[-1] + widths[-1] / 2],
                        [0] + data[0][1] + [0], "o-", color=PALETTE[2], ms=3, lw=1.2)
            ax.set_xticks(edges)
        elif kind == "pie":
            labels = cats or [str(i + 1) for i in range(n)]
            vals = data[0][1]
            if spec.get("show_values"):
                labels = [f"{l}\n({v:g})" for l, v in zip(labels, vals)]
            ax.pie(vals, labels=labels, startangle=90, counterclock=False,
                   colors=["#f2f2f2", "#cfd9ea", "#b7c4dc", "#e6d9c2", "#d6e6d1", "#e8cfcf"] * 3,
                   wedgeprops={"edgecolor": INK, "linewidth": 1}, textprops={"fontsize": 9})
            ax.set_aspect("equal")
        else:
            if len(cats) != n:
                cats = [str(i + 1) for i in range(n)]
            idx = np.arange(n)
            k = len(data)
            if kind == "line":
                for i, (name, vals) in enumerate(data):
                    ax.plot(idx, vals, "o-", color=PALETTE[i % len(PALETTE)], ms=4, lw=1.5, label=name or None)
            else:
                bw = 0.8 / k
                shades = ["#cfd9ea", "#8fa6cc", "#e6d9c2", "#b7c4dc", "#d6e6d1", "#e8cfcf"]
                for i, (name, vals) in enumerate(data):
                    bars = ax.bar(idx - 0.4 + bw * (i + 0.5), vals, width=bw, color=shades[i % len(shades)],
                                  edgecolor=INK, lw=1, label=name or None)
                    if spec.get("show_values"):
                        ax.bar_label(bars, fmt="%g", fontsize=8, padding=1)
            ax.set_xticks(idx)
            ax.set_xticklabels(cats, fontsize=9)
            if any(name for name, _ in data):
                ax.legend(frameon=False, fontsize=9)
    if kind != "pie":
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        ax.set_xlabel(safe_label(spec.get("x_label", "")), fontsize=10)
        ax.set_ylabel(safe_label(spec.get("y_label", "")), fontsize=10)
        ax.tick_params(labelsize=8)
        ax.yaxis.set_major_locator(MaxNLocator(nbins=8))
        ax.grid(axis="y", color="#d5d5d5", lw=0.5, ls=":")
        ax.set_axisbelow(True)
    if spec.get("title"):
        ax.set_title(safe_label(spec["title"]), fontsize=10)
    return 0.7


# ------------------------------------------------------------------ venn

_VENN2 = {"A": (-1.1, 0.0), "B": (1.1, 0.0), "AB": (0.0, 0.0), "none": (-1.95, -1.1)}
_VENN3 = {"A": (-1.0, 0.65), "B": (1.0, 0.65), "C": (0.0, -1.15), "AB": (0.0, 0.85),
          "AC": (-0.62, -0.3), "BC": (0.62, -0.3), "ABC": (0.0, 0.1), "none": (-2.05, -1.65)}


def _draw_venn(fig: Figure, spec: Dict[str, Any]) -> float:
    ax = fig.add_subplot(111)
    sets = spec.get("sets") or []
    if len(sets) not in (2, 3):
        raise DiagramError("venn needs 2 or 3 sets.")
    ax.set_aspect("equal")
    ax.axis("off")
    if len(sets) == 2:
        centers, spots, r = [(-0.6, 0.0), (0.6, 0.0)], _VENN2, 1.0
        ax.set_xlim(-2.4, 2.4)
        ax.set_ylim(-1.7, 1.5)
        uni = Rectangle((-2.3, -1.5), 4.6, 3.0, fill=False, edgecolor=INK, lw=1.2)
        label_pos = [(-1.35, 1.0), (1.35, 1.0)]
    else:
        centers, spots, r = [(-0.55, 0.4), (0.55, 0.4), (0.0, -0.55)], _VENN3, 1.0
        ax.set_xlim(-2.4, 2.4)
        ax.set_ylim(-2.1, 1.8)
        uni = Rectangle((-2.3, -1.95), 4.6, 3.7, fill=False, edgecolor=INK, lw=1.2)
        label_pos = [(-1.35, 1.25), (1.35, 1.25), (0.0, -1.7)]
    ax.add_patch(uni)
    for i, c in enumerate(centers):
        ax.add_patch(Circle(c, r, fill=True, facecolor=("#cfd9ea", "#e6d9c2", "#d6e6d1")[i], alpha=0.5,
                            edgecolor=INK, lw=1.3))
        sl = sets[i].get("label", "ABC"[i]) if isinstance(sets[i], dict) else str(sets[i])
        ax.text(*label_pos[i], safe_label(sl), ha="center", va="center", fontsize=11, fontweight="bold")
    if spec.get("universe_label"):
        ax.text(-2.2, 1.35 if len(sets) == 2 else 1.6, safe_label(spec["universe_label"]), fontsize=10, va="center")
    for key, text in (spec.get("regions") or {}).items():
        k = str(key).upper() if str(key).lower() != "none" else "none"
        if k in spots:
            ax.text(*spots[k], safe_label(text), ha="center", va="center", fontsize=9)
    return 0.8


# ------------------------------------------------------------------ number line

def _draw_number_line(fig: Figure, spec: Dict[str, Any]) -> float:
    ax = fig.add_subplot(111)
    lo, hi = _num(spec.get("min", -5), "min"), _num(spec.get("max", 5), "max")
    if lo >= hi:
        raise DiagramError("number_line min must be less than max.")
    ax.set_xlim(lo - 0.6, hi + 0.8)
    ax.set_ylim(-1, 1.2)
    ax.axis("off")
    ax.annotate("", (hi + 0.7, 0), (lo - 0.5, 0), arrowprops=dict(arrowstyle="<|-|>", color=INK, lw=1.2))
    step = _num(spec.get("step", 1), "step")
    if step <= 0:
        raise DiagramError("step must be positive.")
    for v in np.arange(math.ceil(lo / step) * step, hi + 1e-9, step):
        ax.plot([v, v], [-0.08, 0.08], color=INK, lw=1)
        ax.text(v, -0.3, f"{v:g}", ha="center", va="top", fontsize=9)
    for iv in spec.get("intervals") or []:
        a = _num(iv.get("from", lo - 0.5), "interval from")
        b = _num(iv.get("to", hi + 0.5), "interval to")
        ax.plot([a, b], [0, 0], color=PALETTE[1], lw=3.5, solid_capstyle="butt", zorder=4)
        for val, closed, present in ((a, iv.get("from_closed", True), "from" in iv), (b, iv.get("to_closed", True), "to" in iv)):
            if present:
                ax.plot([val], [0], "o", ms=7, mfc=INK if closed else "white", mec=INK, zorder=6)
    for p in spec.get("points") or []:
        v = _num(p.get("value"), "point value")
        ax.plot([v], [0], "o", ms=7, mfc="white" if p.get("open") else INK, mec=INK, zorder=6)
        if p.get("label"):
            ax.text(v, 0.3, safe_label(p["label"]), ha="center", va="bottom", fontsize=11)
    return 0.22


# ------------------------------------------------------------------ public API

_DRAWERS = {
    "geometry": (_draw_geometry, 6.0),
    "plot": (_draw_plot, 7.2),
    "chart": (_draw_chart, 8.0),
    "venn": (_draw_venn, 6.0),
    "number_line": (_draw_number_line, 9.5),
}


def _validate_envelope(spec: Any) -> Dict[str, Any]:
    if not isinstance(spec, dict):
        raise DiagramError("Diagram must be a JSON object.")
    try:
        size = len(json.dumps(spec))
    except (TypeError, ValueError):
        raise DiagramError("Diagram must be JSON-serialisable.")
    if size > MAX_SPEC_BYTES:
        raise DiagramError("Diagram specification is too large.")
    dtype = str(spec.get("type", "")).lower()
    if dtype not in DIAGRAM_TYPES:
        raise DiagramError(f"Unknown diagram type {spec.get('type')!r}; expected one of {', '.join(DIAGRAM_TYPES)}.")
    return dict(spec, type=dtype)


def _latin_only(value: Any) -> Any:
    """Matplotlib cannot shape Indic scripts, so figure labels are drawn without Indic letters (a label that was only
    Indic text is left blank). The question text around the figure is unaffected."""
    if isinstance(value, str):
        return INDIC_CHAR.sub("", value).strip(" ()-:,;\u00b7") if INDIC_CHAR.search(value) else value
    if isinstance(value, list):
        return [_latin_only(v) for v in value]
    if isinstance(value, dict):
        return {k: (v if k == "caption" else _latin_only(v)) for k, v in value.items()}
    return value


def _make_figure(spec: Dict[str, Any], dpi: int) -> Figure:
    spec = _latin_only(spec)
    drawer, default_cm = _DRAWERS[spec["type"]]
    width_in = max(1.5, min(_num(spec.get("width_cm", default_cm if spec["type"] != "number_line" else 9.5), "width_cm"), 18)) / 2.54
    fig = Figure(figsize=(width_in, width_in * 0.75), dpi=dpi, facecolor="white")
    FigureCanvasAgg(fig)
    try:
        aspect = drawer(fig, spec)
    except DiagramError:
        raise
    except (ExpressionError, KeyError, TypeError, ValueError, AttributeError, IndexError) as exc:
        raise DiagramError(f"Could not draw {spec['type']} diagram: {exc}")
    fig.set_size_inches(width_in, width_in * max(0.18, min(aspect, 1.5)))
    return fig


def render_png(spec: Any, dpi: int = 200) -> RenderedImage:
    spec = _validate_envelope(spec)
    with MPL_LOCK:
        fig = _make_figure(spec, dpi)
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight", pad_inches=0.06, facecolor="white")
    data = buf.getvalue()
    from PIL import Image

    with Image.open(io.BytesIO(data)) as im:
        w, h = im.size
    return RenderedImage(data, w / dpi, h / dpi)


def render_svg(spec: Any) -> bytes:
    spec = _validate_envelope(spec)
    with MPL_LOCK:
        fig = _make_figure(spec, 100)
        buf = io.BytesIO()
        fig.savefig(buf, format="svg", bbox_inches="tight", pad_inches=0.06, metadata={"Date": None})
    return buf.getvalue()


def check_diagram(spec: Any) -> Dict[str, Any]:
    """Validate by drawing at low resolution. Returns the normalised spec or raises DiagramError."""
    normalised = _validate_envelope(spec)
    render_png(normalised, dpi=30)
    return normalised
