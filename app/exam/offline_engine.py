"""Template-based question writer used when no language model is configured.

It is intentionally modest: it builds questions from definitional sentences found in the uploaded
material (cloze, definition, true/false, matching, explain) and, for mathematics, from parametric
templates with exact answers and real diagrams. It keeps the whole product usable offline and makes
the pipeline deterministic for tests; a configured LLM produces far richer papers.
"""
from __future__ import annotations

import random
import re
from collections import Counter
from functools import lru_cache
from fractions import Fraction
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.exam.blueprint import is_math_subject
from app.exam.llm_engine import Engine, GenContext, SlotJob
from app.exam.schema import Slot
from app.ingestion.retrieval import tokenize

from app.textutil import SENTENCE_SPLIT as _SENT_SPLIT
_DEF = re.compile(r"^(?P<subj>[A-Z][^,;:()]{2,60}?)\s+(?:is|are|refers to|is defined as|are defined as|means|is called|"
                  r"are called|is known as|are known as)\s+(?P<rest>.{15,})$")
# A sentence that starts "Some even numbers are ..." or "This method is ..." defines nothing worth asking about.
_NOT_A_TERM = frozenset("some these those this that it its they we you he she there here one each all many most such both "
                        "other another any every few several".split())
_BAD_SENT = re.compile(r"(https?://|www\.|©|\bfig(?:ure)?\.?\s*\d|\btable\s*\d|\bpage\s*\d)", re.I)


# ----------------------------------------------------------------------------- text helpers

def _sentences(sources) -> List[str]:
    out: List[str] = []
    for src in sources:
        if src.kind != "text":
            continue
        para = re.sub(r"\s+", " ", src.text.replace("\n", " "))
        for s in _SENT_SPLIT.split(para):
            s = s.strip()
            if 35 <= len(s) <= 260 and not s.endswith("?") and not _BAD_SENT.search(s) \
                    and sum(c.isdigit() for c in s) / len(s) < 0.2 and s[0].isupper():
                out.append(s)
    seen, uniq = set(), []
    for s in out:
        if s.lower() not in seen:
            seen.add(s.lower())
            uniq.append(s)
    return uniq


def _definitions(sents: List[str]) -> List[Tuple[str, str, str]]:
    out = []
    for s in sents:
        m = _DEF.match(s)
        if m:
            subj = re.sub(r"^(the|a|an)\s+", "", m.group("subj").strip(), flags=re.I)
            if 1 <= len(subj.split()) <= 6 and subj.split()[0].lower() not in _NOT_A_TERM:
                out.append((subj, m.group("rest").rstrip("."), s))
    return out


def _word_pool(sents: List[str]) -> Counter:
    df: Counter = Counter()
    for s in sents:
        df.update({w for w in re.findall(r"[A-Za-z][A-Za-z\-]{4,}", s)})
    return df


def _pick_blank(sentence: str, df: Counter, rng: random.Random) -> Optional[str]:
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z\-]{4,}", sentence) if tokenize(w)]
    words = [w for w in words if sentence.find(w) > 0]
    if not words:
        return None
    words.sort(key=lambda w: (df.get(w, 9), -len(w), rng.random()))
    return words[0]


def _distractors(answer: str, df: Counter, rng: random.Random, n: int = 3, sentence: str = "") -> List[str]:
    stem = sentence.lower()
    pool = [w for w in df if w.lower() != answer.lower() and answer.lower() not in w.lower()
            and w.lower() not in answer.lower() and w[0].isupper() == answer[0].isupper()
            and w.lower() not in stem]  # a distractor already printed in the stem would give the answer away
    rng.shuffle(pool)
    pool.sort(key=lambda w: abs(len(w) - len(answer)))
    return pool[:n]


def _blank(sentence: str, word: str) -> str:
    return sentence.replace(word, "_____", 1)


def _swap(sentence: str, word: str, other: str) -> str:
    return sentence.replace(word, other, 1)


def _cap(s: str) -> str:
    return s[0].upper() + s[1:] if s else s


# ----------------------------------------------------------------------------- math templates

class MQ:
    def __init__(self, text: str, answer: str, steps: List[str], diagram: Optional[dict] = None,
                 distractors: Optional[List[str]] = None, keys: Tuple[str, ...] = (), option: Optional[str] = None):
        self.text, self.answer, self.steps, self.diagram = text, answer, steps, diagram
        self.distractors, self.keys = distractors or [], keys
        self.option = option or answer  # the correct choice as it appears among MCQ options
        self.distractors = [d for d in dict.fromkeys(self.distractors) if d != self.option]  # never offer the answer twice


def _fmt(v) -> str:
    if isinstance(v, Fraction):
        if v.denominator == 1:
            return str(v.numerator)
        sign = "-" if v < 0 else ""
        return f"{sign}\\frac{{{abs(v.numerator)}}}{{{v.denominator}}}"
    return f"{v:g}" if isinstance(v, float) else str(v)


def _num_distractors(value, rng: random.Random) -> List[str]:
    v = Fraction(value)
    cands = [v + 1, v - 1, v * 2, v + 2, v - 2, v + 3, v / 2 if v else Fraction(1, 2)]
    if v <= 0:
        cands.append(-v)  # a sign error is only a plausible slip when the true value is not already positive
    rng.shuffle(cands)
    out: List[str] = []
    for c in cands:
        s = f"${_fmt(c)}$"
        if c != v and s not in out:
            out.append(s)
        if len(out) == 3:
            break
    return out


def _signed(coef: int, var: str, first: bool = False) -> str:
    if coef == 0:
        return ""
    sign = "-" if coef < 0 else ("" if first else "+")
    mag = abs(coef)
    body = var if (mag == 1 and var) else f"{mag}{var}"
    return f"{sign} {body}".strip() if not first else f"{sign}{body}"


