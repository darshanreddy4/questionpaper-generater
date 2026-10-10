"""Turns a pattern + user preferences into an ordered list of question slots.

A slot fixes everything about a question that should not be left to chance: section, type, marks,
difficulty, Bloom level, topic and whether it should carry a diagram. Fixing these up-front is
what lets the paper hit its difficulty mix and syllabus coverage no matter which model writes
the text.
"""
from __future__ import annotations

import random
import re
from typing import Dict, List, Optional, Tuple

from app.exam.schema import (
    OBJECTIVE_TYPES, ExamPattern, GenerationRequest, Slot, TopicSpec,
)

_DIAGRAM_SUBJECT_HINTS = (
    "math", "geometry", "algebra", "calculus", "trigonometry", "statistics", "physics", "chemistry",
    "biology", "science", "geography", "economics", "engineering", "mechanics", "electronics", "circuit",
    "computer", "data structure", "graph",
)
_DIAGRAM_TOPIC_HINTS = (
    "triangle", "circle", "geometry", "graph", "coordinate", "mensuration", "trigonometr", "angle", "area",
    "volume", "statistic", "histogram", "venn", "set", "probability", "ray", "lens", "mirror", "circuit",
    "force", "wave", "optics", "cell", "diagram", "structure", "locus", "vector", "plot", "function",
    "linear programming", "tree", "network", "lines",
)
_DIAGRAM_TYPES = {"mcq", "very_short", "short", "long", "numerical", "case_study"}

_BLOOM_BY_DIFFICULTY = {
    "easy": ["remember", "understand"],
    "medium": ["understand", "apply"],
    "hard": ["apply", "analyze", "evaluate"],
}
_BLOOM_TYPE_FLOOR = {  # long-form types should not be pure recall
    "long": ["apply", "analyze", "evaluate"],
    "case_study": ["analyze", "apply", "evaluate"],
    "numerical": ["apply", "analyze"],
}


def subject_supports_diagrams(subject: str) -> bool:
    s = subject.lower()
    return any(h in s for h in _DIAGRAM_SUBJECT_HINTS)


def is_math_subject(subject: str) -> bool:
    s = subject.lower()
    return any(h in s for h in ("math", "algebra", "calculus", "geometry", "trigonometry", "statistics"))


def parse_syllabus_text(text: str) -> List[TopicSpec]:
    """Heuristic outline parser: one topic per line; 'Unit N: Title' lines become the unit for what follows."""
    topics: List[TopicSpec] = []
    unit = ""
    for raw in text.splitlines():
        line = raw.strip().lstrip("-*\u2022\u25cf\u25aa>").strip()
        line = re.sub(r"^\d+(\.\d+)*[.)]\s+", "", line)
        if len(line) < 2:
            continue
        m = re.match(r"^(unit|chapter|module|section|part)\s*[-\w.]*\s*[:.\-\u2013\u2014]\s*(.+)$", line, re.I)
        if m and not raw.startswith((" ", "\t")):
            unit = m.group(2).strip()
            topics.append(TopicSpec(name=unit[:300], unit=unit[:200]))
            continue
        parts = [p.strip() for p in re.split(r"[;,]\s+(?=[A-Z])", line) if p.strip()] if len(line) > 60 else [line]
        for p in parts:
            topics.append(TopicSpec(name=p[:300], unit=unit[:200]))
        if len(topics) >= 80:
            break
    return topics[:80]


def _assign_difficulty(slots: List[Slot], mix: Dict[str, float], rng: random.Random) -> None:
    total = sum(s.marks for s in slots)
    target = {k: v * total for k, v in mix.items()}
    given = {k: 0.0 for k in mix}
    order = list(range(len(slots)))
    # Larger questions are placed first so they pick the scarce levels instead of absorbing leftovers.
    order.sort(key=lambda i: (-slots[i].marks, rng.random()))
    for i in order:
        slot = slots[i]
        best = max(mix, key=lambda k: (target[k] - given[k], mix[k]))
        slot.difficulty = best
        given[best] += slot.marks


