"""Parallel paper sets (A/B/C/D): same questions, different order, to discourage copying in a hall."""
from __future__ import annotations

import random
import re
from typing import List

from app.exam.schema import OBJECTIVE_TYPES, PaperContent

_POSITIONAL = ("all of the above", "none of the above", "both", "neither", "a and b", "b and c", "a, b", "all the above",
               "none of these")


def _can_shuffle(options: List[str]) -> bool:
    return not any(any(p in o.lower() for p in _POSITIONAL) for o in options)


def make_variant(content: PaperContent, label: str, seed: int) -> PaperContent:
    """Return a deep copy with questions reordered inside sections (and MCQ options shuffled) for set *label*."""
    out = content.model_copy(deep=True)
    out.meta.set_label = label
    if label == "A":
        return out
    rng = random.Random(f"{seed}:{label}")
    for sec in out.sections:
        # Question ids, not positions, identify questions, so reordering never affects the answer key.
        if sec.question_type in OBJECTIVE_TYPES or sec.count > 1:
            rng.shuffle(sec.questions)
        for q in sec.questions:
            if q.type == "mcq" and len(q.options) == 4 and _can_shuffle(q.options) and q.answer.correct_option is not None:
                order = list(range(4))
                rng.shuffle(order)
                correct = q.answer.correct_option
                q.options = [q.options[i] for i in order]
                q.answer.correct_option = order.index(correct)
                if re.match(r"^\([a-dA-D]\) ", q.answer.text):
                    q.answer.text = f"({'abcd'[q.answer.correct_option]}) {q.options[q.answer.correct_option]}"
    return out
