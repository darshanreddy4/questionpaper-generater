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


@dataclass
class ChunkDraft:
    chapter: str
    text: str


def _is_heading(line: str, prev_blank: bool) -> str:
    s = line.strip()
    if not s or len(s) > 90:
        return ""
    m = _MD_HEADING.match(line)
    if m:
        return m.group(1).strip()
    if _chapter_number(line, None) is not None or _UNIT_HEADING.match(s) or _INDIC_UNIT_HEADING.match(s):
        return s
    if prev_blank:
        m = _NUMBERED_HEADING.match(s)
        if m and not s.endswith((".", ",", ";", "\u0964")) and len(s.split()) <= 12:
            return s
        # SHORT ALL-CAPS line on its own is almost always a heading in textbooks
        if s.isupper() and 2 <= len(s.split()) <= 10 and sum(c.isalpha() for c in s) >= 6:
            return s.title()
    return ""


def chunk_text(text: str, target_words: int = 320, max_words: int = 520) -> List[ChunkDraft]:
    """Split into passages that follow heading boundaries, so retrieval stays on-topic."""
    chunks: List[ChunkDraft] = []
    chapter = ""
    buf: List[str] = []
    words = 0
    prev_blank = True

    def flush() -> None:
        nonlocal buf, words
        body = "\n".join(buf).strip()
        if len(body.split()) >= 12:
            chunks.append(ChunkDraft(chapter, body))
        buf, words = [], 0

    for line in text.splitlines():
        heading = _is_heading(line, prev_blank)
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


def summarise_chapters(chunks: List[ChunkDraft]) -> List[dict]:
    order: List[str] = []
    words: dict = {}
    for c in chunks:
        if c.chapter not in words:
            order.append(c.chapter)
            words[c.chapter] = 0
        words[c.chapter] += len(c.text.split())
    return [{"title": t, "words": words[t]} for t in order if t]