def _t_linear(rng, d, dia):
    hi = 9 if d == "easy" else 20
    x, a, b = rng.randint(2, hi), rng.randint(2, 5 if d == "easy" else 9), rng.randint(1, 25)
    c = a * x + b
    return MQ(f"Solve the equation ${a}x + {b} = {c}$.", f"$x = {x}$",
              [f"Subtract ${b}$ from both sides: ${a}x = {c - b}$.", f"Divide by ${a}$: $x = {x}$."],
              distractors=_num_distractors(x, rng), keys=("linear equation", "simple equation", "algebra"), option=f"${x}$")


def _t_quadratic(rng, d, dia):
    hi = 6 if d == "easy" else 12
    p, q = rng.randint(1, hi), -rng.randint(1, hi)
    if rng.random() < 0.5:
        p = -p
    s, pr = p + q, p * q
    eq = f"x^2 {_signed(-s, 'x')} {_signed(pr, '')}".replace("  ", " ").strip()
    eq = eq.replace("+ -", "- ")
    ans = f"$x = {p}$ or $x = {q}$"
    return MQ(f"Find the roots of the quadratic equation ${eq} = 0$.", ans,
              [f"Split the middle term: ${eq} = (x {'-' if p > 0 else '+'} {abs(p)})(x {'-' if q > 0 else '+'} {abs(q)})$.",
               "Equate each factor to zero.", f"Hence {ans}."],
              distractors=[f"$x = {-p}$ or $x = {-q}$", f"$x = {p}$ or $x = {-q}$", f"$x = {q - 1}$ or $x = {p + 1}$",
                           f"$x = {-p - 1}$ or $x = {q}$"],
              keys=("quadratic", "roots"))


def _t_pythagoras(rng, d, dia):
    a, b, c = rng.choice([(3, 4, 5), (5, 12, 13), (8, 15, 17), (7, 24, 25), (6, 8, 10)])
    k = 1 if d == "easy" else rng.randint(1, 5)
    a, b, c = a * k, b * k, c * k
    sc = 5.0 / max(a, b)
    diagram = {"type": "geometry",
               "points": {"B": [0, 0], "A": [0, round(a * sc, 3)], "C": [round(b * sc, 3), 0]},
               "shapes": [{"kind": "polygon", "points": ["A", "B", "C"]},
                          {"kind": "right_angle", "at": "B", "from": "A", "to": "C"},
                          {"kind": "segment", "from": "A", "to": "B", "label": f"{a} cm"},
                          {"kind": "segment", "from": "B", "to": "C", "label": f"{b} cm"},
                          {"kind": "segment", "from": "A", "to": "C", "label": "?"}]} if dia else None
    text = ("In the figure, $\\angle B = 90^\\circ$. Find the length of $AC$." if dia else
            f"In a right-angled triangle $ABC$, $\\angle B = 90^\\circ$, $AB = {a}$ cm and $BC = {b}$ cm. Find $AC$.")
    return MQ(text, f"$AC = {c}$ cm",
              ["By the Pythagoras theorem, $AC^2 = AB^2 + BC^2$.", f"$AC^2 = {a}^2 + {b}^2 = {a * a + b * b}$.",
               f"$AC = \\sqrt{{{a * a + b * b}}} = {c}$ cm."],
              diagram, [f"${c + 1}$ cm", f"${a + b}$ cm", f"${c - 1}$ cm"], keys=("pythagoras", "triangle", "right-angled", "right angled"),
              option=f"${c}$ cm")


def _t_triangle_area(rng, d, dia):
    b = rng.choice([6, 8, 10, 12, 14, 16, 18, 20, 22]) * (1 if d != "hard" else 2)
    h = rng.choice([4, 5, 6, 7, 8, 9, 11, 12])
    px = round(b * 0.35, 3)
    sc = 5.0 / max(b, h)
    diagram = {"type": "geometry",
               "points": {"A": [0, 0], "B": [round(b * sc, 3), 0], "C": [round(px * sc, 3), round(h * sc, 3)],
                          "_D": [round(px * sc, 3), 0]},
               "shapes": [{"kind": "polygon", "points": ["A", "B", "C"]},
                          {"kind": "segment", "from": "C", "to": "_D", "style": "dashed", "label": f"{h} cm"},
                          {"kind": "segment", "from": "A", "to": "B", "label": f"{b} cm"},
                          {"kind": "right_angle", "at": "_D", "from": "B", "to": "C"}]} if dia else None
    text = ("Find the area of the triangle shown in the figure." if dia else
            f"Find the area of a triangle whose base is {b} cm and height is {h} cm.")
    area = Fraction(b * h, 2)
    return MQ(text, f"${_fmt(area)}$ cm$^2$",
              ["Area of a triangle $= \\frac{1}{2} \\times \\text{base} \\times \\text{height}$.",
               f"$= \\frac{{1}}{{2}} \\times {b} \\times {h} = {_fmt(area)}$ cm$^2$."],
              diagram, [f"${b * h}$ cm$^2$", f"${b + h}$ cm$^2$", f"${_fmt(area + h)}$ cm$^2$"],
              keys=("area of", "mensuration", "heron"))


def _t_circle(rng, d, dia):
    r = rng.choice([7, 14, 21, 28, 35, 42, 49, 56, 63, 70]) if d != "easy" else rng.choice([7, 14, 21])
    area = Fraction(22 * r * r, 7)
    diagram = {"type": "geometry", "points": {"O": [0, 0], "P": [r / 7, 0]},
               "shapes": [{"kind": "circle", "center": "O", "radius": r / 7},
                          {"kind": "segment", "from": "O", "to": "P", "label": f"{r} cm"}]} if dia else None
    text = ("Using $\\pi = \\frac{22}{7}$, find the area of the circle shown." if dia else
            f"Find the area of a circle of radius {r} cm. (Use $\\pi = \\frac{{22}}{{7}}$)")
    return MQ(text, f"${_fmt(area)}$ cm$^2$",
              ["Area of a circle $= \\pi r^2$.", f"$= \\frac{{22}}{{7}} \\times {r} \\times {r} = {_fmt(area)}$ cm$^2$."],
              diagram, [f"${_fmt(area * 2)}$ cm$^2$", f"${_fmt(Fraction(44 * r, 7))}$ cm$^2$", f"${_fmt(area + r)}$ cm$^2$"],
              keys=("circle", "area of", "mensuration", "sector"))


