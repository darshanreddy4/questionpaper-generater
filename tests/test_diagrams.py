import pytest

from app.diagrams import DiagramError, check_diagram, render_png, render_svg
from app.diagrams.safe_expr import ExpressionError, compile_expr
import numpy as np

SPECS = {
    "geometry": {"type": "geometry", "points": {"A": [0, 0], "B": [4, 0], "C": [0, 3]},
                 "shapes": [{"kind": "polygon", "points": ["A", "B", "C"]},
                            {"kind": "right_angle", "at": "A", "from": "B", "to": "C"},
                            {"kind": "segment", "from": "B", "to": "C", "label": "5 cm"},
                            {"kind": "angle", "at": "B", "from": "A", "to": "C", "label": "$\\theta$"}]},
    "plot": {"type": "plot", "xlim": [-3, 3], "functions": [{"expr": "x^2 - 1", "label": "$y=x^2-1$"}, {"expr": "sin(x)"}],
             "points": [{"x": 1, "y": 0, "label": "P"}], "fills": [{"expr1": "x", "expr2": "x^2-1", "domain": [-0.6, 1.6]}]},
    "chart": {"type": "chart", "kind": "bar", "categories": ["a", "b"], "series": [{"values": [1, 2]}]},
    "histogram": {"type": "chart", "kind": "histogram", "bins": [0, 5, 10], "series": [{"values": [3, 4]}]},
    "pie": {"type": "chart", "kind": "pie", "categories": ["a", "b"], "series": [{"values": [60, 40]}]},
    "venn": {"type": "venn", "sets": [{"label": "A"}, {"label": "B"}, {"label": "C"}], "regions": {"ABC": "1", "none": "9"}},
    "number_line": {"type": "number_line", "min": -3, "max": 5, "points": [{"value": 2, "label": "P"}],
                    "intervals": [{"from": -1, "to": 3, "to_closed": False}]},
}


@pytest.mark.parametrize("name", list(SPECS))
def test_every_diagram_type_renders(name):
    png = render_png(SPECS[name], dpi=60)
    assert png.data.startswith(b"\x89PNG") and png.width_in > 0 and png.height_in > 0
    assert b"<svg" in render_svg(SPECS[name])


def test_svg_is_deterministic():
    assert render_svg(SPECS["plot"]) == render_svg(SPECS["plot"])


@pytest.mark.parametrize("bad", [
    {"type": "geometry", "points": {"A": [0, 0]}, "shapes": [{"kind": "segment", "from": "A", "to": "Z"}]},
    {"type": "geometry", "points": {"A": [0, "x"]}},
    {"type": "chart", "kind": "bar", "series": [{"values": [1, 2]}, {"values": [1]}]},
    {"type": "nope"},
    {"type": "plot", "functions": [{"expr": "__import__('os').system('id')"}]},
    "not a dict",
])
def test_invalid_diagrams_are_rejected(bad):
    with pytest.raises(DiagramError):
        check_diagram(bad)


@pytest.mark.parametrize("expr", [
    "__import__('os').system('true')", "x.__class__", "open('/etc/passwd')", "[x for x in range(9)]",
    "lambda: 1", "x**999", "eval('1')", "a+1", "x" * 300,
])
def test_expression_evaluator_refuses_unsafe_input(expr):
    with pytest.raises(ExpressionError):
        compile_expr(expr)


def test_expression_school_notation():
    f = compile_expr("2x^2 + 3(x+1) - sin(pi/2)")
    x = np.array([0.0, 1.0, 2.0])
    assert np.allclose(f(x), 2 * x ** 2 + 3 * (x + 1) - 1)
    assert np.isnan(compile_expr("1/x")(np.array([0.0]))[0])
