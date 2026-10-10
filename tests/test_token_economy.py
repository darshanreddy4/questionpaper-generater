import dataclasses
import json

from fastapi.testclient import TestClient

from conftest import register
from app.exam.llm_engine import GenContext, LLMEngine, SlotJob, Source
from app.exam.patterns import get_preset
from app.exam.schema import GenerationRequest, Slot
from app.ingestion.retrieval import compress_passage
from app.llm.base import LLMProvider
from app.llm.usage import ResponseCache, UsageMeter
from app.main import create_app
from app.models import UsageLog

PASSAGE = (
    "Photosynthesis is the process by which green plants prepare food using sunlight. "
    "The museum opens at nine in the morning and closes at five in the evening. "
    "Chlorophyll absorbs light energy in the chloroplast during photosynthesis. "
    "Tickets for the school trip must be bought a week in advance by every student. "
    "Stomata allow carbon dioxide to enter the leaf for photosynthesis. "
    "The cafeteria menu changes every Tuesday and includes vegetarian options. "
) * 4


class CountingModel(LLMProvider):
    name, model = "counting", "count-1"

    def __init__(self, reply='{"ok": true}', report=None):
        super().__init__(2)
        self.reply, self.calls, self.report = reply, 0, report

    def _complete(self, system, user, images, json_mode, max_tokens, temperature):
        self.calls += 1
        if self.report:
            self._report(*self.report)
        return self.reply


def _cache(settings):
    from app.db import Database
    return ResponseCache(Database(settings.database_url))


def test_compress_passage_keeps_relevant_sentences_within_budget():
    out = compress_passage(PASSAGE, "photosynthesis chlorophyll", 400)
    assert len(out) <= 400 and "Chlorophyll" in out
    assert "cafeteria" not in out and "museum" not in out
    assert compress_passage("Short text.", "x", 400) == "Short text."


def test_identical_json_request_is_served_from_cache(settings):
    model = CountingModel(report=(1200, 300, 0))
    model.cache = _cache(settings)
    meter = UsageMeter()
    a = model.complete_json("sys", "user", meter=meter, cache=True)
    b = model.complete_json("sys", "user", meter=meter, cache=True)
    assert a == b == {"ok": True} and model.calls == 1
    assert meter.calls == 1 and meter.total_tokens == 1500
    assert meter.cache_hits == 1 and meter.tokens_saved == 1500
    model.complete_json("sys", "different user prompt", meter=meter, cache=True)
    assert model.calls == 2


def test_invalid_json_is_never_cached(settings):
    model = CountingModel(reply="not json at all")
    model.cache = _cache(settings)
    for _ in range(2):
        try:
            model.complete_json("sys", "user", cache=True)
        except Exception:
            pass
    assert model.calls == 4  # each attempt makes the call plus its single repair retry; nothing replayed


def test_usage_is_estimated_when_provider_reports_none():
    model = CountingModel(reply='{"a": 1}')
    meter = UsageMeter()
    model.complete_json("s" * 400, "u" * 400, meter=meter)
    assert meter.estimated and meter.prompt_tokens >= 200


def test_prompt_contains_only_relevant_source_sentences():
    slot = Slot(index=0, section_id="A", question_type="short", marks=3, difficulty="easy", bloom="remember",
                topic="Photosynthesis")
    seen = {}

    class Spy(LLMProvider):
        name, model = "spy", "spy"

        def __init__(self):
            super().__init__(1)

        def _complete(self, system, user, images, json_mode, max_tokens, temperature):
            seen["user"], seen["system"] = user, system
            return json.dumps({"questions": []})

    req = GenerationRequest(subject="Biology", seed=1)
    job = SlotJob(slot, [Source(id="abc12345", kind="text", chapter="Ch1", text=PASSAGE)])
    LLMEngine(Spy()).write(GenContext(req, get_preset("unit-test-20")), [job], {})
    sent = seen["user"].split("<source", 1)[1]
    assert len(sent) < 1400 < len(PASSAGE)
    assert "cafeteria" not in seen["user"] and "photosynthesis" in seen["user"].lower()


def test_daily_budget_blocks_new_papers_and_usage_endpoints(settings):
    app = create_app(dataclasses.replace(settings, daily_token_budget=5))
    app.state.ctx.provider = CountingModel()  # the budget only protects the platform's own AI key
    with TestClient(app) as client:
        h = register(client)
        me = client.get("/api/auth/me", headers=h).json()
        uid = me["id"]
        with app.state.ctx.db.session() as db:
            db.add(UsageLog(user_id=uid, org_id=me["org_id"], kind="paper", prompt_tokens=10, completion_tokens=0))
        body = {"subject": "Mathematics", "pattern_id": "unit-test-20"}
        r = client.post("/api/papers", headers=h, json=body)
        assert r.status_code == 429 and "budget" in r.json()["detail"].lower()
        usage = client.get("/api/usage", headers=h).json()
        assert usage["used_today"] == 10 and usage["daily_budget"] == 5
        admin = client.get("/api/admin/usage", headers=h).json()
        assert admin["by_user"][0]["prompt_tokens"] == 10
        assert client.get("/readyz").json()["status"] == "ready"
        assert client.get("/healthz").status_code == 200
        assert client.get("/api/meta", headers=h).headers["x-request-id"]
