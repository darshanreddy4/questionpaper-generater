import io
import re
import zipfile

import pypdf
from docx import Document

from app.exam.generator import PaperGenerator
from app.exam.offline_engine import OfflineEngine
from app.exam.patterns import get_preset
from app.exam.schema import GenerationRequest, TopicSpec
from app.export import build_docx, build_pdf
from app.export.mathtext import latex_to_text, render_math, split_math
from app.export.variants import make_variant


def _paper(seed=11):
    req = GenerationRequest(subject="Mathematics", institution="Test <School> & Co", seed=seed, diagram_mode="more",
                            topics=[TopicSpec(name="Quadratic equations"), TopicSpec(name="Triangles"),
                                    TopicSpec(name="Statistics")])
    content, quality, _ = PaperGenerator(OfflineEngine(seed), req, get_preset("cbse-10-maths")).run()
    return content, quality


def _pdf_text(data: bytes) -> str:
    return "\n".join(p.extract_text() for p in pypdf.PdfReader(io.BytesIO(data)).pages)


def test_offline_math_paper_is_complete():
    content, quality = _paper()
    assert quality["failed"] == [] and quality["missing_answers"] == []
    assert sum(len(s.questions) for s in content.sections) == 38
    assert content.meta.total_marks == 80
    for sec in content.sections:
        for q in sec.questions:
            if q.type == "mcq":
                assert len(set(q.options)) == 4 and 0 <= q.answer.correct_option < 4
            elif q.type in ("very_short", "short", "long", "numerical"):
                assert abs(sum(s.marks for s in q.answer.steps) - q.marks) < 1e-9, q.id


def test_pdf_exports_contain_expected_structure():
    content, _ = _paper()
    q_text = _pdf_text(build_pdf(content, "question"))
    assert "Test <School> & Co" in q_text and "SECTION A" in q_text and "SECTION E" in q_text
    assert "Maximum Marks: 80" in q_text and "ANSWER" not in q_text
    a_text = _pdf_text(build_pdf(content, "answer"))
    assert "ANSWER KEY" in a_text and "OBJECTIVE ANSWER KEY" in a_text and "Marking scheme" in a_text
    assert "ANSWER" in _pdf_text(build_pdf(content, "combined"))


def test_docx_exports_open_and_embed_math_and_diagrams():
    content, _ = _paper()
    data = build_docx(content, "combined")
    doc = Document(io.BytesIO(data))
    text = "\n".join(p.text for p in doc.paragraphs)
    assert "SECTION A" in text and "Maximum Marks" in text
    media = [n for n in zipfile.ZipFile(io.BytesIO(data)).namelist() if n.startswith("word/media/")]
    assert len(media) > 10  # equations and figures are embedded
    assert 'descr="' in zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml").decode()  # LaTeX kept as alt text


def test_variants_reorder_but_keep_answer_keys_correct():
    content, _ = _paper()
    b = make_variant(content, "B", 11)
    assert b.meta.set_label == "B"
    by_id = {q.id: q for s in content.sections for q in s.questions}
    moved = 0
    for sec_a, sec_b in zip(content.sections, b.sections):
        assert sorted(q.id for q in sec_a.questions) == sorted(q.id for q in sec_b.questions)
        moved += [q.id for q in sec_a.questions] != [q.id for q in sec_b.questions]
        for q in sec_b.questions:
            orig = by_id[q.id]
            if q.type == "mcq":
                assert q.options[q.answer.correct_option] == orig.options[orig.answer.correct_option]
    assert moved


def test_math_images_and_fallbacks():
    img = render_math(r"\frac{a^2 + b^2}{2} \times \sqrt{3}")
    assert img and img.png.startswith(b"\x89PNG") and img.drop_pt > 0
    assert render_math(r"\begin{tikzpicture} 1 \end{tikzpicture}") is None  # unsupported -> caller falls back to text
    assert latex_to_text(r"\begin{pmatrix} 1 & 2 \\ 3 & 4 \end{pmatrix}") == "[1 2; 3 4]"
    assert latex_to_text(r"\frac{1}{2} x^2 \leq \sqrt{3}") == "(1)/(2) x² ≤ √(3)"
    assert [k for k, _ in split_math(r"Cost is \$5 and $x+1$ here")] == ["text", "math", "text"]


def test_hostile_text_cannot_break_pdf_markup():
    content, _ = _paper()
    content.sections[0].questions[0].text = "<b>unclosed <img src='/etc/passwd'/> & **bold** $\\badcommand{$ end"
    assert build_pdf(content, "question").startswith(b"%PDF")
    assert build_docx(content, "question").startswith(b"PK")
