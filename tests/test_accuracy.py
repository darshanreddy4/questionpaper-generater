import json
import random
import re
import time

import pytest

from conftest import register
from app.diagrams import check_diagram
from app.exam.generator import PaperGenerator
from app.exam.hybrid_engine import HybridEngine
from app.exam.llm_engine import LLMEngine
from app.exam.normalize import normalise_question
from app.exam.offline_engine import _DIAGRAM_TEMPLATES, _MATH_TEMPLATES, OfflineEngine, match_templates
from app.exam.patterns import get_preset
from app.exam.schema import Answer, GenerationRequest, Question, Slot, Step, TopicSpec
from app.exam.verify import parse_latex, verify_question
from app.llm.base import LLMProvider


def _q(text, answer, steps=(), options=(), correct=None, qtype="short", marks=3):
    return Question(id="q1", type=qtype, text=text, marks=marks, options=list(options),
                    answer=Answer(text=answer, steps=[Step(text=s, marks=1) for s in steps], correct_option=correct))


# ----------------------------------------------------------------- verifier

def test_verifier_catches_an_arithmetic_slip_in_a_worked_solution():
    q = _q("Find $AC$.", "$AC = 13$", ["$AC^2 = 5^2 + 12^2 = 169$.", "$AC = \\sqrt{169} = 14$."])
    result = verify_question(q)
    assert result.status == "flagged" and "14" in " ".join(result.issues)


def test_verifier_accepts_correct_work_and_forgives_rounding():
    q = _q("Find the area.", "$12.57$ cm$^2$", ["$\\pi r^2 = 3.14 \\times 4 = 12.56$.", "$= 12.57$"])
    assert verify_question(q).issues == []
    ok = _q("Find $AC$.", "$AC = 13$", ["$5^2 + 12^2 = 169$.", "$\\sqrt{169} = 13$."])
    assert verify_question(ok).status == "checked"


def test_verifier_solves_stated_equations_independently():
    wrong = _q("Solve the equation $x^2 - 5x + 6 = 0$.", "$x = 2$ or $x = 4$")
    assert verify_question(wrong).status == "flagged"
    right = _q("Solve the equation $x^2 - 5x + 6 = 0$.", "$x = 2$ or $x = 3$")
    assert verify_question(right).status == "checked"


def test_verifier_checks_the_option_marked_correct_for_mcq():
    options = ["$x = 2$ or $x = 3$", "$x = 1$ or $x = 6$", "$x = -2$ or $x = -3$", "$x = 5$"]
    good = _q("Find the roots of $x^2 - 5x + 6 = 0$.", "", options=options, correct=0, qtype="mcq", marks=1)
    bad = _q("Find the roots of $x^2 - 5x + 6 = 0$.", "", options=options, correct=1, qtype="mcq", marks=1)
    assert verify_question(good).issues == [] and verify_question(bad).status == "flagged"


def test_verifier_never_guesses_about_what_it_cannot_parse():
    q = _q("Prove that $\\sin^2\\theta + \\cos^2\\theta = 1$.", "Identity.", ["$\\sin^2\\theta = 1 - \\cos^2\\theta$"])
    assert verify_question(q).status == ""  # nothing mechanical to check, nothing claimed


def test_expression_parser_is_not_an_eval_hole():
    for hostile in ("__import__('os').system('echo hi')", "x.__class__", "open('f')", "a" * 400, "9**9**9"):
        assert parse_latex(hostile) is None


# ----------------------------------------------------------------- templates

@pytest.mark.parametrize("name", sorted(_MATH_TEMPLATES))
def test_every_math_template_is_internally_consistent(name):
    for seed in range(40):
        rng = random.Random(seed)
        for difficulty in ("easy", "medium", "hard"):
            for diagram in (False, True):
                mq = _MATH_TEMPLATES[name](rng, difficulty, diagram and name in _DIAGRAM_TEMPLATES)
                assert mq.option not in mq.distractors
                assert len(set(mq.distractors) - {mq.option}) >= 2
                q = _q(mq.text, mq.answer, mq.steps)
                result = verify_question(q)
                assert result.issues == [], (name, mq.text, mq.steps, result.issues)
                if mq.diagram:
                    check_diagram(mq.diagram)


def test_template_matching_is_strict_about_topic():
    assert match_templates("Quadratic equations", False)
    assert "pythagoras" not in match_templates("Coordinate geometry", False)
    assert "distance" in match_templates("Coordinate geometry", True)
    assert match_templates("Poetry of the Romantic era", False) == []