def _t_line(rng, d, dia):
    m, c = rng.choice([1, 2, 3, 4, 5, -1, -2, -3]), rng.choice([2, 3, 4, 5, 6, 8, -2, -3, -4, -6])
    eq = f"y = {_signed(m, 'x', True)} {_signed(c, '')}".replace("  ", " ")
    xi = Fraction(-c, m)
    diagram = {"type": "plot", "xlim": [-6, 6], "ylim": [-8, 8], "functions": [{"expr": f"{m}*x+({c})"}]} if dia else None
    text = f"The graph of the line ${eq}$ is shown. Find the coordinates of the points where it meets the axes." \
        if dia else f"Find the coordinates of the points where the line ${eq}$ meets the x-axis and the y-axis."
    return MQ(text, f"$({_fmt(xi)}, 0)$ and $(0, {c})$",
              ["On the x-axis $y = 0$, so $" + f"{m}x {'+' if c >= 0 else '-'} {abs(c)} = 0$" + f" giving $x = {_fmt(xi)}$.",
               f"On the y-axis $x = 0$, so $y = {c}$."],
              diagram, [f"$({_fmt(-xi)}, 0)$ and $(0, {c})$", f"$({_fmt(xi)}, 0)$ and $(0, {-c})$",
                        f"$(0, {_fmt(xi)})$ and $({c}, 0)$"], keys=("coordinate", "graph", "straight line", "intercept"))


def _t_mean(rng, d, dia):
    n = 5
    vals = [rng.randint(3, 15) for _ in range(n)]
    vals[-1] += (-sum(vals)) % n
    mean = sum(vals) // n
    days = ["Mon", "Tue", "Wed", "Thu", "Fri"]
    diagram = {"type": "chart", "kind": "bar", "categories": days, "series": [{"values": vals}],
               "x_label": "Day", "y_label": "Books read", "show_values": True} if dia else None
    text = ("The bar chart shows the number of books read by a student on five days. Find the mean number of books "
            "read per day." if dia else f"Find the mean of the observations ${', '.join(map(str, vals))}$.")
    return MQ(text, f"${mean}$", [f"Sum of observations $= {sum(vals)}$.",
                                   f"Mean $= \\frac{{{sum(vals)}}}{{{n}}} = {mean}$."],
              diagram, _num_distractors(mean, rng), keys=("mean", "statistic", "average", "data"))


def _t_ap(rng, d, dia):
    a, df_, n = rng.randint(1, 9), rng.randint(2, 7), rng.randint(8, 20)
    an = a + (n - 1) * df_
    return MQ(f"Find the ${n}$th term of the arithmetic progression ${a}, {a + df_}, {a + 2 * df_}, \\dots$",
              f"$a_{{{n}}} = {an}$",
              ["Here $a = %d$ and $d = %d$." % (a, df_), f"$a_n = a + (n-1)d = {a} + {n - 1} \\times {df_}$.",
               f"$a_{{{n}}} = {an}$."], distractors=_num_distractors(an, rng),
              keys=("progression", "sequence", "series", "ap"))


def _t_prob(rng, d, dia):
    r = rng.randint(2, 8)
    b = rng.choice([x for x in range(2, 9) if x != r])
    p = Fraction(r, r + b)
    return MQ(f"A bag contains {r} red balls and {b} blue balls. One ball is drawn at random. Find the probability "
              "that it is red.", f"${_fmt(p)}$",
              [f"Total outcomes $= {r} + {b} = {r + b}$.", f"$P(\\text{{red}}) = \\frac{{{r}}}{{{r + b}}} = {_fmt(p)}$."],
              distractors=[f"${_fmt(Fraction(b, r + b))}$", f"${_fmt(Fraction(1, r + b))}$",
                           f"${_fmt(Fraction(r + 1, r + b + 1))}$",
                           f"${_fmt(Fraction(r, 2 * (r + b)))}$"],
              keys=("probability", "chance"))


def _t_trig(rng, d, dia):
    ang = rng.choice([45, 60, 30])
    dist = {45: rng.choice([8, 10, 12, 15, 18, 20, 24, 25, 30, 36, 40, 50]),
            60: rng.choice([8, 10, 12, 15, 18, 20, 24, 25, 30, 36, 40, 50]),
            30: rng.choice([6, 9, 12, 15, 18, 21, 24, 27, 30, 45, 60])}[ang]
    ans = {45: f"{dist}", 60: f"{dist}\\sqrt{{3}}", 30: f"{dist // 3}\\sqrt{{3}}"}[ang]
    tan = {45: "1", 60: "\\sqrt{3}", 30: "\\frac{1}{\\sqrt{3}}"}[ang]
    width = 5.0
    h = {45: 1.0, 60: 1.732, 30: 0.577}[ang] * width
    diagram = {"type": "geometry", "points": {"B": [0, 0], "C": [width, 0], "T": [0, round(h, 3)]},
               "shapes": [{"kind": "polygon", "points": ["B", "C", "T"]},
                          {"kind": "right_angle", "at": "B", "from": "C", "to": "T"},
                          {"kind": "angle", "at": "C", "from": "B", "to": "T", "label": f"${ang}^\\circ$"},
                          {"kind": "segment", "from": "B", "to": "C", "label": f"{dist} m"}]} if dia else None
    text = (f"A tower $BT$ stands vertically. From a point $C$ on the ground {dist} m from its foot, the angle of "
            f"elevation of the top is ${ang}^\\circ$. Find the height of the tower.")
    return MQ(text, f"${ans}$ m", [f"In right triangle $TBC$, $\\tan {ang}^\\circ = \\frac{{BT}}{{BC}}$.",
                                    f"$BT = {dist} \\times {tan}$.", f"$BT = {ans}$ m."],
              diagram, [f"${dist + 5}$ m", f"${dist}\\sqrt{{2}}$ m" if ang != 45 else f"${dist}\\sqrt{{3}}$ m",
                        f"${dist * 2}$ m"], keys=("trigonometr", "height", "elevation", "depression"))


