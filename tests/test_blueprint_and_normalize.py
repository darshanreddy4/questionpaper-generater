import pytest

from app.exam.blueprint import build_blueprint, parse_syllabus_text
from app.exam.normalize import QuestionInvalid, normalise_question, split_units
from app.exam.patterns import PRESETS
from app.exam.schema import GenerationRequest, Slot, TopicSpec
from app.llm.base import extract_json

EXPECTED_MARKS = {"cbse-10-maths": 80, "cbse-12-science": 70, "icse-10": 80, "state-secondary-100": 100,
                  "univ-semester-100": 100, "univ-internal-50": 50, "jee-main": 100, "neet": 180,
                  "intl-80": 80, "unit-test-20": 20}


@pytest.mark.parametrize("preset", PRESETS, ids=lambda p: p.id)
def test_preset_totals_match_published_marks(preset):
    assert preset.total_marks == EXPECTED_MARKS[preset.id]
    ids = [s.id for s in preset.sections]
    assert len(ids) == len(set(ids))


def _request(**kw):
    base = dict(subject="Physics", seed=3,
                topics=[TopicSpec(name="Optics", weight=6), TopicSpec(name="Motion", weight=3), TopicSpec(name="Heat", weight=1)])
    base.update(kw)
    return GenerationRequest(**base)


def test_blueprint_hits_difficulty_and_topic_weights():
    pattern = next(p for p in PRESETS if p.id == "state-secondary-100")
    req = _request(difficulty={"easy": 40, "medium": 40, "hard": 20})
    slots = build_blueprint(req, pattern)
    total = sum(s.marks for s in slots)
    by_diff = {d: sum(s.marks for s in slots if s.difficulty == d) / total for d in ("easy", "medium", "hard")}
    assert abs(by_diff["easy"] - 0.4) < 0.08 and abs(by_diff["hard"] - 0.2) < 0.08
    by_topic = {t: sum(s.marks for s in slots if s.topic == t) / total for t in ("Optics", "Motion", "Heat")}
    assert by_topic["Optics"] > by_topic["Motion"] > by_topic["Heat"] > 0


def test_blueprint_is_deterministic_and_marks_internal_choice():
    pattern = next(p for p in PRESETS if p.id == "cbse-10-maths")
    a = build_blueprint(_request(subject="Mathematics"), pattern)
    b = build_blueprint(_request(subject="Mathematics"), pattern)
    assert [s.model_dump() for s in a] == [s.model_dump() for s in b]
    assert sum(s.internal_choice for s in a) == sum(sec.internal_choice for sec in pattern.sections)


def test_diagrams_only_planned_for_diagram_subjects():
    pattern = next(p for p in PRESETS if p.id == "unit-test-20")
    assert not any(s.wants_diagram for s in build_blueprint(_request(subject="History"), pattern))
    assert any(s.wants_diagram for s in build_blueprint(_request(subject="Geometry", diagram_mode="more"), pattern))
    assert not any(s.wants_diagram for s in build_blueprint(_request(subject="Geometry", diagram_mode="none"), pattern))


def test_syllabus_parser_keeps_units():
    topics = parse_syllabus_text("Unit 1: Algebra\n- Linear equations\n- Quadratic equations\nUnit 2: Geometry\n1. Triangles")
    assert [t.name for t in topics] == ["Algebra", "Linear equations", "Quadratic equations", "Geometry", "Triangles"]
    assert topics[1].unit == "Algebra" and topics[-1].unit == "Geometry"


@pytest.mark.parametrize("total,weights", [(5, [1, 1, 1]), (3, [1, 2]), (1, [1, 1, 1, 1]), (80, [0, 0])])
def test_split_units_always_sums(total, weights):
    assert abs(sum(split_units(total, weights)) - total) < 1e-9


def _slot(qtype="mcq", **kw):
    base = dict(index=0, section_id="A", question_type=qtype, marks=2, difficulty="medium", bloom="apply", topic="T")
    base.update(kw)
    return Slot(**base)


def test_normalise_rejects_bad_mcq_and_accepts_good():
    with pytest.raises(QuestionInvalid):
        normalise_question({"text": "Which is correct here?", "options": ["a", "b"], "answer": {"correct_option": 0}}, _slot(), "q1")
    q = normalise_question({"text": "Which is correct here?", "options": ["A) one", "B) two", "C) three", "D) four"],
                            "answer": {"correct_option": "B"}}, _slot(), "q1")
    assert q.options[0] == "one" and q.answer.correct_option == 1


def test_normalise_rescales_marking_scheme_to_marks():
    q = normalise_question({"text": "Explain the process of osmosis in plants.",
                            "answer": {"text": "x", "steps": [{"text": "a", "marks": 5}, {"text": "b", "marks": 5}, {"text": "c", "marks": 5}]}},
                           _slot("short", marks=3), "q1")
    assert abs(sum(s.marks for s in q.answer.steps) - 3) < 1e-9


def test_internal_choice_requires_alternative_and_bad_diagram_is_flagged():
    with pytest.raises(QuestionInvalid):
        normalise_question({"text": "Explain osmosis in plants.", "answer": {"text": "x"}}, _slot("short", internal_choice=True), "q1")
    with pytest.raises(QuestionInvalid):
        normalise_question({"text": "Find the area of the figure shown.", "answer": {"text": "x"}, "diagram": {"type": "bogus"}},
                           _slot("short", wants_diagram=True), "q1")


def test_extract_json_recovers_unescaped_latex_and_fences():
    raw = 'Sure!\n```json\n{"text": "Find $\\frac{1}{2} + \\theta$ and \\beta", "n": 1,}\n```'
    data = extract_json(raw)
    assert data["text"] == "Find $\\frac{1}{2} + \\theta$ and \\beta"
    assert extract_json('[{"a": "\\\\frac{1}{2}"}]')[0]["a"] == "\\frac{1}{2}"
    with pytest.raises(ValueError):
        extract_json("no json here")
