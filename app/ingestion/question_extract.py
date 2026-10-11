"""Pulls already-written questions out of an uploaded document's own text -- no AI, nothing invented.

Used by the "use the exact questions from my source document" writer mode: a teacher who has a past paper,
worksheet or question bank as a PDF/DOCX just wants it reproduced with the same wording, numbers and (when
printed in the source) the same options and answer key, re-typeset in ExamForge's layout.

Pure pattern matching over text the ingestion pipeline already extracted. A block that cannot be bounded with
confidence (no leading question number) is left out rather than guessed at, and an MCQ whose correct option
cannot be found in the source is kept as a subjective item with its options folded into the question text,
never assigned a made-up answer.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

_QNUM = re.compile(r"^\s*(?:Q(?:uestion)?\.?\s*)?(\d{1,3})\s*[\.\):]\s+(?=\S)", re.I)
_OPT = re.compile(r"^\s*\(?([a-dA-D])\)?\s*[\.\):]\s+(.+)$")
_MARKS = re.compile(r"[\[\(]\s*(\d{1,3}(?:\.\d+)?)\s*(?:marks?|m)?\s*[\]\)]\s*$", re.I)
_ANSWER_LINE = re.compile(r"^\s*(?:Ans(?:wer)?|Sol(?:ution)?|Correct\s+answer)\s*[:.\-]\s*(.+)$", re.I)
_ANSWER_LETTER = re.compile(r"^\(?\s*([a-dA-D])\s*\)?\.?$")
MIN_QUESTION_CHARS = 8


def _parse_block(lines: List[str], chapter: str) -> Optional[Dict[str, Any]]:
    body: List[str] = [_QNUM.sub("", lines[0], count=1).strip()]
    options: List[str] = []
    answer = ""
    marks: Optional[float] = None
    for raw_ln in lines[1:]:
        ln = raw_ln.strip()
        if not ln:
            continue
        m_opt = _OPT.match(ln)
        if m_opt:
            options.append(m_opt.group(2).strip())
            continue
        m_ans = _ANSWER_LINE.match(ln)
        if m_ans:
            answer = m_ans.group(1).strip()
            continue
        body.append(ln)
    text = " ".join(x for x in body if x).strip()
    for src in [text, *options]:
        m = _MARKS.search(src)
        if m:
            marks = float(m.group(1))
            text = _MARKS.sub("", text).strip()
            break
    if len(text) < MIN_QUESTION_CHARS:
        return None

    item: Dict[str, Any] = {"topic": chapter, "_marks_hint": marks}
    correct_option: Optional[int] = None
    if len(options) >= 2:
        letter_m = _ANSWER_LETTER.match(answer) if answer else None
        if letter_m:
            idx = "ABCD".index(letter_m.group(1).upper())
            if idx < len(options):
                correct_option = idx
    if correct_option is not None:
        item.update(type="mcq", text=text, options=options[:4],
                    answer={"correct_option": correct_option, "text": f"({'abcd'[correct_option]}) {options[correct_option]}"})
    elif options:
        # No printed answer key for these options -- keep them as text rather than guess which one is right.
        labelled = " ".join(f"({c}) {o}" for c, o in zip("abcd", options))
        item.update(type="short", text=f"{text} {labelled}".strip(),
                    answer={"text": answer or "Refer to the source document for the model answer."})
    else:
        item.update(type="short", text=text,
                    answer={"text": answer or "Refer to the source document for the model answer."})
    return item


def extract_from_text(text: str, chapter: str = "") -> List[Dict[str, Any]]:
    """Splits one chunk of already-extracted document text into discrete numbered questions."""
    blocks: List[List[str]] = []
    current: List[str] = []
    for ln in text.splitlines():
        if _QNUM.match(ln):
            if current:
                blocks.append(current)
            current = [ln]
        elif current:
            current.append(ln)
    if current:
        blocks.append(current)
    out = []
    for block in blocks:
        item = _parse_block(block, chapter)
        if item:
            out.append(item)
    return out


def extract_from_chunks(chunks) -> List[Dict[str, Any]]:
    """Same as ``extract_from_text`` but over the chunk views the ingestion pipeline already produced."""
    out: List[Dict[str, Any]] = []
    for c in chunks:
        if c.kind == "text":
            out.extend(extract_from_text(c.text, c.chapter))
    return out
