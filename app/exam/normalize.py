"""Turns loosely-structured model output into validated ``Question`` objects."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from app.diagrams import DiagramError, check_diagram
from app.exam.schema import (
    Alternative, Answer, Match, Question, Slot, Step, SubQuestion, Table,
)

STANDARD_AR_OPTIONS = [
    "Both A and R are true and R is the correct explanation of A.",
    "Both A and R are true but R is not the correct explanation of A.",
    "A is true but R is false.",
    "A is false but R is true.",
]
_OPT_PREFIX = re.compile(r"^\s*\(?[A-Da-d1-4][\).:]\s+")
_LETTERS = "ABCD"


class QuestionInvalid(ValueError):
    """Raised with a message that is fed back to the model on retry."""


class VerificationFailed(QuestionInvalid):
    """The answer key contradicts a code-side check. Kept distinct so the last attempt can still be flagged and used."""


def _s(value: Any, limit: int = 6000) -> str:
    return "" if value is None else str(value).strip()[:limit]


def split_units(total: float, weights: List[float]) -> List[float]:
    """Split *total* marks over *weights* in half-mark units so the parts always sum to *total*."""
    units = int(round(total * 2))
    n = len(weights)
    if n == 0:
        return []
    w = [max(x, 0.0) for x in weights]
    if sum(w) <= 0:
        w = [1.0] * n
    s = sum(w)
    raw = [units * x / s for x in w]
    base = [int(r) for r in raw]
    rest = units - sum(base)
    order = sorted(range(n), key=lambda i: raw[i] - base[i], reverse=True)
    for i in order[:rest]:
        base[i] += 1
    return [b / 2 for b in base]


def _steps(raw: Any, total: float) -> List[Step]:
    steps: List[Step] = []
    for item in raw or []:
        if isinstance(item, dict):
            text = _s(item.get("text") or item.get("step"))
            try:
                marks = float(item.get("marks", 0) or 0)
            except (TypeError, ValueError):
                marks = 0.0
        else:
            text, marks = _s(item), 0.0
        if text:
            steps.append(Step(text=text, marks=marks))
    if not steps or total <= 0:
        return steps
    alloc = split_units(total, [s.marks for s in steps])
    return [Step(text=s.text, marks=m) for s, m in zip(steps, alloc)]


def _diagram(raw: Any, what: str, required: bool) -> Optional[Dict[str, Any]]:
    if not raw:
        return None
    try:
        return check_diagram(raw)
    except DiagramError as exc:
        if required:
            raise QuestionInvalid(f"{what} diagram is invalid: {exc}")
        return None


def _answer(raw: Any, total: float, objective: bool, what: str = "answer") -> Answer:
    raw = raw if isinstance(raw, dict) else {"text": raw}
    text = _s(raw.get("text") or raw.get("final"))
    steps = [] if objective else _steps(raw.get("steps"), total)
    if not objective and text and not steps:
        steps = [Step(text=text, marks=total)]
    co = raw.get("correct_option")
    if isinstance(co, str):
        m = re.match(r"^\(?([A-Da-d])\)?\b", co.strip())
        co = _LETTERS.index(m.group(1).upper()) if m else None
    elif isinstance(co, bool) or not isinstance(co, (int, float)):
        co = None
    return Answer(text=text, steps=steps, correct_option=int(co) if co is not None else None,
                  diagram=_diagram(raw.get("diagram"), f"{what}", required=False))


def _options(raw: Any) -> List[str]:
    return [_OPT_PREFIX.sub("", _s(o, 500)) for o in raw or [] if _s(o)]


def _table(raw: Any) -> Optional[Table]:
    if not isinstance(raw, dict) or not raw.get("rows"):
        return None
    headers = [_s(h, 200) for h in raw.get("headers") or []]
    rows = [[_s(c, 200) for c in r] for r in raw["rows"] if isinstance(r, list)][:40]
    return Table(headers=headers, rows=rows) if rows else None


def _sub_questions(raw: Any, total: float) -> List[SubQuestion]:
    subs: List[SubQuestion] = []
    for i, item in enumerate(raw or []):
        if not isinstance(item, dict) or not _s(item.get("text")):
            continue
        try:
            marks = float(item.get("marks", 1) or 1)
        except (TypeError, ValueError):
            marks = 1.0
        subs.append(SubQuestion(label=_s(item.get("label"), 10) or f"({i + 1})", text=_s(item["text"]),
                                marks=marks, options=_options(item.get("options")),
                                answer=_answer(item.get("answer"), marks, bool(item.get("options")), "sub-answer")))
    if subs:
        alloc = split_units(total, [s.marks for s in subs])
        for s, m in zip(subs, alloc):
            s.marks = m
            if not s.options and s.answer.steps:
                s.answer.steps = _steps([x.model_dump() for x in s.answer.steps], m)
    return subs


def normalise_question(raw: Any, slot: Slot, qid: str) -> Question:
    if not isinstance(raw, dict):
        raise QuestionInvalid("Each question must be a JSON object.")
    qtype = slot.question_type
    objective = qtype in ("mcq", "assertion_reason", "true_false", "fill_blank", "match_following")
    text = _s(raw.get("text"))
    if len(text) < 8:
        raise QuestionInvalid("Question text is missing or too short.")

    options = _options(raw.get("options"))
    match: Optional[Match] = None
    subs: List[SubQuestion] = []
    answer = _answer(raw.get("answer"), slot.marks, qtype in ("mcq", "assertion_reason", "true_false"))

    if qtype == "mcq":
        if len(options) != 4 or len(set(o.lower() for o in options)) != 4:
            raise QuestionInvalid("MCQ needs exactly 4 distinct options.")
    elif qtype == "assertion_reason":
        options = list(STANDARD_AR_OPTIONS)
        if "assertion" not in text.lower() or "reason" not in text.lower():
            raise QuestionInvalid("Assertion-reason text must state 'Assertion (A): ...' and 'Reason (R): ...'.")
    if qtype in ("mcq", "assertion_reason"):
        co = answer.correct_option
        if co is None or not 0 <= co < 4:
            raise QuestionInvalid("answer.correct_option must be the 0-based index of the correct option.")
        if not answer.text:
            answer.text = f"({_LETTERS[co]}) {options[co]}"
    elif qtype == "match_following":
        m = raw.get("match") or {}
        left, right = [_s(x, 300) for x in m.get("left", [])], [_s(x, 300) for x in m.get("right", [])]
        if len(left) < 3 or len(left) != len(right):
            raise QuestionInvalid("match_following needs 'match' with equal-length 'left' and 'right' lists (>=3).")
        match = Match(left=left, right=right)
    elif qtype == "case_study":
        subs = _sub_questions(raw.get("sub_questions"), slot.marks)
        if len(subs) < 2:
            raise QuestionInvalid("case_study needs at least 2 sub_questions.")
        answer = Answer(text=answer.text, steps=[], correct_option=None, diagram=answer.diagram)
    if qtype != "case_study" and not (answer.text or answer.steps):
        raise QuestionInvalid("Missing model answer.")

    diagram = _diagram(raw.get("diagram"), "question", required=slot.wants_diagram)

    alternative: Optional[Alternative] = None
    alt_raw = raw.get("alternative")
    if isinstance(alt_raw, dict) and _s(alt_raw.get("text")):
        a_subs = _sub_questions(alt_raw.get("sub_questions"), slot.marks) if qtype == "case_study" else []
        alternative = Alternative(
            text=_s(alt_raw["text"]), diagram=_diagram(alt_raw.get("diagram"), "alternative", required=False),
            table=_table(alt_raw.get("table")),
            options=STANDARD_AR_OPTIONS if qtype == "assertion_reason" else _options(alt_raw.get("options")),
            sub_questions=a_subs,
            answer=_answer(alt_raw.get("answer"), slot.marks, qtype in ("mcq", "assertion_reason", "true_false")))
    if slot.internal_choice and alternative is None:
        raise QuestionInvalid("This question needs an 'alternative' (the OR choice) of equal marks and difficulty.")

    refs = [_s(r, 40) for r in raw.get("source_refs") or [] if _s(r)][:6]
    return Question(
        id=qid, type=qtype, text=text, marks=slot.marks, difficulty=slot.difficulty, bloom=slot.bloom,
        topic=slot.topic, options=options, diagram=diagram, table=_table(raw.get("table")), match=match,
        sub_questions=subs, answer=answer, alternative=alternative, source_refs=refs,
        origin=_s(raw.get("_origin"), 20), verified=_s(raw.get("_verified"), 20),
    )


def _leaf_values(node) -> List[str]:
    if isinstance(node, dict):
        return [v for item in node.values() for v in _leaf_values(item)]
    if isinstance(node, (list, tuple)):
        return [v for item in node for v in _leaf_values(item)]
    return [] if node is None or isinstance(node, bool) else [str(node)]


def question_text_blob(q: Question) -> str:
    parts = [q.text] + q.options + [s.text for s in q.sub_questions]
    if q.diagram:  # figure-based questions often carry their numbers only in the figure
        parts += _leaf_values(q.diagram)
    return " ".join(parts)
