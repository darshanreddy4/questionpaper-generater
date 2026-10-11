"""AI-assisted chapter relabelling: a one-time paid pass whose result is reused for free afterwards."""
import re
import time

from conftest import register

from app.llm.base import LLMProvider


class FakeChapterModel(LLMProvider):
    name, model = "fake", "fake-1"

    def __init__(self, title: str):
        super().__init__(2)
        self.title = title
        self.calls = 0

    def _complete(self, system, user, images, json_mode, max_tokens, temperature):
        self.calls += 1
        n = len(re.findall(r"^\[\d+\]", user, re.M))
        return __import__("json").dumps({"titles": [self.title] * n})


MESSY_TEXT = """
PATTERNS IN

MATHEMATICS
1
1.1 What is Mathematics?
Mathematics is, in large part, the search for patterns and the explanations for why they exist.
Patterns in Mathematics.

9.  Find your own patterns or relations in and among the sequences in Table 1.
2  4  6  8  10  numbers

Patterns in Mathematics.
""" * 3


def _upload_and_wait(client, h, text):
    d = client.post("/api/documents", headers=h, files=[("files", ("messy.txt", text.encode(), "text/plain"))]).json()["documents"][0]
    for _ in range(100):
        got = client.get(f"/api/documents/{d['id']}", headers=h).json()
        if got["status"] == "ready":
            return got
        time.sleep(0.2)
    raise AssertionError("document not ready")


def test_relabelling_needs_ai_and_is_rejected_offline(client):
    h = register(client)
    doc = _upload_and_wait(client, h, MESSY_TEXT)
    r = client.post(f"/api/documents/{doc['id']}/chapters/ai", headers=h)
    assert r.status_code == 422


def test_relabelling_with_ai_overwrites_the_messy_heuristic_chapters_once(client):
    h = register(client)
    doc = _upload_and_wait(client, h, MESSY_TEXT)
    assert any("Patterns In" in c["title"] or "numbers" in c["title"] for c in doc["chapters"])  # the messy baseline

    app = client.app
    fake = FakeChapterModel("Patterns in Mathematics")
    app.state.ctx.provider = fake

    r = client.post(f"/api/documents/{doc['id']}/chapters/ai", headers=h)
    assert r.status_code == 200
    chapters = r.json()["chapters"]
    assert len(chapters) == 1 and chapters[0]["title"] == "Patterns in Mathematics"

    # saved: reading the document again returns the same clean labels without calling the model (GET never does)
    refreshed = client.get(f"/api/documents/{doc['id']}", headers=h).json()
    assert refreshed["chapters"] == chapters
