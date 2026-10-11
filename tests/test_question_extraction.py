"""The "use the exact questions from my source document" writer mode: pure pattern matching, zero AI."""
import time

from conftest import register

from app.ingestion.question_extract import extract_from_text

SAMPLE_TEXT = """
1. What is the powerhouse of the cell?
(a) Nucleus
(b) Mitochondria
(c) Ribosome
(d) Golgi body
Ans: (b)

2. Which gas do plants absorb from the air for photosynthesis?
(a) Oxygen
(b) Nitrogen
(c) Carbon dioxide
(d) Hydrogen
Ans: (c)

3. Which organelle is called the suicide bag of the cell?
(a) Lysosome
(b) Vacuole
(c) Plastid
(d) Centriole
Ans: (a)

4. Which of these is a vascular tissue that conducts water?
(a) Phloem
(b) Xylem
(c) Cambium
(d) Cork
Ans: (b)

5. Which pigment absorbs light energy for photosynthesis?
(a) Melanin
(b) Haemoglobin
(c) Chlorophyll
(d) Keratin
Ans: (c)

6. Explain the process of transpiration in plants. [2]

7. Describe how aerobic respiration differs from anaerobic respiration. [2]

8. Define osmosis with a suitable example. [2]

9. State two functions of the xylem tissue. [3]

10. Explain why stomata open and close during the day. [3]

11. Describe the role of mitochondria in cellular respiration. [3]
"""


def test_extract_from_text_reads_mcq_with_answer_key_and_plain_questions():
    items = extract_from_text(SAMPLE_TEXT, chapter="Biology")
    assert len(items) == 11
    mcqs = [it for it in items if it["type"] == "mcq"]
    assert len(mcqs) == 5
    first = mcqs[0]
    assert first["text"] == "What is the powerhouse of the cell?"
    assert first["options"] == ["Nucleus", "Mitochondria", "Ribosome", "Golgi body"]
    assert first["answer"]["correct_option"] == 1
    plain = [it for it in items if it["type"] == "short"]
    assert len(plain) == 6
    assert plain[0]["text"] == "Explain the process of transpiration in plants."
    assert plain[0]["_marks_hint"] == 2.0


def test_extract_from_text_keeps_undecidable_mcq_options_as_text_without_guessing_the_answer():
    text = "1. What is 2 + 2?\n(a) 3\n(b) 4\n(c) 5\n(d) 6\n"
    items = extract_from_text(text)
    assert len(items) == 1 and items[0]["type"] == "short"
    assert "(a) 3" in items[0]["text"] and "(b) 4" in items[0]["text"]
    assert items[0]["answer"]["text"] == "Refer to the source document for the model answer."


def test_extract_from_text_ignores_unnumbered_prose():
    assert extract_from_text("Just a paragraph of notes with no question numbers at all.") == []


BODY = {"subject": "Science", "pattern_id": "unit-test-20", "seed": 1, "diagram_mode": "none",
        "writer_mode": "extract_source"}


def _wait(client, pid, h):
    end = time.time() + 60
    while time.time() < end:
        p = client.get(f"/api/papers/{pid}", headers=h).json()
        if p["status"] in ("ready", "failed"):
            return p
        time.sleep(0.2)
    raise AssertionError("timeout")


def _upload(client, h, text):
    d = client.post("/api/documents", headers=h, files=[("files", ("paper.txt", text.encode(), "text/plain"))]).json()["documents"][0]
    for _ in range(100):
        if client.get(f"/api/documents/{d['id']}", headers=h).json()["status"] == "ready":
            return d["id"]
        time.sleep(0.2)
    raise AssertionError("document not ready")


def test_paper_generated_from_source_reproduces_its_questions_verbatim_with_no_ai(client):
    h = register(client)
    doc = _upload(client, h, SAMPLE_TEXT)
    body = dict(BODY, document_ids=[doc])
    plan = client.post("/api/papers/blueprint", headers=h, json=body).json()["writers"]
    assert plan["mode"] == "extract_source" and plan["extracted"] == 11 and plan["ai"] == 0
    paper = _wait(client, client.post("/api/papers", headers=h, json=body).json()["id"], h)
    assert paper["status"] == "ready"
    questions = [q for s in paper["content"]["sections"] for q in s["questions"]]
    assert all(q["origin"] == "source" for q in questions)
    assert paper["quality"]["usage"]["prompt_tokens"] == 0 and paper["quality"]["usage"]["completion_tokens"] == 0
    texts = {q["text"] for q in questions}
    assert "What is the powerhouse of the cell?" in texts
    mcq = next(q for q in questions if q["text"] == "What is the powerhouse of the cell?")
    assert mcq["options"] == ["Nucleus", "Mitochondria", "Ribosome", "Golgi body"]
    assert mcq["answer"]["correct_option"] == 1