def _t_venn(rng, d, dia):
    a, b, both = rng.randint(3, 9), rng.randint(3, 9), rng.randint(2, 6)
    outside = rng.randint(1, 6)
    total = a + b + both
    diagram = {"type": "venn", "sets": [{"label": "A"}, {"label": "B"}],
               "regions": {"A": str(a), "B": str(b), "AB": str(both), "none": str(outside)}} if dia else None
    text = ("In the Venn diagram the numbers show how many elements lie in each region. Find $n(A \\cup B)$."
            if dia else f"If $n(A \\setminus B) = {a}$, $n(B \\setminus A) = {b}$ and $n(A \\cap B) = {both}$, find $n(A \\cup B)$.")
    return MQ(text, f"${total}$", [f"$n(A \\cup B) = {a} + {both} + {b}$.", f"$= {total}$."],
              diagram, _num_distractors(total, rng), keys=("sets", "venn", "union", "intersection"))



def _par(n: int) -> str:
    return f"({n})" if n < 0 else str(n)


def _plus_minus(n: int) -> str:
    """'- 24' for 24, '+ 24' for -24: subtracting a negative reads as adding."""
    return f"+ {-n}" if n < 0 else f"- {n}"


def _t_percent(rng, d, dia):
    cp = rng.choice([200, 400, 600, 800, 1000, 1200, 1600, 2000, 2400])
    pct = rng.choice([5, 10, 20, 25])
    profit = rng.random() < 0.5
    amt = cp * pct // 100
    sp = cp + amt if profit else cp - amt
    word = "profit" if profit else "loss"
    opposite = cp - amt if profit else cp + amt
    return MQ(f"An article is bought for Rs {cp} and sold at a {word} of {pct} per cent. Find the selling price.",
              f"Rs ${sp}$",
              [f"{word.capitalize()} $= \\frac{{{pct}}}{{100}} \\times {cp} = {amt}$.",
               f"Selling price $= {cp} {'+' if profit else '-'} {amt} = {sp}$."],
              None, [f"Rs ${opposite}$", f"Rs ${amt}$", f"Rs ${cp}$"],
              keys=("percent", "profit", "loss", "discount", "commercial"))


def _t_simple_interest(rng, d, dia):
    p, r, t = rng.choice([1000, 2000, 3000, 5000, 8000, 10000]), rng.choice([5, 6, 8, 10, 12]), rng.choice([2, 3, 4, 5])
    si = p * r * t // 100
    return MQ(f"Find the simple interest on Rs {p} for {t} years at {r} per cent per annum.", f"Rs ${si}$",
              ["Simple interest $= \\frac{P \\times R \\times T}{100}$.",
               f"$= \\frac{{{p} \\times {r} \\times {t}}}{{100}} = {si}$."],
              None, [f"Rs ${si + r * t}$", f"Rs ${p * r * t // 10}$", f"Rs ${si // 2}$"],
              keys=("interest", "simple interest", "commercial"))


def _t_simul(rng, d, dia):
    while True:
        x, y = rng.randint(1, 8), rng.randint(1, 8)
        a1, b1, a2, b2 = (rng.randint(1, 5) for _ in range(4))
        det = a1 * b2 - a2 * b1
        if det != 0 and x != y:
            break
    c1, c2 = a1 * x + b1 * y, a2 * x + b2 * y
    num = a1 * c2 - a2 * c1
    ans = f"$x = {x}$, $y = {y}$"
    return MQ(f"Solve the pair of linear equations: ${a1}x + {b1}y = {c1}$ and ${a2}x + {b2}y = {c2}$.", ans,
              [f"Multiply the first equation by ${a2}$ and the second by ${a1}$, then subtract: ${det}y = {num}$.",
               f"$y = \\frac{{{num}}}{{{det}}} = {y}$.",
               f"Substitute $y = {y}$ in the first equation: ${a1}x + {b1 * y} = {c1}$, so $x = {x}$."],
              None, [f"$x = {y}$, $y = {x}$", f"$x = {x + 1}$, $y = {y}$", f"$x = {x}$, $y = {y + 1}$"],
              keys=("pair of linear", "simultaneous", "two variables"))


def _t_distance(rng, d, dia):
    dx, dy, dist = rng.choice([(3, 4, 5), (6, 8, 10), (5, 12, 13), (8, 15, 17), (9, 12, 15)])
    x1, y1 = rng.randint(-5, 4), rng.randint(-5, 4)
    x2, y2 = x1 + rng.choice([1, -1]) * dx, y1 + rng.choice([1, -1]) * dy
    n2 = dx * dx + dy * dy
    diagram = {"type": "plot", "xlim": [min(x1, x2) - 2, max(x1, x2) + 2], "ylim": [min(y1, y2) - 2, max(y1, y2) + 2],
               "points": [{"x": x1, "y": y1, "label": "A"}, {"x": x2, "y": y2, "label": "B"}],
               "lines": [{"from": [x1, y1], "to": [x2, y2], "style": "dashed"}]} if dia else None
    text = ("Find the distance between the points $A$ and $B$ shown on the graph." if dia else
            f"Find the distance between the points $A({x1}, {y1})$ and $B({x2}, {y2})$.")
    return MQ(text, f"$AB = {dist}$ units",
              [f"$AB = \\sqrt{{({x2} - {_par(x1)})^2 + ({y2} - {_par(y1)})^2}}$.",
               f"$= \\sqrt{{{dx * dx} + {dy * dy}}} = \\sqrt{{{n2}}} = {dist}$ units."],
              diagram, [f"${dist + 1}$ units", f"${dx + dy}$ units", f"${dist - 1}$ units"],
              keys=("coordinate", "distance", "section formula", "cartesian"), option=f"${dist}$ units")


