"""Built-in exam pattern presets.

Presets mirror the commonly published structure of each paper; boards revise these from time to
time, so every preset is a starting point that users can edit or save as their own pattern.
"""
from __future__ import annotations

from typing import Dict, List

from app.exam.schema import ExamPattern

_STD_INSTR = [
    "All questions are compulsory unless an internal choice is stated.",
    "Marks for each question are indicated against it.",
    "Write neat and legible answers; draw diagrams wherever necessary.",
]


def _p(**kw) -> ExamPattern:
    return ExamPattern(**kw)


def _sec(id_, title, qtype, count, marks, attempt=None, choice=0, neg=0.0, instr=""):
    return dict(id=id_, title=title, question_type=qtype, count=count, marks_each=marks,
                attempt=attempt, internal_choice=choice, negative_marks=neg, instruction=instr)


PRESETS: List[ExamPattern] = [
    _p(
        id="cbse-10-maths", name="CBSE Class 10 - Mathematics (80 marks)", board="CBSE", level="Secondary",
        description="Sections A-E: MCQ and assertion-reason, very short, short, long and case-based questions.",
        duration_minutes=180,
        instructions=[
            "This question paper contains five sections A, B, C, D and E.",
            "Section A: 20 questions of 1 mark each, including 2 assertion-reason questions.",
            "Section B: 5 questions of 2 marks each. Section C: 6 questions of 3 marks each.",
            "Section D: 4 questions of 5 marks each. Section E: 3 case-based questions of 4 marks each.",
            "There is no overall choice; internal choice is provided in some questions.",
            "Draw neat figures wherever required.",
        ],
        sections=[
            _sec("A", "Multiple Choice Questions", "mcq", 18, 1),
            _sec("A2", "Assertion and Reason", "assertion_reason", 2, 1,
                 instr="Questions 19 and 20 are Assertion (A) and Reason (R) type."),
            _sec("B", "Very Short Answer Questions", "very_short", 5, 2),
            _sec("C", "Short Answer Questions", "short", 6, 3, choice=2),
            _sec("D", "Long Answer Questions", "long", 4, 5, choice=2),
            _sec("E", "Case Study Based Questions", "case_study", 3, 4, instr="Each case study has three sub-parts."),
        ],
    ),
    _p(
        id="cbse-12-science", name="CBSE Class 12 - Science theory (70 marks)", board="CBSE", level="Senior Secondary",
        description="Physics / Chemistry / Biology style paper with five sections A-E.",
        duration_minutes=180,
        instructions=[
            "All questions are compulsory. There are 33 questions in all.",
            "Section A: 16 questions of 1 mark. Section B: 5 questions of 2 marks. Section C: 7 questions of 3 marks.",
            "Section D: 2 case-based questions of 4 marks. Section E: 3 questions of 5 marks.",
            "Internal choice is provided in some questions of Sections B, C and E.",
        ],
        sections=[
            _sec("A", "Multiple Choice Questions", "mcq", 14, 1),
            _sec("A2", "Assertion and Reason", "assertion_reason", 2, 1),
            _sec("B", "Very Short Answer Questions", "very_short", 5, 2, choice=1),
            _sec("C", "Short Answer Questions", "short", 7, 3, choice=2),
            _sec("D", "Case Study Based Questions", "case_study", 2, 4),
            _sec("E", "Long Answer Questions", "long", 3, 5, choice=3),
        ],
    ),
    _p(
        id="icse-10", name="ICSE Class 10 - Two-section paper (80 marks)", board="ICSE / ISC", level="Secondary",
        description="Section A compulsory (40 marks); Section B attempt any four of six (40 marks).",
        duration_minutes=150,
        instructions=[
            "Answers to this paper must be written on the paper provided separately.",
            "You will not be allowed to write during the first 15 minutes, which is reading time.",
            "Section A is compulsory. Attempt any four questions from Section B.",
            "The intended marks for questions or parts of questions are given in brackets [ ].",
        ],
        sections=[
            _sec("A", "Section A (compulsory)", "short", 10, 4, instr="Attempt all questions from this Section."),
            _sec("B", "Section B", "long", 6, 10, attempt=4, instr="Attempt any four questions from this Section."),
        ],
    ),
    _p(
        id="state-secondary-100", name="State Board - Secondary (100 marks)", board="State Board", level="Secondary",
        description="A generic state-board layout: objective, very short, short and long answer sections.",
        duration_minutes=180,
        instructions=_STD_INSTR + ["Section A carries 20 objective questions of 1 mark each."],
        sections=[
            _sec("A", "Objective Questions", "mcq", 20, 1),
            _sec("B", "Very Short Answer Questions", "very_short", 8, 2),
            _sec("C", "Short Answer Questions", "short", 6, 4, choice=3),
            _sec("D", "Long Answer Questions", "long", 4, 10, choice=4),
        ],
    ),
    _p(
        id="univ-semester-100", name="University Semester Exam (100 marks)", board="University", level="Undergraduate",
        description="Part A: ten 2-mark questions. Part B: five 13-mark either/or questions. Part C: one 15-mark either/or.",
        duration_minutes=180,
        instructions=[
            "Answer ALL questions.",
            "Part A carries 10 x 2 = 20 marks. Part B carries 5 x 13 = 65 marks. Part C carries 1 x 15 = 15 marks.",
            "In Parts B and C each question has an internal choice (either / or).",
        ],
        sections=[
            _sec("A", "Part A - Short Answer", "very_short", 10, 2),
            _sec("B", "Part B - Descriptive (either / or)", "long", 5, 13, choice=5),
            _sec("C", "Part C - Analytical (either / or)", "long", 1, 15, choice=1),
        ],
    ),
    _p(
        id="univ-internal-50", name="University Internal / Mid-term (50 marks)", board="University", level="Undergraduate",
        description="Short objective section followed by short and long answers with choice.",
        duration_minutes=90,
        instructions=["Section A is compulsory.", "Attempt any 4 questions from Section B and any 2 from Section C."],
        sections=[
            _sec("A", "Section A - Objective", "mcq", 10, 1),
            _sec("B", "Section B - Short Answer", "short", 6, 5, attempt=4),
            _sec("C", "Section C - Long Answer", "long", 3, 10, attempt=2),
        ],
    ),
    _p(
        id="jee-main", name="JEE Main style - single subject (100 marks)", board="NTA", level="Entrance",
        description="20 single-correct MCQs (-1 negative marking) and 5 numerical-value questions out of 10.",
        duration_minutes=60,
        instructions=[
            "Section A: 20 MCQs with one correct option; +4 for a correct answer, -1 for an incorrect answer.",
            "Section B: 10 numerical-value questions; attempt any 5. +4 for correct, no negative marking.",
        ],
        sections=[
            _sec("A", "Section A - Multiple choice", "mcq", 20, 4, neg=1),
            _sec("B", "Section B - Numerical value", "numerical", 10, 4, attempt=5,
                 instr="Attempt any 5. Answers are numerical values."),
        ],
    ),
    _p(
        id="neet", name="NEET style - single subject (180 marks)", board="NTA", level="Entrance",
        description="35 compulsory MCQs and 15 MCQs of which 10 are to be attempted; -1 negative marking.",
        duration_minutes=60,
        instructions=["Each question carries +4 marks; -1 mark is deducted for each incorrect answer."],
        sections=[
            _sec("A", "Section A", "mcq", 35, 4, neg=1),
            _sec("B", "Section B (attempt any 10)", "mcq", 15, 4, attempt=10, neg=1),
        ],
    ),
    _p(
        id="intl-80", name="International / IGCSE style (80 marks)", board="International", level="Secondary",
        description="Short answer, structured and extended response questions.",
        duration_minutes=120,
        instructions=["Answer all questions.", "The number of marks is given in brackets [ ] at the end of each question."],
        sections=[
            _sec("A", "Section A - Short answer", "very_short", 10, 2),
            _sec("B", "Section B - Structured", "short", 5, 4),
            _sec("C", "Section C - Extended response", "long", 4, 10),
        ],
    ),
    _p(
        id="unit-test-20", name="Unit Test (20 marks)", board="Any", level="School",
        description="A quick 40-minute class test.",
        duration_minutes=40,
        instructions=_STD_INSTR,
        sections=[
            _sec("A", "Objective", "mcq", 5, 1),
            _sec("B", "Short Answer", "very_short", 3, 2),
            _sec("C", "Long Answer", "short", 3, 3),
        ],
    ),
]

_BY_ID: Dict[str, ExamPattern] = {p.id: p for p in PRESETS}


def get_preset(pattern_id: str) -> ExamPattern:
    try:
        return _BY_ID[pattern_id].model_copy(deep=True)
    except KeyError:
        raise KeyError(f"Unknown pattern {pattern_id!r}")
