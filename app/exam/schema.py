"""Data model shared by pattern presets, the generator, the API and the exporters."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

QUESTION_TYPES = [
    "mcq", "assertion_reason", "fill_blank", "true_false", "match_following",
    "very_short", "short", "long", "numerical", "case_study",
]
OBJECTIVE_TYPES = {"mcq", "assertion_reason", "fill_blank", "true_false", "match_following"}
DIFFICULTIES = ["easy", "medium", "hard"]
BLOOMS = ["remember", "understand", "apply", "analyze", "evaluate", "create"]

QUESTION_TYPE_LABELS = {
    "mcq": "Multiple choice",
    "assertion_reason": "Assertion & reason",
    "fill_blank": "Fill in the blanks",
    "true_false": "True / false",
    "match_following": "Match the following",
    "very_short": "Very short answer",
    "short": "Short answer",
    "long": "Long answer",
    "numerical": "Numerical / problem",
    "case_study": "Case / source based",
}


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore")


# ----------------------------------------------------------------- pattern

class SectionSpec(_Model):
    id: str = Field(min_length=1, max_length=8)
    title: str = Field(default="", max_length=120)
    question_type: str
    count: int = Field(ge=1, le=100)
    attempt: Optional[int] = Field(default=None, ge=1)
    marks_each: float = Field(gt=0, le=100)
    internal_choice: int = Field(default=0, ge=0)
    negative_marks: float = Field(default=0, ge=0)
    instruction: str = Field(default="", max_length=500)

    @field_validator("question_type")
    @classmethod
    def _known_type(cls, v: str) -> str:
        if v not in QUESTION_TYPES:
            raise ValueError(f"question_type must be one of {QUESTION_TYPES}")
        return v

    def effective_attempt(self) -> int:
        return min(self.attempt or self.count, self.count)

    @property
    def total_marks(self) -> float:
        return self.effective_attempt() * self.marks_each


class ExamPattern(_Model):
    id: str = ""
    name: str = Field(max_length=200)
    board: str = ""
    level: str = ""
    description: str = ""
    duration_minutes: int = Field(default=180, ge=5, le=600)
    instructions: List[str] = Field(default_factory=list)
    sections: List[SectionSpec] = Field(min_length=1, max_length=12)

    @property
    def total_marks(self) -> float:
        return sum(s.total_marks for s in self.sections)


# ----------------------------------------------------------------- questions

class Step(_Model):
    text: str
    marks: float = 0


class Answer(_Model):
    text: str = ""
    steps: List[Step] = Field(default_factory=list)
    correct_option: Optional[int] = None
    diagram: Optional[Dict[str, Any]] = None


class Table(_Model):
    headers: List[str] = Field(default_factory=list)
    rows: List[List[str]] = Field(default_factory=list)


class Match(_Model):
    left: List[str] = Field(default_factory=list)
    right: List[str] = Field(default_factory=list)


class SubQuestion(_Model):
    label: str = ""
    text: str
    marks: float = 1
    options: List[str] = Field(default_factory=list)
    answer: Answer = Field(default_factory=Answer)


class Alternative(_Model):
    """The 'OR' choice shown beneath a question."""

    text: str
    diagram: Optional[Dict[str, Any]] = None
    table: Optional[Table] = None
    options: List[str] = Field(default_factory=list)
    sub_questions: List[SubQuestion] = Field(default_factory=list)
    answer: Answer = Field(default_factory=Answer)


class Question(_Model):
    id: str
    type: str
    text: str
    marks: float
    difficulty: str = "medium"
    bloom: str = "understand"
    topic: str = ""
    options: List[str] = Field(default_factory=list)
    diagram: Optional[Dict[str, Any]] = None
    table: Optional[Table] = None
    match: Optional[Match] = None
    sub_questions: List[SubQuestion] = Field(default_factory=list)
    answer: Answer = Field(default_factory=Answer)
    alternative: Optional[Alternative] = None
    source_refs: List[str] = Field(default_factory=list)
    # Provenance: who wrote it (model | code | bank) and how far its answer was machine-checked.
    origin: str = ""
    verified: str = ""  # construction | source | checked | flagged | ""
    origin_ref: str = ""  # bank question id when origin == bank

    @field_validator("difficulty")
    @classmethod
    def _difficulty(cls, v: str) -> str:
        v = (v or "medium").lower()
        return v if v in DIFFICULTIES else "medium"

    @field_validator("bloom")
    @classmethod
    def _bloom(cls, v: str) -> str:
        v = (v or "understand").lower()
        return v if v in BLOOMS else "understand"


class PaperSection(_Model):
    id: str
    title: str = ""
    instruction: str = ""
    question_type: str
    marks_each: float
    count: int
    attempt: Optional[int] = None
    negative_marks: float = 0
    questions: List[Question] = Field(default_factory=list)

    def effective_attempt(self) -> int:
        return min(self.attempt or self.count, self.count)


class PaperMeta(_Model):
    institution: str = ""
    title: str = ""
    exam_name: str = ""
    subject: str = ""
    class_level: str = ""
    board: str = ""
    date: str = ""
    language: str = "English"
    duration_minutes: int = 180
    total_marks: float = 0
    paper_code: str = ""
    instructions: List[str] = Field(default_factory=list)
    set_label: str = ""


class PaperContent(_Model):
    meta: PaperMeta
    sections: List[PaperSection] = Field(default_factory=list)


# ----------------------------------------------------------------- request

class TopicSpec(_Model):
    name: str = Field(min_length=1, max_length=300)
    weight: float = Field(default=5, ge=0, le=100)
    unit: str = Field(default="", max_length=200)


class DifficultyMix(_Model):
    easy: float = Field(default=30, ge=0, le=100)
    medium: float = Field(default=50, ge=0, le=100)
    hard: float = Field(default=20, ge=0, le=100)

    def normalised(self) -> Dict[str, float]:
        total = self.easy + self.medium + self.hard
        if total <= 0:
            return {"easy": 1 / 3, "medium": 1 / 3, "hard": 1 / 3}
        return {"easy": self.easy / total, "medium": self.medium / total, "hard": self.hard / total}


class GenerationRequest(_Model):
    title: str = Field(default="", max_length=300)
    institution: str = Field(default="", max_length=200)
    subject: str = Field(min_length=1, max_length=200)
    class_level: str = Field(default="", max_length=100)
    board: str = Field(default="", max_length=100)
    exam_name: str = Field(default="", max_length=200)
    date: str = Field(default="", max_length=60)
    language: str = Field(default="English", max_length=40)
    pattern_id: Optional[str] = None
    pattern: Optional[ExamPattern] = None
    duration_minutes: Optional[int] = Field(default=None, ge=5, le=600)
    document_ids: List[str] = Field(default_factory=list, max_length=20)
    topics: List[TopicSpec] = Field(default_factory=list, max_length=80)
    syllabus_text: str = Field(default="", max_length=20000)
    difficulty: DifficultyMix = Field(default_factory=DifficultyMix)
    diagram_mode: str = "auto"  # none | auto | more
    extra_instructions: str = Field(default="", max_length=2000)
    num_sets: int = Field(default=1, ge=1, le=4)
    seed: Optional[int] = None
    avoid_repeats: bool = True
    writer_mode: str = "auto"  # auto | code_only | ai_all | extract_source
    reuse_bank: bool = False  # fill slots from the user's question bank before writing new questions

    @field_validator("writer_mode")
    @classmethod
    def _writer_mode(cls, v: str) -> str:
        if v not in ("auto", "code_only", "ai_all", "extract_source"):
            raise ValueError("writer_mode must be auto, code_only, ai_all or extract_source")
        return v

    @field_validator("diagram_mode")
    @classmethod
    def _diagram_mode(cls, v: str) -> str:
        if v not in ("none", "auto", "more"):
            raise ValueError("diagram_mode must be none, auto or more")
        return v


# ----------------------------------------------------------------- blueprint

class Slot(_Model):
    index: int
    section_id: str
    question_type: str
    marks: float
    difficulty: str
    bloom: str
    topic: str
    wants_diagram: bool = False
    internal_choice: bool = False


def fmt_marks(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:g}"