def _t_cylinder(rng, d, dia):
    r, h = rng.choice([7, 14, 21]), rng.randint(3, 15)
    v = 22 * r * r * h // 7
    csa = 44 * r * h // 7
    return MQ(f"A cylinder has base radius {r} cm and height {h} cm. Find its volume. (Use $\\pi = \\frac{{22}}{{7}}$)",
              f"${v}$ cm$^3$",
              ["Volume of a cylinder $= \\pi r^2 h$.", f"$= \\frac{{22}}{{7}} \\times {r} \\times {r} \\times {h} = {v}$ cm$^3$."],
              None, [f"${v * 2}$ cm$^3$", f"${csa}$ cm$^3$", f"${v + h}$ cm$^3$"],
              keys=("cylinder", "volume", "surface area", "mensuration"))


def _t_similar(rng, d, dia):
    a, b, q = rng.choice([2, 3, 4, 5, 6]), rng.randint(2, 9), rng.choice([2, 3])
    c, ec = a * q, b * q
    t = a / (a + b)
    pts = {"A": [0, 6], "B": [-4, 0], "C": [4, 0], "D": [round(-4 * t, 3), round(6 - 6 * t, 3)],
           "E": [round(4 * t, 3), round(6 - 6 * t, 3)]}
    diagram = {"type": "geometry", "points": pts,
               "shapes": [{"kind": "polygon", "points": ["A", "B", "C"]}, {"kind": "segment", "from": "D", "to": "E"},
                          {"kind": "segment", "from": "A", "to": "D", "label": f"{a} cm"},
                          {"kind": "segment", "from": "D", "to": "B", "label": f"{b} cm"},
                          {"kind": "segment", "from": "A", "to": "E", "label": f"{c} cm"},
                          {"kind": "segment", "from": "E", "to": "C", "label": "?"}]} if dia else None
    text = ("In the figure, $DE \\parallel BC$. Find the length of $EC$." if dia else
            f"In triangle $ABC$, $D$ lies on $AB$ and $E$ lies on $AC$ with $DE \\parallel BC$. If $AD = {a}$ cm, "
            f"$DB = {b}$ cm and $AE = {c}$ cm, find $EC$.")
    return MQ(text, f"$EC = {ec}$ cm",
              ["By the basic proportionality theorem, $\\frac{AD}{DB} = \\frac{AE}{EC}$.",
               f"$\\frac{{{a}}}{{{b}}} = \\frac{{{c}}}{{EC}}$.", f"$EC = \\frac{{{b} \\times {c}}}{{{a}}} = {ec}$ cm."],
              diagram, [f"${ec + 1}$ cm", f"${ec - 1}$ cm", f"${b + c}$ cm"],
              keys=("similar", "triangle", "thales", "proportionality"), option=f"${ec}$ cm")


