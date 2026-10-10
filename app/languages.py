"""Languages and writing systems ExamForge can set a paper in, and helpers that tell scripts apart."""
from __future__ import annotations

import bisect
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple


@dataclass(frozen=True)
class Language:
    code: str
    name: str
    native: str
    script: str  # "latin" or a key from SCRIPTS
    locale: str  # BCP 47 tag written into DOCX so Word proofs and shapes correctly


# (first, last, script); sorted, non-overlapping
_RANGES: List[Tuple[int, int, str]] = [
    (0x0900, 0x097F, "devanagari"), (0x0980, 0x09FF, "bengali"), (0x0A00, 0x0A7F, "gurmukhi"),
    (0x0A80, 0x0AFF, "gujarati"), (0x0B00, 0x0B7F, "oriya"), (0x0B80, 0x0BFF, "tamil"),
    (0x0C00, 0x0C7F, "telugu"), (0x0C80, 0x0CFF, "kannada"), (0x0D00, 0x0D7F, "malayalam"),
    (0xA8E0, 0xA8FF, "devanagari"),
]
_STARTS = [r[0] for r in _RANGES]

SCRIPTS = {
    "devanagari": "Devanagari", "bengali": "Bengali", "gurmukhi": "Gurmukhi", "gujarati": "Gujarati",
    "oriya": "Odia", "tamil": "Tamil", "telugu": "Telugu", "kannada": "Kannada", "malayalam": "Malayalam",
}

# Any character of a supported Indic script, including its vowel signs and joiners.
INDIC_CHAR = re.compile("[\u0900-\u0D7F\uA8E0-\uA8FF]")
# The danda is shared by Devanagari, Bengali, Odia and others; it takes the script of the word before it.
_JOINERS = "\u200c\u200d\u25cc\u0964\u0965"

LANGUAGES: List[Language] = [
    Language("en", "English", "English", "latin", "en-IN"),
    Language("hi", "Hindi", "हिन्दी", "devanagari", "hi-IN"),
    Language("mr", "Marathi", "मराठी", "devanagari", "mr-IN"),
    Language("ne", "Nepali", "नेपाली", "devanagari", "ne-NP"),
    Language("sa", "Sanskrit", "संस्कृतम्", "devanagari", "sa-IN"),
    Language("bn", "Bengali", "বাংলা", "bengali", "bn-IN"),
    Language("as", "Assamese", "অসমীয়া", "bengali", "as-IN"),
    Language("pa", "Punjabi", "ਪੰਜਾਬੀ", "gurmukhi", "pa-IN"),
    Language("gu", "Gujarati", "ગુજરાતી", "gujarati", "gu-IN"),
    Language("or", "Odia", "ଓଡ଼ିଆ", "oriya", "or-IN"),
    Language("ta", "Tamil", "தமிழ்", "tamil", "ta-IN"),
    Language("te", "Telugu", "తెలుగు", "telugu", "te-IN"),
    Language("kn", "Kannada", "ಕನ್ನಡ", "kannada", "kn-IN"),
    Language("ml", "Malayalam", "മലയാളം", "malayalam", "ml-IN"),
]
_BY_KEY = {}
for _l in LANGUAGES:
    for _k in (_l.code, _l.name.lower(), _l.native.lower()):
        _BY_KEY[_k] = _l
_BY_KEY["oriya"] = _BY_KEY["or"]


def resolve(text: Optional[str]) -> Optional[Language]:
    """'Hindi', 'हिन्दी', 'hi' or 'Hindi medium' -> Hindi. Anything unrecognised (e.g. 'French') -> None."""
    t = (text or "").strip().lower()
    if not t:
        return _BY_KEY["en"]
    if t in _BY_KEY:
        return _BY_KEY[t]
    first = re.split(r"[\s,/(\-]+", t, maxsplit=1)[0]
    return _BY_KEY.get(first)


def key(text: Optional[str]) -> str:
    """Stable identifier for storing and matching a paper's language: the ISO code when we know the language."""
    lang = resolve(text)
    return lang.code if lang else (text or "").strip().lower()[:40]


def is_english(text: Optional[str]) -> bool:
    lang = resolve(text)
    return lang is not None and lang.code == "en"


def script_of_char(ch: str) -> Optional[str]:
    cp = ord(ch)
    if cp < 0x0900:
        return None
    i = bisect.bisect_right(_STARTS, cp) - 1
    if i >= 0 and _RANGES[i][0] <= cp <= _RANGES[i][1]:
        return _RANGES[i][2]
    return None


def indic_share(text: str) -> Optional[float]:
    """Share of letters that are Indic (vs Latin), or None when there are too few letters to judge."""
    indic = len(INDIC_CHAR.findall(text))
    latin = sum(1 for ch in text if ch.isascii() and ch.isalpha())
    return indic / (indic + latin) if indic + latin >= 12 else None


def has_indic(text: str) -> bool:
    return bool(text) and INDIC_CHAR.search(text) is not None


def runs(text: str) -> List[Tuple[Optional[str], str]]:
    """Split into (script, segment) pieces. Script is None for everything that is not an Indic letter, mark or digit.
    Joiners stay with the Indic run around them because conjunct and chillu forms depend on them."""
    out: List[Tuple[Optional[str], str]] = []
    cur: Optional[str] = None
    buf: List[str] = []
    for ch in text:
        s = script_of_char(ch)
        if ch in _JOINERS and cur is not None:
            s = cur
        if s != cur and buf:
            out.append((cur, "".join(buf)))
            buf = []
        cur = s
        buf.append(ch)
    if buf:
        out.append((cur, "".join(buf)))
    return out


def dominant_script(text: str) -> Optional[str]:
    """The Indic script most of the letters are in, or None when the text is mostly Latin."""
    counts = {}
    latin = 0
    for ch in text[:200_000]:
        s = script_of_char(ch)
        if s:
            counts[s] = counts.get(s, 0) + 1
        elif ch.isascii() and ch.isalpha():
            latin += 1
    if not counts:
        return None
    best = max(counts, key=counts.get)
    return best if counts[best] >= max(20, latin * 0.3) else None


def language_for_script(script: Optional[str]) -> Optional[Language]:
    """The most common language written in a script (Devanagari -> Hindi, Bengali script -> Bengali)."""
    for lang in LANGUAGES:
        if lang.script == script:
            return lang
    return None


def catalogue() -> List[dict]:
    return [{"code": l.code, "name": l.name, "native": l.native, "script": l.script} for l in LANGUAGES]
