"""Routes each question slot to the cheapest writer that can do it faithfully.

order of preference:   question bank  ->  code (templates / source sentences)  ->  language model

* Code writes what it can write *exactly*: parametric maths with a known answer, and factual recall items
  taken straight from the uploaded material. These cost no tokens and cannot contain an arithmetic slip.
* The model writes everything that needs judgement or fluent prose: long answers, case studies,
  application and analysis questions, hard items, and topics outside the template library.
* A slot that code fails to serve three times falls back to the model, and a teacher's regenerate
  instruction always goes to the model (code cannot follow instructions).
"""
from __future__ import annotations

import threading
from collections import Counter
from typing import Any, Dict, List

from app import languages
from app.exam.blueprint import is_math_subject
from app.exam.llm_engine import Engine, GenContext, SlotJob
from app.exam.offline_engine import OfflineEngine, math_code_ok, text_code_ok
from app.llm.base import LLMError

CODE_ATTEMPTS = 3


class CodeOnlyEngine(Engine):
    """Writes only what code can write faithfully; every other slot is left for the teacher. Spends no AI tokens."""

    max_attempts = 4
    similarity_limit = 0.9
    multilingual = False
    is_builtin = True
    missing_reason = ("Code-only writing has no template for this question. Switch to Automatic writing so AI can "
                      "write it, or edit it by hand.")

    def __init__(self, code: OfflineEngine):
        self.code = code
        self._tries: Counter = Counter()

    def write(self, ctx: GenContext, jobs: List[SlotJob], feedback: Dict[int, str]) -> Dict[int, Any]:
        for j in jobs:
            self._tries[j.slot.index] += 1
        return self.code.write_strict(ctx, jobs, {j.slot.index: self._tries[j.slot.index] for j in jobs})


class HybridEngine(Engine):
    def __init__(self, model: Engine, code: OfflineEngine):
        self.model, self.code = model, code
        self.max_attempts = model.max_attempts
        self.similarity_limit = model.similarity_limit
        self._tries: Counter = Counter()
        self._lock = threading.Lock()

    def localise(self, texts, language, meter=None):
        return self.model.localise(texts, language, meter)

    def routes_to_code(self, ctx: GenContext, job: SlotJob) -> bool:
        if ctx.prefer_model or not languages.is_english(ctx.request.language):
            return False  # templates and source sentences are English wording
        if self._tries[job.slot.index] >= CODE_ATTEMPTS:
            return False
        if is_math_subject(ctx.request.subject):
            return math_code_ok(job.slot)
        return text_code_ok(job.slot, job.sources)

    def write(self, ctx: GenContext, jobs: List[SlotJob], feedback: Dict[int, str]) -> Dict[int, Any]:
        with self._lock:
            code_jobs = [j for j in jobs if self.routes_to_code(ctx, j)]
            for j in code_jobs:
                self._tries[j.slot.index] += 1
            attempts = {j.slot.index: self._tries[j.slot.index] for j in code_jobs}
        out: Dict[int, Any] = self.code.write_strict(ctx, code_jobs, attempts) if code_jobs else {}
        model_jobs = [j for j in jobs if j.slot.index not in out]
        if not model_jobs:
            return out
        wanted = {j.slot.index for j in model_jobs}
        try:
            out.update(self.model.write(ctx, model_jobs, {i: m for i, m in feedback.items() if i in wanted}))
        except LLMError:
            if not out:
                raise
            # Keep what code already produced; the generator retries the slots the model did not deliver.
        return out