def _t_median(rng, d, dia):
    n = rng.choice([5, 7])
    vals = rng.sample(range(2, 40), n)
    srt = sorted(vals)
    med = srt[n // 2]
    mean = round(sum(vals) / n)
    dist = [srt[n // 2 - 1], srt[n // 2 + 1], mean]
    return MQ(f"Find the median of the data ${', '.join(map(str, vals))}$.", f"${med}$",
              [f"Ascending order: ${', '.join(map(str, srt))}$.",
               f"There are ${n}$ observations, so the median is the $\\frac{{{n} + 1}}{{2}}$th value, and $\\frac{{{n} + 1}}{{2}} = {(n + 1) // 2}$.",
               f"Median $= {med}$."],
              None, [f"${x}$" for x in dist if x != med], keys=("median", "statistic", "central tendency", "data"))


def _t_derivative(rng, d, dia):
    import sympy as sp

    x = sp.Symbol("x")
    n = rng.choice([2, 3]) if d == "easy" else rng.choice([3, 4, 5])
    a, b, c, k = rng.randint(2, 7), rng.randint(1, 6), rng.randint(1, 9), rng.randint(1, 9)
    f = a * x ** n + b * x ** 2 + c * x + k if n != 2 else a * x ** 2 + c * x + k
    fp = sp.diff(f, x)
    lt = sp.latex
    terms = [t for t in sp.Add.make_args(sp.expand(f)) if t.has(x)]
    steps = ["Using the power rule $\\frac{d}{dx}(x^n) = n x^{n-1}$ term by term."]
    steps += [f"$\\frac{{d}}{{dx}}\\left({lt(t)}\\right) = {lt(sp.diff(t, x))}$." for t in terms]
    steps.append(f"Hence $\\frac{{dy}}{{dx}} = {lt(fp)}$.")
    wrong = [lt(sp.integrate(f, x)), lt(sp.expand(fp + k)), lt(sp.expand(fp - c))]
    return MQ(f"Find $\\frac{{dy}}{{dx}}$ if $y = {lt(f)}$.", f"$\\frac{{dy}}{{dx}} = {lt(fp)}$", steps, None,
              [f"$\\frac{{dy}}{{dx}} = {w}$" for w in wrong], keys=("derivative", "differentiation", "calculus", "rate of change"))


def _t_integral(rng, d, dia):
    import sympy as sp

    x = sp.Symbol("x")
    n, m = rng.choice([2, 3, 4]), rng.choice([1, 2])
    a, b = rng.randint(1, 5), rng.randint(1, 6)
    g = a * (n + 1) * x ** n + b * (m + 1) * x ** m
    big = sp.integrate(g, x)
    lt = sp.latex
    steps = ["Using $\\int x^n\\,dx = \\frac{x^{n+1}}{n+1} + C$ term by term.",
             f"Hence $\\int \\left({lt(g)}\\right) dx = {lt(big)} + C$."]
    wrong = [lt(sp.diff(g, x)), lt(sp.expand(big * x)), lt(sp.expand(big + x))]
    return MQ(f"Evaluate $\\int \\left({lt(g)}\\right) dx$.", f"${lt(big)} + C$", steps, None,
              [f"${w} + C$" for w in wrong], keys=("integral", "integration", "calculus", "antiderivative"))


def _t_determinant(rng, d, dia):
    a, b, c, e = (rng.randint(-5, 9) for _ in range(4))
    val = a * e - b * c
    cand = [a * e + b * c, -val, a * b - c * e, val + 1, val - 1, val + 2, val * 2 + 1]
    cand = [x for x in dict.fromkeys(cand) if x != val]
    return MQ(f"Find the value of the determinant $\\begin{{vmatrix}} {a} & {b} \\\\ {c} & {e} \\end{{vmatrix}}$.", f"${val}$",
              ["For a $2 \\times 2$ determinant, the value is $ad - bc$.",
               f"$= ({a})({e}) - ({b})({c}) = {val}$."],
              None, [f"${x}$" for x in cand[:4]], keys=("matrix", "matrices", "determinant"))


def _t_discriminant(rng, d, dia):
    a, b, c = rng.choice([1, 1, 2, 3]), rng.choice([-8, -6, -5, -4, -3, -2, 2, 3, 4, 5, 6, 8]), rng.choice([-6, -4, -3, -2, -1, 1, 2, 3, 4, 6])
    disc = b * b - 4 * a * c
    nature = "real and distinct" if disc > 0 else ("real and equal" if disc == 0 else "not real")

    def nat(v):
        return "real and distinct" if v > 0 else ("real and equal" if v == 0 else "not real")

    eq = f"{_signed(a, 'x^2', True)} {_signed(b, 'x')} {_signed(c, '')} = 0".replace("  ", " ")
    opts = [f"$D = {v}$; the roots are {nat(v)}." for v in dict.fromkeys([b * b + 4 * a * c, b * b - 2 * a * c, -disc]) if v != disc]
    return MQ(f"Find the discriminant of the quadratic equation ${eq}$ and state the nature of its roots.",
              f"$D = {disc}$; the roots are {nature}.",
              ["For $ax^2 + bx + c = 0$, the discriminant is $D = b^2 - 4ac$.",
               f"$D = ({b})^2 - 4({a})({c}) = {b * b} {_plus_minus(4 * a * c)} = {disc}$.",
               f"Since $D {'>' if disc > 0 else ('=' if disc == 0 else '<')} 0$, the roots are {nature}."],
              None, opts, keys=("discriminant", "nature of roots"))


_MATH_TEMPLATES: Dict[str, Callable] = {
    "linear": _t_linear, "quadratic": _t_quadratic, "pythagoras": _t_pythagoras, "triangle_area": _t_triangle_area,
    "circle": _t_circle, "line": _t_line, "mean": _t_mean, "ap": _t_ap, "prob": _t_prob, "trig": _t_trig, "venn": _t_venn,
    "percent": _t_percent, "simple_interest": _t_simple_interest, "simul": _t_simul, "distance": _t_distance,
    "cylinder": _t_cylinder, "similar": _t_similar, "median": _t_median, "derivative": _t_derivative,
    "integral": _t_integral, "determinant": _t_determinant, "discriminant": _t_discriminant,
}
_DIAGRAM_TEMPLATES = ["pythagoras", "triangle_area", "circle", "line", "mean", "trig", "venn", "distance", "similar"]
_FORMULA_SETS = [
    ("Area of a rectangle", "$l \\times b$"), ("Area of a triangle", "$\\frac{1}{2} b h$"),
    ("Area of a circle", "$\\pi r^2$"), ("Circumference of a circle", "$2 \\pi r$"),
    ("Volume of a cube", "$a^3$"), ("Volume of a sphere", "$\\frac{4}{3} \\pi r^3$"),
]


@lru_cache(maxsize=None)
def _template_keys(name: str) -> Tuple[str, ...]:
    return _MATH_TEMPLATES[name](random.Random(0), "easy", False).keys


def match_templates(topic: str, wants_diagram: bool) -> List[str]:
    """Templates whose subject matter really is *topic* (empty when the topic is outside the library)."""
    pool = _DIAGRAM_TEMPLATES if wants_diagram else list(_MATH_TEMPLATES)
    t = topic.lower()
    return [n for n in pool if any(k in t for k in _template_keys(n))]


def _pick_template(rng: random.Random, slot: Slot, strict: bool = False) -> MQ:
    names = match_templates(slot.topic, slot.wants_diagram)
    if strict and not names:
        raise LookupError(slot.topic)
    pool = _DIAGRAM_TEMPLATES if slot.wants_diagram else list(_MATH_TEMPLATES)
    name = rng.choice(names or pool)
    return _MATH_TEMPLATES[name](rng, slot.difficulty, slot.wants_diagram and name in _DIAGRAM_TEMPLATES)


def _math_question(slot: Slot, rng: random.Random, allow_alt: bool = True, strict: bool = False) -> Dict[str, Any]:
    qtype = slot.question_type
    if qtype == "match_following":
        pairs = rng.sample(_FORMULA_SETS, 4)
        right = [p[1] for p in pairs]
        order = list(range(4))
        rng.shuffle(order)
        shuffled = [right[i] for i in order]
        letters = "abcd"
        key = ", ".join(f"{i + 1}-{letters[order.index(i)]}" for i in range(4))
        return {"text": "Match the quantities in Column I with their formulas in Column II.",
                "match": {"left": [p[0] for p in pairs], "right": shuffled}, "answer": {"text": key}}
    if qtype == "assertion_reason":
        s, p = rng.randint(3, 12), rng.randint(2, 30)
        bank = [
            (f"For the equation $x^2 - {s}x + {s - 1} = 0$ the sum of the roots is ${s}$.",
             "For $ax^2 + bx + c = 0$ the sum of the roots is $-\\frac{b}{a}$.", 0, "Both true; R is the rule that gives A."),
            (f"For the equation $x^2 - {s}x + {p} = 0$ the product of the roots is ${p}$.",
             "For $ax^2 + bx + c = 0$ the product of the roots is $\\frac{c}{a}$.", 0, "Both true; R is the rule that gives A."),
            (f"The sum of the first ${s}$ natural numbers is ${s * (s + 1) // 2}$.",
             "The sum of the first $n$ natural numbers is $\\frac{n(n+1)}{2}$.", 0, "Both true; R gives A by substitution."),
            ("The probability of an impossible event is 1.",
             "The probability of any event lies between 0 and 1, both inclusive.", 3,
             "A is false (it is 0); R is true."),
            (f"For the equation $x^2 - {s}x + {s - 1} = 0$ the sum of the roots is ${-s}$.",
             "For $ax^2 + bx + c = 0$ the sum of the roots is $-\\frac{b}{a}$.", 3,
             f"A is false (the sum is ${s}$); R is true."),
        ]
        a_text, r_text, key, why = rng.choice(bank)
        return {"text": f"Assertion (A): {a_text}\nReason (R): {r_text}", "answer": {"correct_option": key, "text": why}}
    if qtype == "case_study":
        subs = []
        for label in ("(i)", "(ii)", "(iii)"):
            mq = _pick_template(rng, slot.model_copy(update={"wants_diagram": False}))
            subs.append({"label": label, "text": mq.text, "marks": slot.marks / 3,
                         "answer": {"text": mq.answer, "steps": [{"text": t, "marks": 1} for t in mq.steps]}})
        return {"text": "A class teacher prepares a set of practice problems from the topics studied this term. "
                        "Answer the following questions based on them.", "sub_questions": subs,
                "answer": {"text": "See sub-part answers."}}
    mq = _pick_template(rng, slot, strict)
    raw: Dict[str, Any] = {"text": mq.text, "diagram": mq.diagram,
                           "answer": {"text": mq.answer, "steps": [{"text": t, "marks": 1} for t in mq.steps]}}
    if qtype == "mcq":
        opts = list(dict.fromkeys([mq.option] + [d for d in mq.distractors if d != mq.option]))[:4]
        shift = 1
        while len(opts) < 4:  # pad with look-alike numeric options rather than an out-of-format filler
            opts.append(re.sub(r"-?\d+", lambda m: str(int(m.group()) + shift), mq.option, count=1))
            opts = list(dict.fromkeys(opts))
            shift += 1
        rng.shuffle(opts)
        raw["options"] = opts
        raw["answer"] = {"correct_option": opts.index(mq.option), "text": " ".join(mq.steps)}
    elif qtype == "true_false":
        truth = rng.random() < 0.5
        shown = mq.answer if truth else (mq.distractors[0] if mq.distractors else "$0$")
        raw = {"text": f"State whether true or false: {mq.text} The answer is {shown}.", "diagram": mq.diagram,
               "answer": {"text": ("True. " if truth else "False. ") + f"The correct answer is {mq.answer}."}}
    elif qtype == "fill_blank":
        raw = {"text": f"{mq.text} Answer: _____", "diagram": mq.diagram, "answer": {"text": mq.answer}}
    elif qtype == "very_short":
        raw["answer"] = {"text": mq.answer, "steps": [{"text": mq.steps[-1], "marks": 1}] if mq.steps else []}
    if slot.internal_choice and allow_alt:
        alt = _math_question(slot.model_copy(update={"internal_choice": False}), rng, allow_alt=False, strict=strict)
        raw["alternative"] = alt
    return raw


# ----------------------------------------------------------------------------- text questions

def _text_question(job: SlotJob, rng: random.Random, allow_alt: bool = True) -> Optional[Dict[str, Any]]:
    slot = job.slot
    sents = _sentences(job.sources)
    if not sents:
        return None
    defs = _definitions(sents)
    df = _word_pool(sents)
    refs = [s.id for s in job.sources[:2]]
    qtype = slot.question_type
    topic = slot.topic
    rng.shuffle(sents)
    raw: Optional[Dict[str, Any]] = None

    def cloze(sentence: str):
        word = _pick_blank(sentence, df, rng)
        return (word, _blank(sentence, word)) if word else (None, None)

    if qtype in ("mcq", "fill_blank", "true_false", "assertion_reason"):
        for sentence in sents:
            word, blanked = cloze(sentence)
            if not word:
                continue
            ds = _distractors(word, df, rng, sentence=sentence)
            if qtype == "mcq" and len(ds) == 3:
                opts = ds + [word]
                rng.shuffle(opts)
                raw = {"text": f"Choose the word that correctly completes the statement: \"{blanked}\"", "options": opts,
                       "answer": {"correct_option": opts.index(word), "text": sentence}}
            elif qtype == "fill_blank":
                raw = {"text": f"Fill in the blank: {blanked}", "answer": {"text": word}}
            elif qtype == "true_false" and ds:
                if rng.random() < 0.5:
                    raw = {"text": sentence, "answer": {"text": "True. " + sentence}}
                else:
                    raw = {"text": _swap(sentence, word, ds[0]),
                           "answer": {"text": f"False. The correct statement is: {sentence}"}}
            elif qtype == "assertion_reason" and ds and len(sents) > 1:
                other = next((x for x in sents if x != sentence), sentence)
                raw = {"text": f"Assertion (A): {sentence}\nReason (R): {_swap(other, _pick_blank(other, df, rng) or '', ds[0])}",
                       "answer": {"correct_option": 2, "text": "A is true; R has been altered and is false."}}
            if raw:
                break
        if raw is None:
            qtype = "very_short"
    if raw is None and qtype == "match_following":
        if len(defs) >= 4:
            chosen = defs[:4]
            order = list(range(4))
            rng.shuffle(order)
            rights = [" ".join(chosen[i][1].split()[:12]) for i in order]
            letters = "abcd"
            raw = {"text": "Match the terms in Column I with their descriptions in Column II.",
                   "match": {"left": [c[0] for c in chosen], "right": rights},
                   "answer": {"text": ", ".join(f"{i + 1}-{letters[order.index(i)]}" for i in range(4))}}
        else:
            return _text_question(SlotJob(slot.model_copy(update={"question_type": "fill_blank"}), job.sources), rng)
    if raw is None and qtype == "very_short":
        if defs:
            term, rest, full = rng.choice(defs)
            raw = {"text": rng.choice([f"Define {term}.", f"What is meant by {term}?"]), "answer": {"text": full}}
        else:
            raw = {"text": f"State one important fact about {topic}.", "answer": {"text": sents[0]}}
    elif raw is None and qtype in ("short", "numerical"):
        picks = sents[:3]
        raw = {"text": f"Explain {topic} briefly.", "answer": {"text": " ".join(picks),
               "steps": [{"text": p, "marks": 1} for p in picks]}}
    elif raw is None and qtype == "long":
        picks = sents[:max(4, min(8, int(slot.marks)))]
        raw = {"text": f"Discuss {topic} in detail.", "answer": {"text": " ".join(picks),
               "steps": [{"text": p, "marks": 1} for p in picks]}}
    elif raw is None and qtype == "case_study":
        passage = " ".join(sents[:3])
        subs = []
        for label, s in zip(("(i)", "(ii)", "(iii)"), sents[3:6] or sents[:3]):
            word, blanked = cloze(s)
            subs.append({"label": label, "text": f"Fill in the blank: {blanked}" if word else f"Why is the following important: {s}",
                         "marks": slot.marks / 3, "answer": {"text": word or s}})
        raw = {"text": passage, "sub_questions": subs, "answer": {"text": "See sub-part answers."}}
    if raw is None:
        return None
    raw["source_refs"] = refs
    if slot.internal_choice and allow_alt:
        alt_job = SlotJob(slot.model_copy(update={"internal_choice": False}), job.sources)
        raw["alternative"] = _text_question(alt_job, rng, allow_alt=False)
    return raw


_MATH_CODE_MAX_MARKS = {"mcq": 2, "fill_blank": 2, "true_false": 2, "very_short": 2, "short": 3, "numerical": 3}
_TEXT_CODE_MAX_MARKS = {"mcq": 1, "fill_blank": 2, "true_false": 2, "very_short": 2, "match_following": 4}


def math_code_ok(slot: Slot) -> bool:
    """Can a parametric template with an exact answer stand in for the model on this slot?"""
    cap = _MATH_CODE_MAX_MARKS.get(slot.question_type)
    if cap is None or slot.marks > cap or slot.difficulty == "hard" or slot.bloom in ("analyze", "evaluate", "create"):
        return False
    return bool(match_templates(slot.topic, slot.wants_diagram))


def text_code_ok(slot: Slot, sources) -> bool:
    """Factual recall items built from the learner's own sentences; anything needing judgement goes to the model."""
    cap = _TEXT_CODE_MAX_MARKS.get(slot.question_type)
    if cap is None or slot.marks > cap or slot.wants_diagram or slot.difficulty == "hard":
        return False
    if slot.bloom not in ("remember", "understand") or (slot.question_type == "mcq" and slot.difficulty != "easy"):
        return False
    sents = _sentences(sources)
    defs = _definitions(sents)
    if slot.question_type == "very_short":
        return bool(defs)
    if slot.question_type == "match_following":
        return len(defs) >= 4
    return len(sents) >= 3


class OfflineEngine(Engine):
    max_attempts = 10  # cheap to retry, and each retry draws fresh parameters
    multilingual = False  # its templates are English
    is_builtin = True
    similarity_limit = 0.9  # templated wording is shared by design; only reject near-identical items

    def __init__(self, seed: int = 0):
        self.seed = seed
        self._calls: Counter = Counter()

    def write_strict(self, ctx: GenContext, jobs: List[SlotJob], attempt: Dict[int, int]) -> Dict[int, Any]:
        """Only what code can do faithfully: no filler questions. Slots it cannot serve are simply omitted."""
        out: Dict[int, Any] = {}
        math = is_math_subject(ctx.request.subject)
        for job in jobs:
            rng = random.Random(f"{self.seed}:{job.slot.index}:{attempt.get(job.slot.index, 0)}:strict")
            try:
                raw = _math_question(job.slot, rng, strict=True) if math else _text_question(job, rng)
            except LookupError:
                raw = None
            if raw:
                raw["_origin"] = "code"
                raw["_verified"] = "construction" if math else "source"
                out[job.slot.index] = raw
        return out

    def write(self, ctx: GenContext, jobs: List[SlotJob], feedback: Dict[int, str]) -> Dict[int, Any]:
        out: Dict[int, Any] = {}
        math = is_math_subject(ctx.request.subject)
        for job in jobs:
            self._calls[job.slot.index] += 1
            rng = random.Random(f"{self.seed}:{job.slot.index}:{self._calls[job.slot.index]}")
            raw = None
            # A template is used when it really is about the topic, or when nothing was uploaded to base the question on.
            # With uploaded material and no matching template, the question must come from that material, not from the
            # template library.
            template_fits = bool(match_templates(job.slot.topic, job.slot.wants_diagram))
            if (math and (template_fits or not job.sources)) or (
                    not math and not job.sources and any(k in job.slot.topic.lower() for k in ("equation", "triangle", "circle"))):
                raw = _math_question(job.slot, rng)
            if raw is None:
                raw = _text_question(job, rng)
            if raw is None and job.sources:
                continue  # the material has nothing this writer can build the question from; say so rather than invent one
            if raw is None:
                raw = {"text": f"Define {job.slot.topic} and explain its importance.",
                       "answer": {"text": "Refer to the prescribed textbook; no source material was uploaded for this topic."}}
                if job.slot.question_type in ("mcq", "assertion_reason"):
                    continue
            raw.setdefault("_origin", "code")
            out[job.slot.index] = raw
        return out
