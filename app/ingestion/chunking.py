from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List

try:  # reuse the multilingual chapter-heading detector from the book-to-skill project
    from book_to_skill.utils import _chapter_number
except Exception:  # pragma: no cover - library layout changed
    def _chapter_number(line, prev_line=None):
        return None

_MD_HEADING = re.compile(r"^\s{0,3}#{1,4}\s+(.+?)\s*#*\s*$")
_NUMBERED_HEADING = re.compile(r"^\s*(?:\d{1,2}(?:\.\d{1,2}){0,2})[.)]?\s+([A-Z\u0900-\u0D7F][^\n]{2,80})$")
_UNIT_HEADING = re.compile(r"^\s*(?:unit|chapter|lesson|module|topic)\s+[\dIVXivx]+\b.*$", re.I)
# "Chapter 3" / "Unit 2" in the Indian languages; \b is unreliable next to combining marks, so look ahead instead.
_INDIC_UNIT_HEADING = re.compile(
    r"^\s*(?:अध्याय|इकाई|पाठ|घटक|অধ্যায়|ইউনিট|ਅਧਿਆਇ|ਇਕਾਈ|પ્રકરણ|એકમ|ଅଧ୍ୟାୟ|ଏକକ|பாடம்|அலகு|"
    r"అధ్యాయం|పాఠం|యూనిట్|ಅಧ್ಯಾಯ|ಪಾಠ|ಘಟಕ|അധ്യായം|പാഠം|യൂണിറ്റ്)(?=[\s\d:.\-]|$).*$")
# PDFs wrap a paragraph across several short "lines", so a numbered exercise question ("9. Find your own
# patterns...") can look exactly like a numbered heading once it is split at the page width. These are the
# tells that a candidate line is really prose, not a title, and get it rejected regardless of which pattern
# above matched it.
_EXERCISE_START = re.compile(
    r"^(find|explain|describe|state|define|write|solve|calculate|prove|show|verify|draw|construct|give|list|"
    r"name|mention|fill|choose|select|complete|match|identify|observe|evaluate|simplify|determine|derive|"
    r"discuss|compare|convert|express|estimate|classify|arrange|what|which|how|why|who|when|where|is|are|do|"
    r"does|can|will|if|a\.|b\.|c\.|d\.)\b", re.I)


def _looks_like_heading(s: str) -> bool:
    if _EXERCISE_START.match(s) or s.rstrip().endswith("?"):
        return False
    tokens = s.split()
    numeric = sum(1 for t in tokens if re.fullmatch(r"[\d,./\-]+", t))
    return not (tokens and numeric / len(tokens) > 0.4)


@dataclass
class ChunkDraft:
    chapter: str
    text: str


def _is_heading(line: str, prev_blank: bool, freq: dict = {}) -> str:
    s = line.strip()
    if not s or len(s) > 90:
        return ""
    m = _MD_HEADING.match(line)
    if m:
        return m.group(1).strip()
    if _UNIT_HEADING.match(s) or _INDIC_UNIT_HEADING.match(s):
        return s  # an explicit "Chapter/Unit N" keyword marker, not a heuristic guess that needs double-checking
    if _chapter_number(line, None) is not None:
        return s if _looks_like_heading(s) else ""
    if prev_blank:
        m = _NUMBERED_HEADING.match(s)
        if m and not s.endswith((".", ",", ";", "\u0964")) and len(s.split()) <= 12 and _looks_like_heading(m.group(1)):
            return s
        # A short ALL-CAPS line is usually a heading, but a one-off occurrence is just as often a running
        # header or title page caught mid-sentence by the PDF's own line wrapping; require it to repeat
        # elsewhere in the document (as a real running header would) before trusting it.
        if s.isupper() and 2 <= len(s.split()) <= 10 and sum(c.isalpha() for c in s) >= 6 and _looks_like_heading(s) \
                and freq.get(s, 0) >= 2:
            return s.title()
    return ""


def chunk_text(text: str, target_words: int = 320, max_words: int = 520) -> List[ChunkDraft]:
    """Split into passages that follow heading boundaries, so retrieval stays on-topic."""
    chunks: List[ChunkDraft] = []
    chapter = ""
    buf: List[str] = []
    words = 0
    prev_blank = True
    lines = text.splitlines()
    freq: dict = {}
    for ln in lines:
        s = ln.strip()
        if s:
            freq[s] = freq.get(s, 0) + 1

    def flush() -> None:
        nonlocal buf, words
        body = "\n".join(buf).strip()
        if len(body.split()) >= 12:
            chunks.append(ChunkDraft(chapter, body))
        buf, words = [], 0

    for line in lines:
        heading = _is_heading(line, prev_blank, freq)
        if heading:
            flush()
            chapter = heading
            prev_blank = False
            continue
        if not line.strip():
            prev_blank = True
            if words >= target_words:
                flush()
            else:
                buf.append("")
            continue
        prev_blank = False
        buf.append(line)
        words += len(line.split())
        if words >= max_words:
            flush()
    flush()
    return chunks


_SOLUTIONS_CHAPTER = re.compile(r"\bsolutions?\b|\banswer\s*keys?\b", re.I)


def summarise_chapters(chunks: List[ChunkDraft]) -> List[dict]:
    order: List[str] = []
    words: dict = {}
    for c in chunks:
        if c.chapter not in words:
            order.append(c.chapter)
            words[c.chapter] = 0
        words[c.chapter] += len(c.text.split())
    # A genuine "Chapter N -- Solutions" heading is real, but a poor exam topic: it is about the answer key,
    # not the subject matter, so it is left out of what gets offered as syllabus coverage.
    return [{"title": t, "words": words[t]} for t in order if t and not _SOLUTIONS_CHAPTER.search(t)]
