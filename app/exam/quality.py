from __future__ import annotations

import hashlib
import re
from typing import Dict, List, Optional, Set, Tuple

from app.exam.normalize import question_text_blob
from app.exam.schema import ExamPattern, GenerationRequest, PaperContent, Slot

from app.textutil import WORD as _TOKEN


def text_tokens(text: str) -> Set[str]:
    # Digits count even when short: "Solve 3x + 5 = 20" and "Solve 4x + 7 = 31" are different problems.
    return {t for t in _TOKEN.findall(text.lower()) if len(t) > 2 or any(c.isdigit() for c in t)}


def jaccard(a: Set[str], b: Set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def text_hash(text: str) -> str:
    norm = " ".join(_TOKEN.findall(text.lower()))
    return hashlib.sha256(norm.encode()).hexdigest()


def analyse(content: PaperContent, pattern: ExamPattern, req: GenerationRequest, slots: List[Slot],
            failed: Dict[int, str], bank_hashes: Set[str], similarity_limit: float = 0.72) -> Dict:
    warnings: List[str] = []
    qs = [(s.id, q) for s in content.sections for q in s.questions]
    placeholders = {q.id for _, q in qs if q.id.startswith("q") and q.id[1:].isdigit() and int(q.id[1:]) - 1 in failed}

    sections = []
    for sec_spec, sec in zip(pattern.sections, content.sections):
        sections.append({"id": sec.id, "questions": len(sec.questions), "expected": sec_spec.count,
                         "marks": sec_spec.total_marks})

    marks_total = sum(q.marks for _, q in qs) or 1
    diff_actual = {"easy": 0.0, "medium": 0.0, "hard": 0.0}
    for _, q in qs:
        diff_actual[q.difficulty] = diff_actual.get(q.difficulty, 0) + q.marks
    target = req.difficulty.normalised()
    diff = {k: round(v / marks_total * 100, 1) for k, v in diff_actual.items()}
    drift = sum(abs(diff.get(k, 0) - target[k] * 100) for k in target) / 2

    topics: Dict[str, Dict[str, float]] = {}
    for _, q in qs:
        t = topics.setdefault(q.topic or req.subject, {"questions": 0, "marks": 0})
        t["questions"] += 1
        t["marks"] += q.marks
    requested = [t.name for t in req.topics]
    uncovered = [t for t in requested if t not in topics]

    toks = [(q.id, text_tokens(question_text_blob(q))) for _, q in qs if q.id not in placeholders]
    dups = []
    for i in range(len(toks)):
        for j in range(i + 1, len(toks)):
            sim = jaccard(toks[i][1], toks[j][1])
            if sim >= similarity_limit:
                dups.append({"a": toks[i][0], "b": toks[j][0], "similarity": round(sim, 2)})
    # Questions the user chose to reuse from the bank are repeats by design, not a warning.
    bank_hits = [q.id for _, q in qs if q.origin != "bank" and text_hash(question_text_blob(q)) in bank_hashes]

    missing = []
    for _, q in qs:
        if q.id in placeholders:
            continue
        has_answer = bool(q.answer.text or q.answer.steps or q.answer.correct_option is not None
                          or any(s.answer.text for s in q.sub_questions))
        if not has_answer:
            missing.append(q.id)

    writers: Dict[str, int] = {}
    verification: Dict[str, int] = {}
    flagged = []
    for _, q in qs:
        if q.id in placeholders:
            continue
        writers[q.origin or "model"] = writers.get(q.origin or "model", 0) + 1
        verification[q.verified or "unchecked"] = verification.get(q.verified or "unchecked", 0) + 1
        if q.verified == "flagged":
            flagged.append(q.id)

    wanted = {s.index + 1 for s in slots if s.wants_diagram}
    delivered = sum(1 for _, q in qs if q.diagram and int(q.id[1:] or 0) in wanted) if wanted else 0

    score = 100.0 - 12 * len(failed) - 6 * len(dups) - 8 * len(missing) - 3 * len(bank_hits) \
        - 2 * max(len(wanted) - delivered, 0) - max(drift - 10, 0) * 0.5 - 4 * len(uncovered) - 8 * len(flagged)
    score = max(0, round(score))

    if failed:
        warnings.append(f"{len(failed)} question(s) could not be generated; use Regenerate on the flagged items.")
    if dups:
        warnings.append(f"{len(dups)} pair(s) of questions look too similar.")
    if bank_hits:
        warnings.append(f"{len(bank_hits)} question(s) repeat earlier papers in your question bank.")
    if missing:
        warnings.append(f"{len(missing)} question(s) have no model answer.")
    if wanted and delivered < len(wanted):
        warnings.append(f"{len(wanted) - delivered} planned diagram(s) are missing.")
    if flagged:
        warnings.append(f"{len(flagged)} answer key(s) failed a computer check ({', '.join(flagged[:6])}); review them.")
    if drift > 15:
        warnings.append("Difficulty mix differs noticeably from the requested split.")
    if uncovered:
        warnings.append("Topics without a question: " + ", ".join(uncovered[:6]))

    return {
        "score": score, "status": "pass" if score >= 85 and not failed and not flagged else "review",
        "sections": sections, "difficulty": {"target": {k: round(v * 100, 1) for k, v in target.items()}, "actual": diff},
        "topics": topics, "uncovered_topics": uncovered, "duplicates": dups, "bank_repeats": bank_hits,
        "missing_answers": missing, "failed": [{"slot": k, "error": v} for k, v in failed.items()],
        "diagrams": {"planned": len(wanted), "delivered": delivered},
        "writers": writers, "verification": {"counts": verification, "flagged": flagged},
        "warnings": warnings,
    }
