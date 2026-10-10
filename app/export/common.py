from __future__ import annotations

from typing import Dict, List

from app.exam.schema import PaperContent, PaperSection, Question, fmt_marks
from app.export.labels import ENGLISH, Labels

SET_LABELS = ["A", "B", "C", "D"]


def number_questions(content: PaperContent) -> Dict[str, int]:
    out: Dict[str, int] = {}
    n = 0
    for sec in content.sections:
        for q in sec.questions:
            n += 1
            out[q.id] = n
    return out


def section_display_id(sec_id: str) -> str:
    """'A2' is a continuation of Section A (patterns split one printed section into two question types)."""
    return sec_id[:-1] if len(sec_id) == 2 and sec_id[0].isalpha() and sec_id[1].isdigit() else sec_id


def section_heading(sec: PaperSection, lab: Labels = ENGLISH) -> str:
    return f"{lab.get('section')} {section_display_id(sec.id)}"


def section_marks_line(sec: PaperSection, lab: Labels = ENGLISH) -> str:
    each = fmt_marks(sec.marks_each)
    attempt = sec.effective_attempt()
    unit = lab.get("marks_word")
    if attempt < sec.count:
        line = (f"{lab.get('attempt', a=attempt, n=sec.count)} · {attempt} × {each} = "
                f"{fmt_marks(attempt * sec.marks_each)} {unit}")
    else:
        line = f"{sec.count} × {each} = {fmt_marks(sec.count * sec.marks_each)} {unit}"
    if sec.negative_marks:
        line += " · " + lab.get("negative", x=fmt_marks(sec.negative_marks))
    return line


def duration_text(minutes: int, lab: Labels = ENGLISH) -> str:
    return lab.duration(minutes)


def option_letter(i: int) -> str:
    return "abcd"[i] if 0 <= i < 4 else str(i + 1)


def question_marks_label(q: Question) -> str:
    return fmt_marks(q.marks)
