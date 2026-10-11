"""Fills slots with questions copied verbatim from the uploaded source document(s) -- no AI, no fabrication.

A slot whose type has no unused extracted item left (or whose "OR" alternative cannot be found) is simply not
served, exactly like CodeOnlyEngine leaves gaps for templates it has no coverage for; the teacher can write
the rest by hand or switch to Automatic/AI writing for them.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

from app.exam.llm_engine import Engine, GenContext, SlotJob


class ExtractEngine(Engine):
    max_attempts = 3  # a mismatched pick fails schema validation harmlessly; a retry can find a better one
    similarity_limit = 0.99  # identical wording to the source is the whole point; never reject it as "too similar"
    multilingual = False  # copied verbatim in whatever language the source document is written in
    is_builtin = True
    missing_reason = ("No more usable questions were found in the source document(s) for this slot (objective "
                      "questions need a printed answer key next to them). Add more material, write it by hand, "
                      "or switch to Automatic/AI writing for the rest.")

    def __init__(self, items: List[Dict[str, Any]]):
        self._pool = items
        self._used: Set[int] = set()

    def _find(self, qtype: str, exclude: frozenset = frozenset()) -> Optional[int]:
        for i, item in enumerate(self._pool):
            if i not in self._used and i not in exclude and item.get("type") == qtype:
                return i
        for i, item in enumerate(self._pool):
            if i not in self._used and i not in exclude:
                return i
        return None

    def write(self, ctx: GenContext, jobs: List[SlotJob], feedback: Dict[int, str]) -> Dict[int, Any]:
        out: Dict[int, Any] = {}
        for job in jobs:
            i = self._find(job.slot.question_type)
            if i is None:
                continue
            alt_i = None
            if job.slot.internal_choice:
                alt_i = self._find(job.slot.question_type, exclude=frozenset({i}))
                if alt_i is None:
                    continue  # no second question available for the OR-choice; leave this slot unfilled
            self._used.add(i)
            raw = dict(self._pool[i])
            if alt_i is not None:
                self._used.add(alt_i)
                raw["alternative"] = dict(self._pool[alt_i])
            raw["_origin"] = "source"
            out[job.slot.index] = raw
        return out