# ----------------------------------------------------------------- hybrid routing

class CountingModel(LLMProvider):
    """Records which slots it is asked to write and answers them correctly."""

    name, model = "counting", "count-1"

    def __init__(self, wrong_first=False):
        super().__init__(2)
        self.slots_seen, self.wrong_first, self.calls = [], wrong_first, 0

    def _complete(self, system, user, images, json_mode, max_tokens, temperature):
        self.calls += 1
        slots = json.loads(re.search(r"SLOTS TO WRITE:\n(\[.*\])", user).group(1))
        self.slots_seen += [s["slot"] for s in slots]
        retry = "FIX THESE PROBLEMS" in user
        out = []
        for s in slots:
            wrong = self.wrong_first and not retry
            q = {"slot": s["slot"], "text": f"Hard question {s['slot']} on {s['topic']} number {s['slot'] * 7 + 3}",
                 "answer": {"text": "$AC = 13$", "steps": [
                     {"text": f"$5^2 + 12^2 = {170 if wrong else 169}$.", "marks": 1},
                     {"text": "$\\sqrt{169} = 13$.", "marks": 1}, {"text": "Hence the result.", "marks": 1}]}}
            if s["type"] == "mcq":
                q["options"] = ["one", "two", "three", "four"]
                q["answer"] = {"correct_option": 1}
            if s.get("internal_choice"):
                q["alternative"] = {"text": f"Alternative to {s['slot']} with other numbers {s['slot'] * 11}",
                                    "answer": {"text": "ok", "steps": [{"text": "ok", "marks": 3}]}}
            out.append(q)
        return json.dumps({"questions": out})


def _maths_request(**kw):
    topics = [TopicSpec(name=t) for t in ("Quadratic equations", "Arithmetic progression", "Probability",
                                         "Pythagoras theorem", "Proofs in number theory")]
    return GenerationRequest(subject="Mathematics", seed=3, topics=topics, **kw)


def test_hybrid_sends_only_what_code_cannot_write_to_the_model():
    model = CountingModel()
    engine = HybridEngine(LLMEngine(model), OfflineEngine(3))
    gen = PaperGenerator(engine, _maths_request(), get_preset("unit-test-20"), workers=1)
    content, quality, slots = gen.run()
    by_origin = quality["writers"]
    assert by_origin.get("code", 0) > 0 and by_origin.get("model", 0) > 0
    assert len(set(model.slots_seen)) < len(slots)  # the model was not asked to write every question
    coded = [q for s in content.sections for q in s.questions if q.origin == "code"]
    assert coded and all(q.verified == "construction" for q in coded)
    for q in coded:  # the slots routed to code are on topics with a real template
        assert q.topic in ("Quadratic equations", "Arithmetic progression", "Probability", "Pythagoras theorem")


def test_a_fully_templated_paper_costs_zero_model_calls():
    class Boom(LLMProvider):
        name, model = "boom", "boom"

        def __init__(self):
            super().__init__(1)

        def _complete(self, *a, **k):
            raise AssertionError("the model must not be called")

    req = GenerationRequest(subject="Mathematics", seed=2, difficulty={"easy": 60, "medium": 40, "hard": 0},
                            diagram_mode="none", topics=[TopicSpec(name="Quadratic equations")])
    engine = HybridEngine(LLMEngine(Boom()), OfflineEngine(2))
    pattern = get_preset("unit-test-20")
    content, quality, _ = PaperGenerator(engine, req, pattern, workers=1).run()
    assert quality["writers"] == {"code": 11} and not quality["failed"]


def test_wrong_answer_key_is_retried_then_fixed():
    model = CountingModel(wrong_first=True)
    req = GenerationRequest(subject="Physics", seed=1, topics=[TopicSpec(name="Optics")])
    gen = PaperGenerator(LLMEngine(model), req, get_preset("unit-test-20"), workers=1)
    content, quality, _ = gen.run()
    assert quality["verification"]["flagged"] == []
    assert quality["verification"]["counts"].get("checked", 0) > 0


