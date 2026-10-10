from __future__ import annotations

import math
import re
from collections import Counter
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

_STOP = frozenset(
    "a an the and or of to in on for with by is are was were be been being as at from that this these those it its "
    "which who whom what when where how why do does did not no nor but if then than so such can could should would "
    "may might will shall have has had into over under about between also their there they them he she his her we "
    "our you your i me my".split()
)
from app.languages import INDIC_CHAR
from app.textutil import SENTENCE_END, SENTENCE_START, WORD as _WORD
_INDIC_STEM = 6
_CJK = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]")


def tokenize(text: str) -> List[str]:
    out: List[str] = []
    for w in _WORD.findall(text.lower()):
        if _CJK.search(w):
            out.extend(ch for ch in w if _CJK.match(ch))
            continue
        if INDIC_CHAR.search(w):
            # Kannada, Telugu, Tamil and the rest add case endings to the word ("ಹರಿತ್ತು" -> "ಹರಿತ್ತಿನ"), so match on
            # the first few characters, which keeps the root.
            out.append(w[:_INDIC_STEM] if len(w) > _INDIC_STEM else w)
            continue
        if w in _STOP or len(w) < 2:
            continue
        for suf in ("ing", "es", "ed", "s"):
            if len(w) > len(suf) + 3 and w.endswith(suf):
                w = w[: -len(suf)]
                break
        out.append(w)
    return out


class Retriever:
    """In-memory BM25 over source chunks. No embedding service required, and fully deterministic."""

    def __init__(self, items: Sequence, k1: float = 1.4, b: float = 0.75):
        self.items = list(items)
        self.k1, self.b = k1, b
        self._tf: List[Counter] = []
        self._len: List[int] = []
        df: Counter = Counter()
        for it in self.items:
            toks = tokenize(f"{getattr(it, 'chapter', '')} {it.text}")
            tf = Counter(toks)
            self._tf.append(tf)
            self._len.append(len(toks))
            df.update(tf.keys())
        n = max(len(self.items), 1)
        self._avg = (sum(self._len) / n) or 1.0
        self._idf: Dict[str, float] = {t: math.log(1 + (n - d + 0.5) / (d + 0.5)) for t, d in df.items()}
        self.chapters = {getattr(it, "chapter", "") for it in self.items}

    def search(self, query: str, k: int = 4, *, chapter: Optional[str] = None, kinds: Optional[Iterable[str]] = None,
               used: Optional[Counter] = None) -> List[Tuple[float, object]]:
        q = tokenize(query)
        kind_set = set(kinds) if kinds else None
        pool = range(len(self.items))
        if chapter and chapter in self.chapters:
            pool = [i for i in pool if getattr(self.items[i], "chapter", "") == chapter]
        scored: List[Tuple[float, object]] = []
        for i in pool:
            it = self.items[i]
            if kind_set and getattr(it, "kind", "text") not in kind_set:
                continue
            tf, dl = self._tf[i], self._len[i]
            score = 0.0
            for t in q:
                f = tf.get(t)
                if f:
                    score += self._idf.get(t, 0.0) * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * dl / self._avg))
            if chapter and getattr(it, "chapter", "") == chapter:
                score += 0.25  # in-chapter chunks still rank even when the title shares no words with the body
            if used:
                score /= 1 + 0.9 * used.get(it.id, 0)  # spread repeated topic slots over different passages
            if score > 0:
                scored.append((score, it))
        scored.sort(key=lambda p: -p[0])
        return scored[:k]


_SENTENCE = re.compile(rf"(?<={SENTENCE_END})\s+(?={SENTENCE_START})|\n{{2,}}")


def compress_passage(text: str, query: str, max_chars: int) -> str:
    """Keep the sentences most relevant to *query*, in their original order, within *max_chars*.

    Retrieval picks a whole ~400-word passage, but a question usually needs only a few sentences of it.
    Sending just those is the largest single token saving in the pipeline.
    """
    text = text.strip()
    if len(text) <= max_chars:
        return text
    sentences = list(dict.fromkeys(s.strip() for s in _SENTENCE.split(text) if s.strip()))
    q = set(tokenize(query))
    scored = []
    for i, s in enumerate(sentences):
        hits = len(q & set(tokenize(s)))
        # A small lead bonus keeps the opening definition, which usually frames the rest.
        scored.append((hits + (0.6 if i == 0 else 0.0) + (0.2 if hits and i < 3 else 0.0), i))
    chosen, used = set(), 0
    for score, i in sorted(scored, key=lambda p: (-p[0], p[1])):
        if score <= 0:
            break  # irrelevant sentences are never used as filler
        cost = len(sentences[i]) + 1
        if used + cost > max_chars:
            continue
        chosen.add(i)
        used += cost
    if not chosen:
        return text[:max_chars]
    return " ".join(sentences[i] for i in sorted(chosen))
