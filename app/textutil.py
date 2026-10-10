"""Word and sentence boundaries that work for Indic scripts as well as Latin.

Python's \\w does not match combining vowel signs, so Devanagari, Tamil, Telugu and the other scripts fall apart
into fragments with the standard pattern. Sentences end in a danda (U+0964) as often as in a full stop.
"""
from __future__ import annotations

import re
import unicodedata


def _indic_marks() -> str:
    out = []
    for cp in range(0x0900, 0x0D80):
        if unicodedata.category(chr(cp)) in ("Mn", "Mc"):
            out.append(chr(cp))
    out += ["\u200c", "\u200d"]
    return "".join(out)


_MARKS = _indic_marks()

# A word: letters or digits (any script) with Indic combining marks and joiners kept inside it.
WORD = re.compile(rf"(?:[^\W_]|[{_MARKS}])+", re.UNICODE)

SENTENCE_END = "[.!?\u0964\u0965]"
SENTENCE_START = "[A-Z0-9(\"'\u0900-\u0D7F]"
SENTENCE_SPLIT = re.compile(rf"(?<={SENTENCE_END})\s+(?={SENTENCE_START})")
