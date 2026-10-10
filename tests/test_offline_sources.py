"""Without AI, questions must come from the uploaded material, never from unrelated built-in templates."""
import time

from conftest import BIOLOGY_TEXT, register


def _wait(client, pid, h):
    end = time.time() + 60
    while time.time() < end:
        p = client.get(f"/api/papers/{pid}", headers=h).json()
        if p["status"] in ("ready", "failed"):
            return p
        time.sleep(0.2)
    raise AssertionError("timeout")


def _upload(client, h):
    d = client.post("/api/documents", headers=h, files=[("files", ("notes.txt", BIOLOGY_TEXT.encode(), "text/plain"))]).json()["documents"][0]
    for _ in range(100):
        if client.get(f"/api/documents/{d['id']}", headers=h).json()["status"] == "ready":
            return d["id"]
        time.sleep(0.2)
    raise AssertionError("document not ready")


BODY = {"subject": "Mathematics", "pattern_id": "unit-test-20", "seed": 3, "diagram_mode": "none"}


def test_a_maths_paper_from_an_uploaded_chapter_is_built_from_that_chapter_not_from_the_template_library(client):
    h = register(client)
    doc = _upload(client, h)
    body = dict(BODY, document_ids=[doc], syllabus_text="Photosynthesis")
    plan = client.post("/api/papers/blueprint", headers=h, json=body).json()["writers"]
    assert plan["code"] > 0  # the preview counts the questions it can base on the material
    paper = _wait(client, client.post("/api/papers", headers=h, json=body).json()["id"], h)
    assert paper["status"] == "ready"
    written = [q for s in paper["content"]["sections"] for q in s["questions"] if not q["text"].startswith("[This question could not")]
    assert written
    texts = " ".join(q["text"] + " " + " ".join(q["options"]) + " " + q["answer"]["text"] for q in written).lower()
    assert "photosynthesis" in texts or "chlorophyll" in texts or "stomata" in texts  # from the notes
    assert not any("$" in q["text"] for q in written)  # none of the built-in maths templates
    assert not any(w in texts for w in ("determinant", "arithmetic progression", "simple interest", "cylinder"))
    assert any("no ai connected" in w.lower() for w in paper["quality"]["warnings"]) and paper["quality"]["status"] == "review"


def test_a_maths_paper_with_no_uploaded_material_still_uses_the_templates(client):
    h = register(client)
    paper = _wait(client, client.post("/api/papers", headers=h, json=dict(BODY, syllabus_text="Quadratic equations")).json()["id"], h)
    assert paper["status"] == "ready" and paper["quality"]["failed"] == []
    assert any("$" in q["text"] for s in paper["content"]["sections"] for q in s["questions"])
    assert not any("no ai connected" in w.lower() for w in paper["quality"]["warnings"])


def test_a_template_that_really_fits_the_topic_is_still_used_alongside_the_material(client):
    h = register(client)
    doc = _upload(client, h)
    paper = _wait(client, client.post("/api/papers", headers=h, json=dict(BODY, document_ids=[doc], syllabus_text="Quadratic equations")).json()["id"], h)
    assert paper["status"] == "ready"
    assert any("$" in q["text"] for s in paper["content"]["sections"] for q in s["questions"])