def _assign_topics(slots: List[Slot], topics: List[TopicSpec], fallback: str, rng: random.Random) -> None:
    if not topics:
        for s in slots:
            s.topic = fallback
        return
    weights = [max(t.weight, 0.01) for t in topics]
    total_w = sum(weights)
    total_marks = sum(s.marks for s in slots)
    target = [w / total_w * total_marks for w in weights]
    given = [0.0] * len(topics)
    last_by_section: Dict[str, int] = {}
    order = list(range(len(slots)))
    order.sort(key=lambda i: (-slots[i].marks, rng.random()))
    for i in order:
        slot = slots[i]
        ranked = sorted(range(len(topics)), key=lambda k: (-(target[k] - given[k]), rng.random()))
        pick = ranked[0]
        if len(topics) > 1 and last_by_section.get(slot.section_id) == pick:
            pick = ranked[1]  # avoid back-to-back duplicates inside one section
        last_by_section[slot.section_id] = pick
        slot.topic = topics[pick].name
        given[pick] += slot.marks


def _assign_blooms(slots: List[Slot]) -> None:
    counters: Dict[Tuple[str, str], int] = {}
    for s in slots:
        pool = _BLOOM_BY_DIFFICULTY[s.difficulty]
        floor = _BLOOM_TYPE_FLOOR.get(s.question_type)
        if floor:
            pool = [b for b in floor if b in pool] or floor
        n = counters.get((s.difficulty, s.question_type), 0)
        counters[(s.difficulty, s.question_type)] = n + 1
        s.bloom = pool[n % len(pool)]


def _assign_diagrams(slots: List[Slot], req: GenerationRequest, rng: random.Random) -> None:
    if req.diagram_mode == "none" or not subject_supports_diagrams(req.subject):
        return
    share = 0.5 if req.diagram_mode == "more" else 0.25
    eligible = [s for s in slots if s.question_type in _DIAGRAM_TYPES]
    if not eligible:
        return
    want = max(1, round(len(eligible) * share))

    def score(s: Slot) -> float:
        topic = s.topic.lower()
        hit = any(h in topic for h in _DIAGRAM_TOPIC_HINTS)
        # Diagram-heavy topics first; objective one-markers are the cheapest to leave without a figure.
        return (2.0 if hit else 0.0) + (0.5 if s.marks >= 3 else 0.0) + rng.random() * 0.4

    for s in sorted(eligible, key=score, reverse=True)[:want]:
        s.wants_diagram = True


def build_blueprint(req: GenerationRequest, pattern: ExamPattern, topics: Optional[List[TopicSpec]] = None) -> List[Slot]:
    rng = random.Random(req.seed if req.seed is not None else 0xE0A1)
    slots: List[Slot] = []
    for sec in pattern.sections:
        choice_idx = set(rng.sample(range(sec.count), min(sec.internal_choice, sec.count)))
        for n in range(sec.count):
            slots.append(Slot(
                index=len(slots), section_id=sec.id, question_type=sec.question_type, marks=sec.marks_each,
                difficulty="medium", bloom="understand", topic="", internal_choice=n in choice_idx,
            ))
    chosen_topics = topics if topics is not None else req.topics
    _assign_difficulty(slots, req.difficulty.normalised(), rng)
    _assign_topics(slots, chosen_topics, req.subject, rng)
    _assign_blooms(slots)
    _assign_diagrams(slots, req, rng)
    return slots


def blueprint_summary(slots: List[Slot], pattern: ExamPattern) -> Dict:
    total = sum(s.marks for s in slots if s.section_id in {sec.id for sec in pattern.sections}) or 1
    diff: Dict[str, float] = {}
    topic_marks: Dict[str, float] = {}
    for s in slots:
        diff[s.difficulty] = diff.get(s.difficulty, 0) + s.marks
        topic_marks[s.topic] = topic_marks.get(s.topic, 0) + s.marks
    return {
        "questions": len(slots),
        "total_marks": pattern.total_marks,
        "difficulty_share": {k: round(v / total * 100, 1) for k, v in diff.items()},
        "topic_marks": topic_marks,
        "diagram_slots": sum(1 for s in slots if s.wants_diagram),
    }


def is_objective(question_type: str) -> bool:
    return question_type in OBJECTIVE_TYPES