def test_persistently_wrong_answer_key_is_kept_but_flagged():
    class AlwaysWrong(CountingModel):
        def _complete(self, system, user, images, json_mode, max_tokens, temperature):
            return super()._complete(system, user.replace("FIX THESE PROBLEMS", "NOTE"), images, json_mode,
                                     max_tokens, temperature)

    model = AlwaysWrong(wrong_first=True)
    req = GenerationRequest(subject="Physics", seed=1, topics=[TopicSpec(name="Optics")])
    content, quality, _ = PaperGenerator(LLMEngine(model), req, get_preset("unit-test-20"), workers=1).run()
    assert quality["verification"]["flagged"] and quality["status"] == "review"
    assert any("computer check" in w for w in quality["warnings"])


def test_teacher_instruction_always_goes_to_the_model():
    model = CountingModel()
    req = _maths_request()
    engine = HybridEngine(LLMEngine(model), OfflineEngine(3))
    gen = PaperGenerator(engine, req, get_preset("unit-test-20"), workers=1)
    content, _, _ = gen.run()
    coded = next(q for s in content.sections for q in s.questions if q.origin == "code")
    before = len(model.slots_seen)
    new_q = gen.regenerate(content, coded.id, "Make it a word problem")
    assert new_q.origin == "model" and len(model.slots_seen) == before + 1


# ----------------------------------------------------------------- bank reuse (API)

def _wait(client, pid, headers):
    end = time.time() + 60
    while time.time() < end:
        paper = client.get(f"/api/papers/{pid}", headers=headers).json()
        if paper["status"] in ("ready", "failed"):
            return paper
        time.sleep(0.2)
    raise AssertionError("timeout")


def test_second_paper_reuses_bank_questions_and_spends_nothing_on_them(client):
    h = register(client)
    body = {"subject": "Mathematics", "pattern_id": "unit-test-20", "seed": 5, "diagram_mode": "none",
            "syllabus_text": "Quadratic equations\nArithmetic progression\nProbability",
            "difficulty": {"easy": 50, "medium": 50, "hard": 0}}
    first = _wait(client, client.post("/api/papers", headers=h, json=body).json()["id"], h)
    assert first["status"] == "ready" and first["quality"]["reuse"]["from_bank"] == 0
    second_body = dict(body, seed=9, reuse_bank=True)
    second = _wait(client, client.post("/api/papers", headers=h, json=second_body).json()["id"], h)
    assert second["status"] == "ready"
    assert second["quality"]["reuse"]["from_bank"] > 0
    assert second["quality"]["writers"].get("bank", 0) == second["quality"]["reuse"]["from_bank"]
    assert not second["quality"]["bank_repeats"]  # reuse was requested, so it is not reported as a repeat
    bank = client.get("/api/bank", headers=h).json()
    items = bank.get("items") or bank.get("questions") or []
    reused_questions = [q for s in second["content"]["sections"] for q in s["questions"] if q["origin"] == "bank"]
    assert reused_questions and all(q["origin_ref"] for q in reused_questions)
    assert len(items) == len({i.get("id") for i in items})  # reused questions are not stored twice


# ----------------------------------------------------------------- equations the renderer must handle

def test_matrices_cases_and_aligned_blocks_render_as_images():
    from app.export.mathtext import render_math

    samples = [
        r"A = \begin{pmatrix} 1 & 2 \\ 3 & 4 \end{pmatrix} + B",
        r"\begin{vmatrix} 3 & -2 \\ 5 & 7 \end{vmatrix} = 31",
        r"f(x) = \begin{cases} x^2 & \text{if } x > 0 \\ -x & \text{if } x \le 0 \end{cases}",
        r"\begin{aligned} 2x + 3y &= 12 \\ x - y &= 1 \end{aligned}",
        r"\frac{-b \pm \sqrt{b^2 - 4ac}}{2a}",
    ]
    for tex in samples:
        img = render_math(tex, 12.0)
        assert img is not None and img.png.startswith(b"\x89PNG") and img.width_pt > 20, tex


def test_papers_with_matrix_questions_export_to_pdf_and_docx():
    import io

    from docx import Document
    from pypdf import PdfReader

    from app.export import build_docx, build_pdf

    req = GenerationRequest(subject="Mathematics", seed=4, diagram_mode="none", topics=[TopicSpec(name="Matrices and determinants")])
    content, quality, _ = PaperGenerator(OfflineEngine(4), req, get_preset("unit-test-20"), workers=1).run()
    assert any("vmatrix" in q.text for s in content.sections for q in s.questions)
    pdf = build_pdf(content, "combined")
    assert pdf.startswith(b"%PDF") and len(PdfReader(io.BytesIO(pdf)).pages) >= 1
    assert Document(io.BytesIO(build_docx(content, "answer"))).paragraphs
